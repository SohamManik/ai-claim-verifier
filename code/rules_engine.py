"""
rules_engine.py — Deterministic post-processing of model output.

Validates enums, cross-checks evidence rules, applies history flags,
handles injection detection.  This is where 'deterministic where possible' lives.
"""

from __future__ import annotations

import logging
from typing import Any

from config import (
    ALLOWED_CLAIM_OBJECTS,
    ALLOWED_CLAIM_STATUS,
    ALLOWED_ISSUE_TYPES,
    ALLOWED_OBJECT_PARTS,
    ALLOWED_RISK_FLAGS,
    ALLOWED_SEVERITY,
    CLAIM_STATUS_ALIASES,
    ISSUE_TYPE_ALIASES,
    OUTPUT_COLUMNS,
    SEVERITY_ALIASES,
)

logger = logging.getLogger(__name__)

# Quality-related risk flags that indicate problematic images.
_QUALITY_FLAGS = {
    "blurry_image",
    "cropped_or_obstructed",
    "low_light_or_glare",
    "wrong_angle",
}


# ── helpers ──────────────────────────────────────────────────────────────────


def validate_enum(
    value: str,
    allowed: set[str],
    aliases: dict[str, str],
) -> str:
    """Validate *value* against an allowed set with cascading fallbacks.

    Resolution order:
    1. Direct membership in *allowed* (case-sensitive).
    2. Lookup in *aliases* dict.
    3. Case-insensitive match against *allowed*.
    4. Case-insensitive lookup in *aliases*.
    5. Substring match — if exactly one allowed value contains *value*
       (or vice-versa) case-insensitively, return it.
    6. Return ``'unknown'``.
    """
    if not isinstance(value, str) or not value.strip():
        return "unknown"

    cleaned: str = value.strip()

    # 1. Exact match
    if cleaned in allowed:
        return cleaned

    # 2. Alias lookup (exact)
    if cleaned in aliases:
        return aliases[cleaned]

    # 3. Case-insensitive match against allowed
    lower = cleaned.lower()
    for item in allowed:
        if item.lower() == lower:
            return item

    # 4. Case-insensitive alias lookup
    aliases_lower = {k.lower(): v for k, v in aliases.items()}
    if lower in aliases_lower:
        return aliases_lower[lower]

    # 5. Substring match (exactly one hit)
    substring_hits: list[str] = []
    for item in allowed:
        if lower in item.lower() or item.lower() in lower:
            substring_hits.append(item)
    if len(substring_hits) == 1:
        return substring_hits[0]

    # 6. Last resort
    return "unknown"


def validate_object_part(part: str, claim_object: str) -> str:
    """Validate *part* belongs to the correct per-object parts list.

    If *claim_object* is ``'laptop'`` but *part* is ``'front_bumper'``
    (a car part), returns ``'unknown'``.  Uses
    :pydata:`config.ALLOWED_OBJECT_PARTS`.
    """
    if not isinstance(part, str) or not part.strip():
        return "unknown"

    cleaned = part.strip().lower()

    # Normalise claim_object
    obj = claim_object.strip().lower() if isinstance(claim_object, str) else ""
    if obj not in ALLOWED_OBJECT_PARTS:
        return "unknown"

    allowed_parts = ALLOWED_OBJECT_PARTS[obj]

    # Exact match (case-insensitive)
    for p in allowed_parts:
        if p.lower() == cleaned:
            return p

    # Substring / fuzzy — single match only
    hits = [p for p in allowed_parts if cleaned in p.lower() or p.lower() in cleaned]
    if len(hits) == 1:
        return hits[0]

    return "unknown"


# ── main entry-point ─────────────────────────────────────────────────────────


