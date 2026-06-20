import os
from pathlib import Path

def generate_empathy_message(claim_status: str, quality_flags: list[str], past_rejected_count: int) -> str:
    """Generate a polite, actionable support email based on the claim status and flags."""
    if claim_status in ("supported", "auto_approve_candidate"):
        return "Your claim has been successfully processed and is moving forward!"

    # Base response
    msg = "Thank you for submitting your claim. "

    if "blurry_image" in quality_flags or "wrong_angle" in quality_flags or "glare_reflection" in quality_flags:
        msg += "Unfortunately, we couldn't automatically verify the damage because the image quality wasn't quite clear enough. "
        msg += "Could you please take another photo in a well-lit room without the flash? "
    elif claim_status == "not_enough_information":
        msg += "We need a bit more visual evidence to fully assess this claim. Please upload additional photos. "
    elif claim_status == "contradicted":
        msg += "Based on our review, the visual evidence does not match the claim description. Your claim has been flagged for manual review. "

    # The "Empathy" Fallback
    if past_rejected_count >= 2:
        msg += "\n\nWe noticed you've had trouble with the automated system recently. "
        msg += "We're here to help! Please contact our human support team directly at 1-800-SUPPORT "
        msg += "or reply to this email to speak with an agent."

    return msg.strip()

def generate_reviewer_report(
    claim_id: str,
    output_dir: Path,
    claim_data: dict,
    model_result: dict,
    routing_status: str,
    empathy_message: str
) -> None:
    """Generate a markdown report for human reviewers."""
    os.makedirs(output_dir, exist_ok=True)
    report_path = output_dir / f"{claim_id}_report.md"

    # Gather data
    user_claim = claim_data.get("user_claim", "")
    risk_flags = ", ".join(model_result.get("risk_flags", [])) or "None"
    justification = model_result.get("justification", "No justification provided.")
    confidence = model_result.get("confidence", 0.0)
    image_paths = claim_data.get("image_paths", "None")

    markdown_content = f"""# Manual Review Required: {claim_id}

## Overview
- **Routing Status:** `{routing_status}`
- **Model Confidence:** {confidence:.2f}
- **Risk Flags:** {risk_flags}

## Claim Details
**User's Original Claim:**
> {user_claim}

**Submitted Images:**
`{image_paths}`

## AI Assessment
**Justification:**
> {justification}

---

## Customer Experience (CX) Fallback Draft
*If this claim is rejected, the following automated email will be sent to the customer:*

> {empathy_message}
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)
