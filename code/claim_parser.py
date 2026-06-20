"""
claim_parser.py — Load and parse all CSV data files for the evidence review system.

Provides helpers to:
  • Load claims (both sample and full)
  • Load user history and generate prompt-ready summaries
  • Load evidence requirements and filter by claim object
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Numeric columns in user_history.csv that should be cast to int.
_HISTORY_INT_FIELDS: set[str] = {
    "past_claim_count",
    "accept_claim",
    "manual_review_claim",
    "rejected_claim",
    "last_90_days_claim_count",
}


# ---------------------------------------------------------------------------
# Claims
# ---------------------------------------------------------------------------

def load_claims(csv_path: Path) -> list[dict]:
    """Load ``claims.csv`` or ``sample_claims.csv`` into a list of dicts.

    Each row is returned as a :class:`dict` keyed by the CSV header.
    ``sample_claims.csv`` may contain additional output columns — they are
    preserved as-is.

    Args:
        csv_path: Absolute or relative path to the claims CSV.

    Returns:
        A list of row dicts (order matches the CSV).

    Raises:
        FileNotFoundError: If *csv_path* does not exist.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Claims file not found: {csv_path}")

    claims: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Strip whitespace from keys and values
            cleaned = {k.strip(): v.strip() for k, v in row.items()}
            claims.append(cleaned)

    logger.info("Loaded %d claims from %s", len(claims), csv_path.name)
    return claims


# ---------------------------------------------------------------------------
# User History
# ---------------------------------------------------------------------------

def load_user_history(csv_path: Path) -> dict[str, dict]:
    """Load ``user_history.csv`` into a dict keyed by ``user_id``.

    Numeric columns (``past_claim_count``, ``accept_claim``,
    ``manual_review_claim``, ``rejected_claim``, ``last_90_days_claim_count``)
    are converted to ``int``.

    Args:
        csv_path: Path to the user-history CSV.

    Returns:
        ``{user_id: {column: value, …}, …}``

    Raises:
        FileNotFoundError: If *csv_path* does not exist.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"User history file not found: {csv_path}")

    history: dict[str, dict] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cleaned = {k.strip(): v.strip() for k, v in row.items()}

            # Cast numeric fields
            for field in _HISTORY_INT_FIELDS:
                if field in cleaned:
                    try:
                        cleaned[field] = int(cleaned[field])
                    except (ValueError, TypeError):
                        cleaned[field] = 0

            uid = cleaned.get("user_id", "")
            history[uid] = cleaned

    logger.info("Loaded history for %d users from %s", len(history), csv_path.name)
    return history


def get_user_history_summary(user_id: str, history: dict[str, dict]) -> dict:
    """Build a prompt-ready summary for a single user.

    Args:
        user_id: The user to look up.
        history: The full history dict returned by :func:`load_user_history`.

    Returns:
        A dict with:

        * ``'summary_text'`` – 1–2 sentence human-readable summary suitable
          for inclusion in a model prompt.
        * ``'history_flags'`` – ``list[str]`` parsed from the semicolon-separated
          ``history_flags`` column.
        * ``'has_risk'`` – ``True`` if ``'user_history_risk'`` is among the flags.
        * ``'needs_manual_review'`` – ``True`` if ``'manual_review_required'``
          is among the flags.
        * ``'raw'`` – the full raw history record (or empty dict).
    """
    record = history.get(user_id)

    if record is None:
        return {
            "summary_text": "No prior claim history on file.",
            "history_flags": [],
            "has_risk": False,
            "needs_manual_review": False,
            "raw": {},
        }

    # Parse flags
    raw_flags = record.get("history_flags", "")
    flags = [f.strip() for f in raw_flags.split(";") if f.strip()]

    # Build readable summary
    past = record.get("past_claim_count", 0)
    rejected = record.get("rejected_claim", 0)
    history_summary_text = record.get("history_summary", "")

    parts: list[str] = [
        f"User has {past} past claims ({rejected} rejected)."
    ]
    if flags:
        parts.append(f"History flags: {', '.join(flags)}.")
    if history_summary_text:
        parts.append(history_summary_text)

    return {
        "summary_text": " ".join(parts),
        "history_flags": flags,
        "has_risk": "user_history_risk" in flags,
        "needs_manual_review": "manual_review_required" in flags,
        "raw": record,
    }


# ---------------------------------------------------------------------------
# Evidence Requirements
# ---------------------------------------------------------------------------

def load_evidence_requirements(csv_path: Path) -> list[dict]:
    """Load ``evidence_requirements.csv`` as a list of dicts.

    Expected columns: ``requirement_id``, ``claim_object``, ``applies_to``,
    ``minimum_image_evidence``.

    Args:
        csv_path: Path to the evidence-requirements CSV.

    Returns:
        A list of row dicts.

    Raises:
        FileNotFoundError: If *csv_path* does not exist.
    """
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Evidence requirements file not found: {csv_path}")

    requirements: list[dict] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            cleaned = {k.strip(): v.strip() for k, v in row.items()}
            requirements.append(cleaned)

    logger.info(
        "Loaded %d evidence requirements from %s",
        len(requirements),
        csv_path.name,
    )
    return requirements


def get_relevant_requirements(
    claim_object: str,
    issue_type_hint: str | None,
    requirements: list[dict],
) -> list[str]:
    """Return the ``minimum_image_evidence`` texts relevant to a claim.

    Matching logic:
      • A requirement matches if its ``claim_object`` column equals
        *claim_object* (case-insensitive) **or** is ``'all'``.
      • Because the exact issue-type may not be known before the model call,
        we return **all** requirements whose ``claim_object`` matches plus
        all ``'all'`` requirements.  The *issue_type_hint* parameter is
        accepted for future narrowing but currently does not filter.

    Args:
        claim_object: The claim object type (e.g. ``'car'``).
        issue_type_hint: Optional issue-type string (currently unused for
            filtering).
        requirements: The full list returned by :func:`load_evidence_requirements`.

    Returns:
        A list of ``minimum_image_evidence`` text strings.
    """
    obj_lower = claim_object.strip().lower()

    matched: list[str] = []
    for req in requirements:
        req_obj = req.get("claim_object", "").strip().lower()
        if req_obj == obj_lower or req_obj == "all":
            evidence_text = req.get("minimum_image_evidence", "").strip()
            if evidence_text:
                matched.append(evidence_text)

    return matched