def validate_and_finalize(
    model_output: dict[str, Any] | None,
    claim_data: dict[str, Any],
) -> dict[str, str]:
    """Main rules-engine function.

    Takes *model_output* (may be ``None`` when the API failed) and enriched
    *claim_data* (from ``preprocess_claim``).

    Returns a ``dict`` whose keys match :pydata:`config.OUTPUT_COLUMNS`
    (all 14 columns, all values are strings ready for CSV writing).
    """

    # ── passthrough fields from the original claim ───────────────────────
    user_id: str = str(claim_data.get("user_id", ""))
    image_paths: str = str(claim_data.get("image_paths", ""))
    user_claim: str = str(claim_data.get("user_claim", ""))
    claim_object: str = str(claim_data.get("claim_object", "")).strip().lower()

    # Normalise claim_object to an allowed value
    if claim_object not in ALLOWED_CLAIM_OBJECTS:
        claim_object = "unknown"

    risk_flags: list[str] = []

    # ── 1. HANDLE NULL MODEL OUTPUT ──────────────────────────────────────
    if model_output is None:
        risk_flags.append("manual_review_required")
        _apply_history_flags(claim_data, risk_flags)
        _apply_injection_flags_from_claim(claim_data, risk_flags)

        return _format_output(
            user_id=user_id,
            image_paths=image_paths,
            user_claim=user_claim,
            claim_object=claim_object,
            claim_status="not_enough_information",
            justification="Model analysis unavailable; manual review required.",
            evidence_met="false",
            evidence_reason="Model analysis unavailable.",
            issue_type="unknown",
            object_part="unknown",
            severity="unknown",
            valid_image="false",
            risk_flags=risk_flags,
            supporting_image_ids="none",
        )

    # ── 2. VALIDATE ALL ENUM FIELDS ──────────────────────────────────────
    claim_status = validate_enum(
        str(model_output.get("draft_claim_status", "")),
        ALLOWED_CLAIM_STATUS,
        CLAIM_STATUS_ALIASES,
    )

    issue_type = validate_enum(
        str(model_output.get("draft_issue_type", "")),
        ALLOWED_ISSUE_TYPES,
        ISSUE_TYPE_ALIASES,
    )

    object_part = validate_object_part(
        str(model_output.get("draft_object_part", "")),
        claim_object,
    )

    severity = validate_enum(
        str(model_output.get("draft_severity", "")),
        ALLOWED_SEVERITY,
        SEVERITY_ALIASES,
    )

    # Validate individual risk flags from model — drop any that aren't allowed
    raw_risk_flags: list[str] = model_output.get("draft_risk_flags", []) or []
    for rf in raw_risk_flags:
        normalised = rf.strip().lower() if isinstance(rf, str) else ""
        if normalised in ALLOWED_RISK_FLAGS and normalised != "none":
            risk_flags.append(normalised)

    # ── 3. CROSS-VALIDATE OBJECT PART vs CLAIM OBJECT ────────────────────
    if object_part != "unknown" and claim_object in ALLOWED_OBJECT_PARTS:
        if object_part not in ALLOWED_OBJECT_PARTS[claim_object]:
            logger.warning(
                "Object part '%s' invalid for claim_object '%s' — resetting to unknown",
                object_part,
                claim_object,
            )
            object_part = "unknown"
            if "wrong_object_part" not in risk_flags:
                risk_flags.append("wrong_object_part")

    # ── 4. CHECK EVIDENCE REQUIREMENTS ───────────────────────────────────
    draft_evidence_met: bool = bool(model_output.get("draft_evidence_met", False))
    evidence_reason: str = str(model_output.get("draft_evidence_reason", ""))

    per_image: list[dict[str, Any]] = model_output.get("per_image", []) or []

    # If every image has quality flags and model said evidence_met → override
    if per_image and draft_evidence_met:
        all_have_quality_issues = all(
            bool(img.get("quality_flags"))
            for img in per_image
        )
        if all_have_quality_issues:
            draft_evidence_met = False
            evidence_reason = (
                "Overridden: all images have quality issues "
                f"({evidence_reason})"
            )
            logger.info("Evidence-met overridden to False — all images have quality flags")

    # If evidence not met AND model had low confidence -> downgrade to NEI
    # But if model was confident in its assessment, trust it even if evidence_met is false.
    model_confidence = model_output.get("confidence", 0.5)
    if not draft_evidence_met and claim_status == "supported" and model_confidence < 0.6:
        claim_status = "not_enough_information"
        evidence_reason = evidence_reason or "Evidence standard not met."

    # ── 5. APPLY USER HISTORY RISK FLAGS ─────────────────────────────────
    _apply_history_flags(claim_data, risk_flags)

    # ── 6. APPLY INJECTION FLAGS ─────────────────────────────────────────
    _apply_injection_flags_from_claim(claim_data, risk_flags)

    # Model-side injection detection
    if model_output.get("text_instruction_detected", False):
        if "text_instruction_present" not in risk_flags:
            risk_flags.append("text_instruction_present")
        if "manual_review_required" not in risk_flags:
            risk_flags.append("manual_review_required")

    # ── 7. CONSISTENCY CHECKS ────────────────────────────────────────────
    if issue_type == "none":
        severity = "none"

    # NEI severity: only force unknown if issue_type is also unknown
    # (otherwise the model had a definite damage assessment)
    if claim_status == "not_enough_information" and issue_type == "unknown":
        severity = "unknown"

    # valid_image: false if all per_image entries have is_original=False
    valid_image = "true"
    if per_image:
        if all(not img.get("is_original", True) for img in per_image):
            valid_image = "false"
    else:
        # No per_image data → can't confirm any image is valid
        valid_image = "false"

    # supporting_image_ids
    raw_supporting: list[str] = model_output.get("draft_supporting_image_ids", []) or []
    supporting_image_ids = [
        sid.strip() for sid in raw_supporting if isinstance(sid, str) and sid.strip()
    ]

    if claim_status == "not_enough_information":
        supporting_image_ids = []

    justification: str = str(model_output.get("draft_justification", ""))

    # ── 8. FORMAT FINAL OUTPUT ───────────────────────────────────────────
    evidence_met_str = "true" if draft_evidence_met else "false"

    return _format_output(
        user_id=user_id,
        image_paths=image_paths,
        user_claim=user_claim,
        claim_object=claim_object,
        claim_status=claim_status,
        justification=justification,
        evidence_met=evidence_met_str,
        evidence_reason=evidence_reason,
        issue_type=issue_type,
        object_part=object_part,
        severity=severity,
        valid_image=valid_image,
        risk_flags=risk_flags,
        supporting_image_ids=";".join(supporting_image_ids) if supporting_image_ids else "none",
    )


