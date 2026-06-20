"""
evaluation/main.py — Evaluate the system against sample_claims.csv ground truth.

Usage:
    python evaluation/main.py                     # Run system on sample, then evaluate
    python evaluation/main.py --output-only FILE  # Evaluate a pre-existing output file

Produces per-field accuracy metrics and a confusion matrix for claim_status.
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Add parent code directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    SAMPLE_CLAIMS_PATH, SAMPLE_OUTPUT_PATH, OUTPUT_COLUMNS,
    ALLOWED_CLAIM_STATUS,
)


def load_ground_truth(csv_path: Path) -> list[dict]:
    """Load sample_claims.csv which has both input and output columns."""
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def load_predictions(csv_path: Path) -> list[dict]:
    """Load predicted output CSV."""
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(dict(row))
    return rows


def exact_match_accuracy(predictions: list[dict], ground_truth: list[dict],
                         field: str) -> tuple[float, list[int]]:
    """
    Compute exact-match accuracy for a single field.
    Returns (accuracy_float, list_of_mismatched_indices).
    """
    if len(predictions) != len(ground_truth):
        print(f"  WARNING: prediction count ({len(predictions)}) != "
              f"ground truth count ({len(ground_truth)})")

    n = min(len(predictions), len(ground_truth))
    correct = 0
    mismatches = []

    for i in range(n):
        pred_val = predictions[i].get(field, "").strip().lower()
        true_val = ground_truth[i].get(field, "").strip().lower()

        if pred_val == true_val:
            correct += 1
        else:
            mismatches.append(i)

    accuracy = correct / n if n > 0 else 0.0
    return accuracy, mismatches


def set_f1_score(predictions: list[dict], ground_truth: list[dict],
                 field: str, separator: str = ";") -> tuple[float, list[int]]:
    """
    Compute set-based F1 for semicolon-separated fields (risk_flags, supporting_image_ids).
    Returns (avg_f1, list_of_low_f1_indices).
    """
    n = min(len(predictions), len(ground_truth))
    f1_scores = []
    low_f1_indices = []

    for i in range(n):
        pred_set = {v.strip().lower() for v in predictions[i].get(field, "").split(separator) if v.strip()}
        true_set = {v.strip().lower() for v in ground_truth[i].get(field, "").split(separator) if v.strip()}

        # Handle "none" specially
        pred_set.discard("")
        true_set.discard("")

        if not pred_set and not true_set:
            f1_scores.append(1.0)
            continue

        if not pred_set or not true_set:
            f1_scores.append(0.0)
            low_f1_indices.append(i)
            continue

        tp = len(pred_set & true_set)
        precision = tp / len(pred_set) if pred_set else 0
        recall = tp / len(true_set) if true_set else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0
        f1_scores.append(f1)

        if f1 < 0.5:
            low_f1_indices.append(i)

    avg_f1 = sum(f1_scores) / len(f1_scores) if f1_scores else 0.0
    return avg_f1, low_f1_indices


def confusion_matrix(predictions: list[dict], ground_truth: list[dict],
                     field: str = "claim_status") -> dict:
    """
    Build a confusion matrix for claim_status.
    Returns dict of dicts: matrix[true_label][pred_label] = count.
    """
    labels = sorted(ALLOWED_CLAIM_STATUS)
    matrix = {true: {pred: 0 for pred in labels} for true in labels}
    n = min(len(predictions), len(ground_truth))

    for i in range(n):
        true_val = ground_truth[i].get(field, "").strip().lower()
        pred_val = predictions[i].get(field, "").strip().lower()

        if true_val in matrix and pred_val in matrix[true_val]:
            matrix[true_val][pred_val] += 1

    return matrix


def print_confusion_matrix(matrix: dict):
    """Pretty-print a confusion matrix."""
    labels = sorted(matrix.keys())
    # Abbreviate labels for display
    abbrev = {
        "supported": "sup",
        "contradicted": "con",
        "not_enough_information": "nei",
    }

    header = "            " + "  ".join(f"{abbrev.get(l, l):>5}" for l in labels)
    print(f"\n  Confusion Matrix (rows=actual, cols=predicted):")
    print(f"  {header}")
    print(f"  {'-' * len(header)}")

    for true_label in labels:
        row_vals = "  ".join(f"{matrix[true_label][pred]:>5}" for pred in labels)
        print(f"  {abbrev.get(true_label, true_label):>10}  {row_vals}")


def evaluate(predictions: list[dict], ground_truth: list[dict]):
    """Run full evaluation and print results."""
    print(f"\n{'='*60}")
    print("EVALUATION RESULTS")
    print(f"{'='*60}")
    print(f"  Predictions: {len(predictions)} rows")
    print(f"  Ground truth: {len(ground_truth)} rows")

    # ------------------------------------------------------------------
    # Exact-match fields
    # ------------------------------------------------------------------
    exact_fields = [
        "claim_status",           # Most important
        "issue_type",
        "object_part",
        "severity",
        "evidence_standard_met",
        "valid_image",
    ]

    print(f"\n  --- Exact Match Accuracy ---")
    for field in exact_fields:
        acc, mismatches = exact_match_accuracy(predictions, ground_truth, field)
        status = "[OK]" if acc >= 0.8 else "[~~]" if acc >= 0.6 else "[XX]"
        print(f"  {status} {field:>30}: {acc:.1%}  ({len(mismatches)} mismatches)")

        # Show details for mismatches in the most important field
        if field == "claim_status" and mismatches:
            for idx in mismatches[:5]:  # Show first 5
                uid = ground_truth[idx].get("user_id", "?")
                true_v = ground_truth[idx].get(field, "?")
                pred_v = predictions[idx].get(field, "?")
                print(f"      row {idx}: {uid} — expected '{true_v}', got '{pred_v}'")
            if len(mismatches) > 5:
                print(f"      ... and {len(mismatches) - 5} more")

    # ------------------------------------------------------------------
    # Set-based F1 fields
    # ------------------------------------------------------------------
    set_fields = ["risk_flags", "supporting_image_ids"]

    print(f"\n  --- Set F1 Score ---")
    for field in set_fields:
        f1, low_indices = set_f1_score(predictions, ground_truth, field)
        status = "[OK]" if f1 >= 0.7 else "[~~]" if f1 >= 0.5 else "[XX]"
        print(f"  {status} {field:>30}: {f1:.3f}  ({len(low_indices)} low-F1 rows)")

    # ------------------------------------------------------------------
    # Confusion matrix for claim_status
    # ------------------------------------------------------------------
    matrix = confusion_matrix(predictions, ground_truth)
    print_confusion_matrix(matrix)

    # ------------------------------------------------------------------
    # Overall summary
    # ------------------------------------------------------------------
    status_acc, _ = exact_match_accuracy(predictions, ground_truth, "claim_status")
    issue_acc, _ = exact_match_accuracy(predictions, ground_truth, "issue_type")
    part_acc, _ = exact_match_accuracy(predictions, ground_truth, "object_part")

    print(f"\n  --- Summary ---")
    print(f"  claim_status accuracy:  {status_acc:.1%}")
    print(f"  issue_type accuracy:    {issue_acc:.1%}")
    print(f"  object_part accuracy:   {part_acc:.1%}")

    overall = (status_acc + issue_acc + part_acc) / 3
    print(f"  Average (top-3 fields): {overall:.1%}")

    if overall >= 0.85:
        print(f"\n  EXCELLENT -- ready for submission")
    elif overall >= 0.70:
        print(f"\n  GOOD -- consider prompt tuning on mismatches")
    else:
        print(f"\n  NEEDS WORK -- review mismatches and adjust prompts/rules")

    return {
        "claim_status_accuracy": status_acc,
        "issue_type_accuracy": issue_acc,
        "object_part_accuracy": part_acc,
        "overall_avg": overall,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate evidence review system against sample ground truth"
    )
    parser.add_argument(
        "--output-only", type=str, default=None,
        help="Path to pre-existing output CSV to evaluate (skip running the system)",
    )
    args = parser.parse_args()

    # Load ground truth
    print(f"Loading ground truth from {SAMPLE_CLAIMS_PATH}...")
    ground_truth = load_ground_truth(SAMPLE_CLAIMS_PATH)
    print(f"  Loaded {len(ground_truth)} ground-truth rows")

    if args.output_only:
        # Evaluate existing output file
        pred_path = Path(args.output_only)
        print(f"\nLoading predictions from {pred_path}...")
        predictions = load_predictions(pred_path)
    else:
        # Run the system on sample data first
        print(f"\nRunning system on sample data...")
        import subprocess
        main_py = Path(__file__).resolve().parent.parent / "main.py"
        result = subprocess.run(
            [sys.executable, str(main_py), "--sample"],
            capture_output=False,
            cwd=str(main_py.parent),
        )
        if result.returncode != 0:
            print(f"ERROR: System run failed with exit code {result.returncode}")
            return 1

        print(f"\nLoading predictions from {SAMPLE_OUTPUT_PATH}...")
        predictions = load_predictions(SAMPLE_OUTPUT_PATH)

    print(f"  Loaded {len(predictions)} prediction rows")

    # Run evaluation
    metrics = evaluate(predictions, ground_truth)

    return 0


if __name__ == "__main__":
    sys.exit(main())
