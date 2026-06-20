"""
output_writer.py — Write the final output CSV in the exact format required.

Handles quoting, column ordering, encoding, and pre-write validation.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from config import (
    ALLOWED_CLAIM_OBJECTS,
    ALLOWED_CLAIM_STATUS,
    ALLOWED_ISSUE_TYPES,
    ALLOWED_OBJECT_PARTS,
    ALLOWED_RISK_FLAGS,
    ALLOWED_SEVERITY,
    OUTPUT_COLUMNS,
)

logger = logging.getLogger(__name__)


def write_output_csv(results: list[dict[str, str]], output_path: Path) -> None:
    """Write *results* to a CSV file at *output_path*.

    - Uses :pydata:`csv.QUOTE_ALL` so every field is double-quoted,
      matching the input CSV format.
    - Opens with ``newline=''`` and ``encoding='utf-8'`` to prevent
      double line-endings on Windows.
    - Explicitly sets ``lineterminator='\\n'`` (Unix-style) as required
      by the problem statement.
    - Column order is exactly :pydata:`config.OUTPUT_COLUMNS`.
    """
    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, mode="w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(
            fh,
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )

        # Header
        writer.writerow(OUTPUT_COLUMNS)

        # Data rows — maintain strict column order
        for row in results:
            writer.writerow([str(row.get(col, "")) for col in OUTPUT_COLUMNS])

    logger.info("Wrote %d result row(s) to %s", len(results), output_path)


def validate_output(results: list[dict[str, str]]) -> list[str]:
    """Run sanity checks on *results* before writing.

    Returns a list of human-readable warning strings.
    An empty list means all checks passed.

    Checks performed:
        1. Every result contains all 14 columns from ``OUTPUT_COLUMNS``.
        2. No ``None`` values in any field.
        3. ``claim_status`` ∈ ``ALLOWED_CLAIM_STATUS``.
        4. ``issue_type`` ∈ ``ALLOWED_ISSUE_TYPES``.
        5. ``severity`` ∈ ``ALLOWED_SEVERITY``.
        6. ``evidence_standard_met`` ∈ ``{'true', 'false'}``.
        7. ``valid_image`` ∈ ``{'true', 'false'}``.
        8. ``claim_object`` ∈ ``ALLOWED_CLAIM_OBJECTS``.
        9. ``object_part`` ∈ ``ALLOWED_OBJECT_PARTS[claim_object]``.
       10. Each flag in ``risk_flags`` (split by ``';'``) ∈ ``ALLOWED_RISK_FLAGS``.
       11. ``supporting_image_ids`` is not the empty string.
    """
    warnings: list[str] = []

    for idx, row in enumerate(results):
        prefix = f"Row {idx}"

        # 1. Missing columns
        missing_cols = [c for c in OUTPUT_COLUMNS if c not in row]
        if missing_cols:
            warnings.append(f"{prefix}: missing columns {missing_cols}")

        # 2. None values
        none_cols = [c for c in OUTPUT_COLUMNS if c in row and row[c] is None]
        if none_cols:
            warnings.append(f"{prefix}: None values in {none_cols}")

        # Skip further field-level checks if we can't trust the row structure
        if missing_cols:
            continue

        # 3. claim_status
        if row["claim_status"] not in ALLOWED_CLAIM_STATUS:
            warnings.append(
                f"{prefix}: invalid claim_status '{row['claim_status']}'"
            )

        # 4. issue_type
        if row["issue_type"] not in ALLOWED_ISSUE_TYPES:
            warnings.append(
                f"{prefix}: invalid issue_type '{row['issue_type']}'"
            )

        # 5. severity
        if row["severity"] not in ALLOWED_SEVERITY:
            warnings.append(
                f"{prefix}: invalid severity '{row['severity']}'"
            )

        # 6. evidence_standard_met
        if row["evidence_standard_met"] not in {"true", "false"}:
            warnings.append(
                f"{prefix}: evidence_standard_met must be 'true' or 'false', "
                f"got '{row['evidence_standard_met']}'"
            )

        # 7. valid_image
        if row["valid_image"] not in {"true", "false"}:
            warnings.append(
                f"{prefix}: valid_image must be 'true' or 'false', "
                f"got '{row['valid_image']}'"
            )

        # 8. claim_object
        claim_object = row["claim_object"]
        if claim_object not in ALLOWED_CLAIM_OBJECTS:
            warnings.append(
                f"{prefix}: invalid claim_object '{claim_object}'"
            )

        # 9. object_part valid for given claim_object
        object_part = row["object_part"]
        if claim_object in ALLOWED_OBJECT_PARTS:
            if object_part not in ALLOWED_OBJECT_PARTS[claim_object]:
                warnings.append(
                    f"{prefix}: object_part '{object_part}' not valid for "
                    f"claim_object '{claim_object}'"
                )

        # 10. risk_flags — each semicolon-delimited flag must be allowed
        risk_flags_raw = row.get("risk_flags", "")
        if risk_flags_raw:
            for flag in risk_flags_raw.split(";"):
                flag = flag.strip()
                if flag and flag not in ALLOWED_RISK_FLAGS:
                    warnings.append(
                        f"{prefix}: invalid risk_flag '{flag}'"
                    )

        # 11. supporting_image_ids must not be empty string
        if row.get("supporting_image_ids", "") == "":
            warnings.append(
                f"{prefix}: supporting_image_ids is empty string "
                "(should be 'none' if no supporting images)"
            )

    return warnings