# ── gate-skip helper ─────────────────────────────────────────────────────────


def build_gate_skip_output(claim_data: dict[str, Any], reason: str) -> dict[str, str]:
    """Build a complete output row for claims skipped by guard gates.

    Used when all images failed to load (no API call was made).

    Sets safe defaults with ``claim_status='not_enough_information'`` and
    attaches any applicable user-history risk flags.
    """
    user_id = str(claim_data.get("user_id", ""))
    image_paths = str(claim_data.get("image_paths", ""))
    user_claim = str(claim_data.get("user_claim", ""))
    claim_object = str(claim_data.get("claim_object", "")).strip().lower()
    if claim_object not in ALLOWED_CLAIM_OBJECTS:
        claim_object = "unknown"

    risk_flags: list[str] = []
    _apply_history_flags(claim_data, risk_flags)
    _apply_injection_flags_from_claim(claim_data, risk_flags)
    if "manual_review_required" not in risk_flags:
        risk_flags.append("manual_review_required")

    return _format_output(
        user_id=user_id,
        image_paths=image_paths,
        user_claim=user_claim,
        claim_object=claim_object,
        claim_status="not_enough_information",
        justification=reason,
        evidence_met="false",
        evidence_reason=reason,
        issue_type="unknown",
        object_part="unknown",
        severity="unknown",
        valid_image="false",
        risk_flags=risk_flags,
        supporting_image_ids="none",
    )


# ── private helpers ──────────────────────────────────────────────────────────


def _apply_history_flags(
    claim_data: dict[str, Any],
    risk_flags: list[str],
) -> None:
    """Append user-history risk flags in-place.

    History flags are **additive only** — they never change ``claim_status``.
    """
    history_info: dict[str, Any] = claim_data.get("history_info", {}) or {}

    if history_info.get("has_risk", False):
        if "user_history_risk" not in risk_flags:
            risk_flags.append("user_history_risk")

    if history_info.get("needs_manual_review", False):
        if "manual_review_required" not in risk_flags:
            risk_flags.append("manual_review_required")


def _apply_injection_flags_from_claim(
    claim_data: dict[str, Any],
    risk_flags: list[str],
) -> None:
    """Append injection-detection risk flags from pre-processed claim data.

    Injection flags are **additive only** — they never change ``claim_status``.
    """
    injection_info: dict[str, Any] = claim_data.get("injection_info", {}) or {}

    if injection_info.get("detected", False):
        if "text_instruction_present" not in risk_flags:
            risk_flags.append("text_instruction_present")
        if "manual_review_required" not in risk_flags:
            risk_flags.append("manual_review_required")


def _format_output(
    *,
    user_id: str,
    image_paths: str,
    user_claim: str,
    claim_object: str,
    claim_status: str,
    justification: str,
    evidence_met: str,
    evidence_reason: str,
    issue_type: str,
    object_part: str,
    severity: str,
    valid_image: str,
    risk_flags: list[str],
    supporting_image_ids: str,
) -> dict[str, str]:
    """Assemble the final 14-column output dict.

    All values are strings.  ``risk_flags`` list is joined with ``';'``.
    """
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped_flags: list[str] = []
    for flag in risk_flags:
        if flag not in seen:
            seen.add(flag)
            deduped_flags.append(flag)

    flags_str = ";".join(deduped_flags) if deduped_flags else "none"

    result: dict[str, str] = {
        "user_id": user_id,
        "image_paths": image_paths,
        "user_claim": user_claim,
        "claim_object": claim_object,
        "evidence_standard_met": evidence_met,
        "evidence_standard_met_reason": evidence_reason,
        "risk_flags": flags_str,
        "issue_type": issue_type,
        "object_part": object_part,
        "claim_status": claim_status,
        "claim_status_justification": justification,
        "supporting_image_ids": supporting_image_ids,
        "valid_image": valid_image,
        "severity": severity,
    }

    # Sanity check: ensure we have exactly the right keys
    expected_keys = set(OUTPUT_COLUMNS)
    actual_keys = set(result.keys())
    if actual_keys != expected_keys:
        missing = expected_keys - actual_keys
        extra = actual_keys - expected_keys
        logger.error(
            "Output column mismatch! missing=%s extra=%s", missing, extra
        )

    return result
