"""
main.py — Entry point for the Multi-Modal Evidence Review system.

Reads dataset/claims.csv (or sample_claims.csv), processes each claim through
the full pipeline, and writes output.csv.

Usage:
    python main.py                          # Process claims.csv → output.csv
    python main.py --sample                 # Process sample_claims.csv → sample_output.csv
    python main.py --sample --dry-run       # Dry run with mock model (no API calls)
"""

import argparse
import sys
import time
from pathlib import Path

# Add code directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (
    CLAIMS_PATH, SAMPLE_CLAIMS_PATH, OUTPUT_PATH, SAMPLE_OUTPUT_PATH,
    DATASET_DIR, USER_HISTORY_PATH, EVIDENCE_REQ_PATH, OUTPUT_COLUMNS,
)
from image_loader import load_and_validate_images
from prompt_builder import build_system_prompt, build_user_content
from claim_parser import load_claims, load_user_history, load_evidence_requirements
from claim_preprocessor import preprocess_claim
from confidence import compute_confidence
from model_client import ModelClient
from rules_engine import validate_and_finalize, build_gate_skip_output
from stats_tracker import StatsTracker
from feedback_generator import generate_empathy_message, generate_reviewer_report
from output_writer import write_output_csv, validate_output


def process_single_claim(
    claim: dict,
    history: dict,
    requirements: list[dict],
    system_prompt: str,
    model_client: ModelClient | None,
    stats: StatsTracker,
    dry_run: bool = False,
) -> dict:
    """
    Process a single claim through the full pipeline.

    Returns a dict with all OUTPUT_COLUMNS ready for CSV writing.
    """
    claim_start = time.time()
    user_id = claim.get("user_id", "unknown")
    print(f"\n{'='*60}")
    print(f"Processing claim: {user_id} | object: {claim.get('claim_object', '?')}")

    # ---------------------------------------------------------------
    # Step 1: Preprocess — enrich claim with history, evidence, injection check
    # ---------------------------------------------------------------
    enriched = preprocess_claim(claim, history, requirements)
    print(f"  Injection detected: {enriched['injection_info']['detected']}")
    print(f"  Multi-claim: {enriched['multi_claim_info']['is_multi']}")
    print(f"  History risk: {enriched['history_info']['has_risk']}")

    # ---------------------------------------------------------------
    # Step 2: Load and validate images — GUARD GATES
    # ---------------------------------------------------------------
    # ── 3. Load and validate images ──────────────────────────────────────
    img_result = load_and_validate_images(
        claim["image_paths"], DATASET_DIR, user_id=user_id
    )
    image_data = img_result
    loaded_count = len(image_data["loaded"])
    failed_count = len(image_data["failed"])
    stats.record_images(sent=loaded_count, failed=failed_count)

    print(f"  Images loaded: {loaded_count}, failed: {failed_count}")

    # GATE: If no images loaded, skip model call entirely
    if image_data["all_failed"]:
        print(f"  GATE SKIP: All images failed to load — no API call made")
        stats.record_gate_skip()
        reason = "No usable images: " + "; ".join(
            f"{f['image_id']}: {f['reason']}" for f in image_data["failed"]
        ) if image_data["failed"] else "No images were provided or all image files are missing."
        result = build_gate_skip_output(enriched, reason)
        stats.record_claim_time(time.time() - claim_start)
        return result

    # ---------------------------------------------------------------
    # Step 3: Call the model (or mock in dry-run)
    # ---------------------------------------------------------------
    model_output = None

    if dry_run:
        print(f"  DRY RUN: Skipping model call")
        model_output = _mock_model_output(enriched, image_data)
    else:
        # Build the user prompt
        user_prompt = build_user_content(enriched, image_data["loaded"])

        # Prepare image lists for Gemini (PIL) and Groq (base64)
        images_pil = [img["pil_image"] for img in image_data["loaded"]]
        images_b64 = [
            {"image_id": img["image_id"], "base64": img["base64"]}
            for img in image_data["loaded"]
        ]

        print(f"  Calling model with {loaded_count} image(s)...")
        model_output = model_client.analyze_claim(
            images_pil=images_pil,
            images_b64=images_b64,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

        if model_output is None:
            print(f"  WARNING: Both Gemini and Groq failed — using deterministic fallback")

    # ---------------------------------------------------------------
    # Step 4: Rules engine — validate and finalize
    # ---------------------------------------------------------------
    result = validate_and_finalize(model_output, enriched)
    print(f"  Result: status={result['claim_status']}, "
          f"issue={result['issue_type']}, "
          f"part={result['object_part']}, "
          f"severity={result['severity']}")

    # ---------------------------------------------------------------
    # Step 5: Confidence scoring
    # ---------------------------------------------------------------
    conf = compute_confidence(model_output, result, enriched)
    print(f"  Confidence: {conf['score']:.2f} -> {conf['routing']}")

    # Embed confidence into justification if there's room
    justification = result.get("claim_status_justification", "")
    if justification and conf["score"] > 0:
        confidence_label = "High" if conf["score"] >= 0.8 else "Medium" if conf["score"] >= 0.5 else "Low"
        result["claim_status_justification"] = (
            f"{justification} ({confidence_label} confidence: {conf['score']:.2f})"
        )

    # ---------------------------------------------------------------
    # Step 6: Empathy Layer & Reviewer Dashboard
    # ---------------------------------------------------------------
    past_rejected_count = claim.get("history_info", {}).get("raw", {}).get("rejected_claim", 0)
    final_claim_status = result.get("claim_status", "not_enough_information")
    final_risk_flags_str = result.get("risk_flags", "")
    final_risk_flags = [x.strip() for x in final_risk_flags_str.split(";")] if final_risk_flags_str != "none" else []
    
    # Check if duplicate fraud was detected in preprocessing
    for img in image_data.get("loaded", []):
        if img.get("is_duplicate_fraud"):
            final_risk_flags.append("duplicate_image_fraud")
    
    empathy_msg = generate_empathy_message(
        final_claim_status, final_risk_flags, past_rejected_count
    )
    
    # If routed to human or fraud detected, generate the dashboard report
    if conf["routing"] in ("manual_review_recommended", "escalate_to_senior") or "duplicate_image_fraud" in final_risk_flags:
        reports_dir = Path("reviewer_reports")
        generate_reviewer_report(
            claim_id=user_id,
            output_dir=reports_dir,
            claim_data=claim,
            model_result={
                "risk_flags": final_risk_flags,
                "justification": result.get("claim_status_justification", ""),
                "confidence": conf["score"]
            },
            routing_status=conf["routing"],
            empathy_message=empathy_msg
        )

    stats.record_claim_time(time.time() - claim_start)
    return result


def _mock_model_output(claim_data: dict, image_data: dict) -> dict:
    """
    Generate a mock model output for dry-run testing.
    Returns a minimal valid response so the rules engine can be tested.
    """
    image_ids = [img["image_id"] for img in image_data["loaded"]]
    return {
        "per_image": [
            {
                "image_id": iid,
                "object_visible": claim_data.get("claim_object", "unknown"),
                "part_visible": "unknown",
                "damage_observed": "unknown",
                "severity_estimate": "unknown",
                "quality_flags": [],
                "is_original": True,
                "description": "Mock analysis for dry run",
            }
            for iid in image_ids
        ],
        "draft_issue_type": "unknown",
        "draft_object_part": "unknown",
        "draft_claim_status": "not_enough_information",
        "draft_severity": "unknown",
        "draft_evidence_met": False,
        "draft_evidence_reason": "Dry run — no model analysis performed.",
        "draft_supporting_image_ids": [],
        "draft_risk_flags": [],
        "draft_justification": "Dry run — no model analysis performed.",
        "text_instruction_detected": False,
        "text_instruction_details": "",
        "confidence": 0.0,
        "confidence_reasoning": "Dry run — no real analysis.",
        "multi_claim_detected": False,
        "sub_claims": [],
        "primary_sub_claim_index": 0,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Modal Evidence Review System"
    )
    parser.add_argument(
        "--sample", action="store_true",
        help="Process sample_claims.csv instead of claims.csv",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip model calls (use mock output) to test the pipeline",
    )
    args = parser.parse_args()

    # Determine input/output paths
    if args.sample:
        claims_path = SAMPLE_CLAIMS_PATH
        output_path = SAMPLE_OUTPUT_PATH
        print("=== SAMPLE MODE: Processing sample_claims.csv ===")
    else:
        claims_path = CLAIMS_PATH
        output_path = OUTPUT_PATH
        print("=== PRODUCTION MODE: Processing claims.csv ===")

    if args.dry_run:
        print("=== DRY RUN: No API calls will be made ===")

    # ------------------------------------------------------------------
    # Load all reference data
    # ------------------------------------------------------------------
    print(f"\nLoading claims from {claims_path}...")
    claims = load_claims(claims_path)
    print(f"  Loaded {len(claims)} claims")

    print(f"Loading user history from {USER_HISTORY_PATH}...")
    history = load_user_history(USER_HISTORY_PATH)
    print(f"  Loaded {len(history)} user records")

    print(f"Loading evidence requirements from {EVIDENCE_REQ_PATH}...")
    requirements = load_evidence_requirements(EVIDENCE_REQ_PATH)
    print(f"  Loaded {len(requirements)} requirements")

    # ------------------------------------------------------------------
    # Initialize model client and stats tracker
    # ------------------------------------------------------------------
    stats = StatsTracker()
    model_client = None
    if not args.dry_run:
        model_client = ModelClient(stats_tracker=stats)

    # Build the system prompt once (reused for every claim)
    system_prompt = build_system_prompt()
    print(f"\nSystem prompt built ({len(system_prompt)} chars)")

    # ------------------------------------------------------------------
    # Process each claim
    # ------------------------------------------------------------------
    results = []
    for i, claim in enumerate(claims):
        print(f"\n--- Claim {i+1}/{len(claims)} ---")
        result = process_single_claim(
            claim=claim,
            history=history,
            requirements=requirements,
            system_prompt=system_prompt,
            model_client=model_client,
            stats=stats,
            dry_run=args.dry_run,
        )
        results.append(result)

    # ------------------------------------------------------------------
    # Validate and write output
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("Validating output...")
    warnings = validate_output(results)
    if warnings:
        print(f"  {len(warnings)} warning(s):")
        for w in warnings:
            print(f"    [!] {w}")
    else:
        print("  All rows passed validation [OK]")

    print(f"\nWriting output to {output_path}...")
    write_output_csv(results, output_path)
    print(f"  Wrote {len(results)} rows [OK]")

    # ------------------------------------------------------------------
    # Print stats
    # ------------------------------------------------------------------
    stats.print_summary()

    # Save stats for the evaluation report
    stats_path = Path(__file__).resolve().parent / "evaluation" / "run_stats.json"
    stats.save_to_file(stats_path)
    print(f"\nStats saved to {stats_path}")

    print(f"\n{'='*60}")
    print("DONE.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
