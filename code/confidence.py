"""
confidence.py — Compute final confidence scores from model output + rules engine signals.

Blends the model's self-reported confidence with deterministic adjustments
derived from image quality, risk flags, and injection detection.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── routing thresholds ───────────────────────────────────────────────────────
_AUTO_THRESHOLD = 0.85
_MANUAL_THRESHOLD = 0.50

# ── quality flags that trigger per-type penalties ────────────────────────────
_QUALITY_FLAG_TYPES = {
    "blurry_image",
    "cropped_or_obstructed",
    "low_light_or_glare",
    "wrong_angle",
}


def compute_confidence(
    model_output: dict[str, Any] | None,
    rules_engine_result: dict[str, str],
    claim_data: dict[str, Any],
) -> dict[str, Any]:
    """Compute a final confidence score and routing recommendation.

    Args:
        model_output: Raw model output dict (may be ``None`` if API failed).
        rules_engine_result: Finalized output dict from
            :func:`rules_engine.validate_and_finalize`.
        claim_data: Enriched claim data from ``preprocess_claim``.

    Returns:
        A dict with three keys:

        - ``'score'``: ``float`` in ``[0.0, 1.0]``.
        - ``'routing'``: one of ``'auto_approve_candidate'``,
          ``'auto_deny_candidate'``, ``'manual_review_recommended'``,
          ``'escalate_to_senior'``.
        - ``'reasoning'``: Short human-readable explanation.
    """

    reasoning_parts: list[str] = []

    # ── 1. Base confidence ───────────────────────────────────────────────
    if model_output is not None:
        raw_conf = model_output.get("confidence", 0.5)
        try:
            score = float(raw_conf)
        except (TypeError, ValueError):
            score = 0.5
            reasoning_parts.append("Model confidence unparseable, defaulting to 0.5")
        else:
            reasoning_parts.append(f"Base model confidence: {score:.2f}")
    else:
        score = 0.2
        reasoning_parts.append("No model output — base confidence 0.20")

    # ── 2. Adjustments ───────────────────────────────────────────────────

    # +0.1 if all per_image analyses agree on object and damage type
    if model_output is not None:
        per_image: list[dict[str, Any]] = model_output.get("per_image", []) or []
        if len(per_image) > 1:
            objects_seen = {
                str(img.get("object_visible", "")).strip().lower()
                for img in per_image
            }
            damages_seen = {
                str(img.get("damage_observed", "")).strip().lower()
                for img in per_image
            }
            if len(objects_seen) == 1 and len(damages_seen) == 1:
                score += 0.1
                reasoning_parts.append("+0.10: all images agree on object and damage")
        elif len(per_image) == 1:
            # Single image: agreement is trivial — no bonus, but also no penalty yet
            pass

        # +0.1 if no quality flags on any image
        if per_image:
            any_quality_flags = any(
                bool(img.get("quality_flags"))
                for img in per_image
            )
            if not any_quality_flags:
                score += 0.1
                reasoning_parts.append("+0.10: no quality flags on any image")

            # -0.15 for each quality flag TYPE present across all images
            quality_types_present: set[str] = set()
            for img in per_image:
                for flag in (img.get("quality_flags") or []):
                    flag_lower = flag.strip().lower() if isinstance(flag, str) else ""
                    if flag_lower in _QUALITY_FLAG_TYPES:
                        quality_types_present.add(flag_lower)

            if quality_types_present:
                penalty = 0.15 * len(quality_types_present)
                score -= penalty
                reasoning_parts.append(
                    f"-{penalty:.2f}: {len(quality_types_present)} quality flag type(s) "
                    f"present ({', '.join(sorted(quality_types_present))})"
                )

    # Parse risk_flags from the rules-engine result
    risk_flags_str: str = rules_engine_result.get("risk_flags", "none")
    active_risk_flags: set[str] = set()
    if risk_flags_str and risk_flags_str != "none":
        active_risk_flags = {f.strip() for f in risk_flags_str.split(";")}

    # -0.3 if wrong_object or claim_mismatch in risk_flags
    severe_flags = active_risk_flags & {"wrong_object", "claim_mismatch"}
    if severe_flags:
        score -= 0.3
        reasoning_parts.append(
            f"-0.30: severe risk flag(s): {', '.join(sorted(severe_flags))}"
        )

    # -0.2 if text_instruction_present in risk_flags
    if "text_instruction_present" in active_risk_flags:
        score -= 0.2
        reasoning_parts.append("-0.20: text instruction detected in claim text")

    # -0.1 if only one image provided (single-evidence penalty)
    if model_output is not None:
        per_image_list = model_output.get("per_image", []) or []
        if len(per_image_list) == 1:
            score -= 0.1
            reasoning_parts.append("-0.10: only one image as evidence")

    # -0.1 if injection_info detected in claim text
    injection_info: dict[str, Any] = claim_data.get("injection_info", {}) or {}
    if injection_info.get("detected", False):
        score -= 0.1
        reasoning_parts.append("-0.10: injection pattern detected in claim text")

    # ── 3. Clamp ─────────────────────────────────────────────────────────
    score = max(0.0, min(1.0, score))

    # ── 4. Routing ───────────────────────────────────────────────────────
    claim_status: str = rules_engine_result.get("claim_status", "")

    # Check for manual_review_required override
    force_manual = "manual_review_required" in active_risk_flags

    if force_manual:
        routing = "manual_review_recommended"
        reasoning_parts.append(
            "Routing override: manual_review_required flag present"
        )
    elif (
        score >= _AUTO_THRESHOLD
        and claim_status == "supported"
        and (not active_risk_flags or active_risk_flags == {"none"})
    ):
        routing = "auto_approve_candidate"
        reasoning_parts.append("High confidence supported claim with no risk flags")
    elif score >= _AUTO_THRESHOLD and claim_status == "contradicted":
        routing = "auto_deny_candidate"
        reasoning_parts.append("High confidence contradicted claim")
    elif score >= _MANUAL_THRESHOLD:
        routing = "manual_review_recommended"
        reasoning_parts.append(
            f"Moderate confidence ({score:.2f}) — manual review recommended"
        )
    else:
        routing = "escalate_to_senior"
        reasoning_parts.append(
            f"Low confidence ({score:.2f}) — escalation to senior reviewer"
        )

    return {
        "score": round(score, 4),
        "routing": routing,
        "reasoning": "; ".join(reasoning_parts),
    }
