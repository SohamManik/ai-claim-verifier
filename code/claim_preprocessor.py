"""
claim_preprocessor.py — Pre-model-call analysis for insurance claims.

Performs three enrichment steps *before* the model is invoked:
  1. **Prompt-injection detection** — flags suspicious text in user claims.
  2. **Multi-claim detection** — identifies when a single row contains
     multiple distinct damage claims by looking for keyword connectors
     and multiple part names from config.ALLOWED_OBJECT_PARTS.
  3. **Claim enrichment** — bundles injection info, multi-claim info,
     user-history summary, evidence requirements, and image IDs onto the
     claim dict so downstream code has everything it needs.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from config import ALLOWED_OBJECT_PARTS, INJECTION_PATTERNS
from claim_parser import get_relevant_requirements, get_user_history_summary

logger = logging.getLogger(__name__)

# Connectors that commonly separate two sub-claims in natural language.
_MULTI_CLAIM_CONNECTORS: re.Pattern = re.compile(
    r"\b(?:and\s+also|also|and|plus|both|two\s+things)\b"
    r"|(?:\.\s+(?:Second|Also|Plus|Additionally))",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Part-name matching helpers
# ---------------------------------------------------------------------------

def _normalise_for_matching(text: str) -> str:
    """Lower-case and replace underscores with spaces for fuzzy part matching."""
    return text.lower().replace("_", " ")


def _build_part_patterns(claim_object: str) -> list[tuple[str, re.Pattern]]:
    """Return compiled regex patterns for every known part of *claim_object*.

    Each pattern matches the part name as a whole word (with underscores
    replaced by optional whitespace or underscores).  The returned list is
    sorted longest-first so greedy matching prefers more-specific parts
    (e.g. ``front_bumper`` before ``bumper``).
    """
    parts = ALLOWED_OBJECT_PARTS.get(claim_object.lower(), set())

    patterns: list[tuple[str, re.Pattern]] = []
    for part in parts:
        if part == "unknown":
            continue
        # Allow "front bumper" or "front_bumper" in the claim text
        escaped = re.escape(part).replace(r"\_", r"[\s_]")
        pat = re.compile(rf"\b{escaped}\b", re.IGNORECASE)
        patterns.append((part, pat))

    # Sort longest first for greedy matching
    patterns.sort(key=lambda t: len(t[0]), reverse=True)
    return patterns


def _find_mentioned_parts(text: str, claim_object: str) -> list[str]:
    """Return deduplicated list of canonical part names found in *text*."""
    patterns = _build_part_patterns(claim_object)
    seen: set[str] = set()
    result: list[str] = []

    for part_name, pat in patterns:
        if part_name in seen:
            continue
        if pat.search(text):
            seen.add(part_name)
            result.append(part_name)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_injection(user_claim: str) -> dict:
    """Scan *user_claim* for prompt-injection patterns.

    Uses the pre-compiled ``INJECTION_PATTERNS`` from :mod:`config`.

    Args:
        user_claim: The raw claim text submitted by the user.

    Returns:
        A dict with:

        * ``'detected'`` – ``True`` if at least one pattern matched.
        * ``'matched_patterns'`` – list of matched text snippets.
    """
    matched: list[str] = []

    for pattern in INJECTION_PATTERNS:
        match = pattern.search(user_claim)
        if match:
            matched.append(match.group())

    detected = len(matched) > 0
    if detected:
        logger.warning(
            "Prompt-injection detected in claim text. Matches: %s", matched
        )

    return {
        "detected": detected,
        "matched_patterns": matched,
    }


def detect_multi_claim(user_claim: str, claim_object: str) -> dict:
    """Detect whether *user_claim* describes multiple distinct damage claims.

    Detection strategy (keyword / part-name based, not model-based):
      1. Find all known part names for *claim_object* that appear in the text.
      2. If **two or more distinct** parts are mentioned, treat it as a
         multi-claim.
      3. If fewer than two parts are found, look for connector keywords
         (``and``, ``also``, ``plus``, ``both``, ``two things``, sentence-
         level ``Second``, etc.) as a secondary signal combined with at
         least one part mention.

    When a multi-claim is detected the text is split on the first connector
    and each segment is paired with its mentioned part(s).

    Args:
        user_claim: The raw claim text.
        claim_object: One of ``'car'``, ``'laptop'``, ``'package'``.

    Returns:
        A dict with:

        * ``'is_multi'`` – ``True`` if multiple sub-claims detected.
        * ``'sub_claims'`` – list of dicts, each with ``'part'`` and
          ``'description'`` keys.  If not multi, a single sub-claim is
          returned covering the whole text.
    """
    mentioned_parts = _find_mentioned_parts(user_claim, claim_object)
    has_connector = bool(_MULTI_CLAIM_CONNECTORS.search(user_claim))

    # Primary signal: two or more distinct parts in the text
    is_multi = len(mentioned_parts) >= 2

    # Secondary: connector present and at least one known part
    if not is_multi and has_connector and len(mentioned_parts) >= 1:
        # Heuristic: connector + at least one part likely means a second
        # damage description even if the second part name isn't in our dict.
        # Split on the connector and see if both halves have substance.
        split_match = _MULTI_CLAIM_CONNECTORS.search(user_claim)
        if split_match:
            before = user_claim[: split_match.start()].strip()
            after = user_claim[split_match.end() :].strip()
            # Only treat as multi if both halves have meaningful content
            if len(before) > 5 and len(after) > 5:
                is_multi = True

    if is_multi:
        sub_claims = _split_into_sub_claims(user_claim, claim_object, mentioned_parts)
    else:
        # Single claim — use the first mentioned part or 'unknown'
        part = mentioned_parts[0] if mentioned_parts else "unknown"
        sub_claims = [{"part": part, "description": user_claim.strip()}]

    return {
        "is_multi": is_multi,
        "sub_claims": sub_claims,
    }


def _split_into_sub_claims(
    user_claim: str,
    claim_object: str,
    mentioned_parts: list[str],
) -> list[dict]:
    """Split a multi-claim string into individual sub-claim dicts.

    Splits on the first connector match.  Each resulting segment is
    associated with the part name(s) found within it.
    """
    split_match = _MULTI_CLAIM_CONNECTORS.search(user_claim)

    if split_match:
        segments = [
            user_claim[: split_match.start()].strip(),
            user_claim[split_match.end() :].strip(),
        ]
    else:
        # Fallback: can't find a clean split point — treat whole text as
        # two sub-claims keyed by different parts.
        segments = [user_claim.strip()]

    sub_claims: list[dict] = []
    assigned_parts: set[str] = set()

    for segment in segments:
        seg_parts = _find_mentioned_parts(segment, claim_object)
        # Pick the first un-assigned part found in this segment
        chosen = "unknown"
        for p in seg_parts:
            if p not in assigned_parts:
                chosen = p
                break
        assigned_parts.add(chosen)
        sub_claims.append({"part": chosen, "description": segment})

    # If we only got one segment but had multiple parts, create entries
    # for remaining parts too.
    if len(sub_claims) == 1 and len(mentioned_parts) > 1:
        for part in mentioned_parts:
            if part not in assigned_parts:
                sub_claims.append({"part": part, "description": user_claim.strip()})
                assigned_parts.add(part)

    return sub_claims


# ---------------------------------------------------------------------------
# Top-level enrichment
# ---------------------------------------------------------------------------

def preprocess_claim(
    claim: dict,
    history: dict[str, dict],
    requirements: list[dict],
) -> dict:
    """Enrich a raw claim dict with all pre-model analysis.

    Adds the following keys to a **copy** of the claim:

    * ``'injection_info'`` — result of :func:`detect_injection`
    * ``'multi_claim_info'`` — result of :func:`detect_multi_claim`
    * ``'history_info'`` — result of
      :func:`~claim_parser.get_user_history_summary`
    * ``'evidence_texts'`` — list of relevant requirement texts from
      :func:`~claim_parser.get_relevant_requirements`
    * ``'image_ids'`` — list of image IDs (filename without extension)
      extracted from the ``image_paths`` field.

    Args:
        claim: A single row dict from :func:`~claim_parser.load_claims`.
        history: Full user-history dict from
            :func:`~claim_parser.load_user_history`.
        requirements: Full requirements list from
            :func:`~claim_parser.load_evidence_requirements`.

    Returns:
        An enriched copy of the claim dict.
    """
    enriched = dict(claim)  # shallow copy — avoids mutating the original

    user_claim = claim.get("user_claim", "")
    claim_object = claim.get("claim_object", "")
    user_id = claim.get("user_id", "")
    image_paths_str = claim.get("image_paths", "")

    # 1. Injection detection
    enriched["injection_info"] = detect_injection(user_claim)

    # 2. Multi-claim detection
    enriched["multi_claim_info"] = detect_multi_claim(user_claim, claim_object)

    # 3. User history summary
    enriched["history_info"] = get_user_history_summary(user_id, history)

    # 4. Evidence requirements
    enriched["evidence_texts"] = get_relevant_requirements(
        claim_object, None, requirements
    )

    # 5. Image IDs from paths
    raw_paths = [p.strip() for p in image_paths_str.split(";") if p.strip()]
    enriched["image_ids"] = [Path(p).stem for p in raw_paths]

    return enriched
