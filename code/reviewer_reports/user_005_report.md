# Manual Review Required: user_005

## Overview
- **Routing Status:** `manual_review_recommended`
- **Model Confidence:** 0.85
- **Risk Flags:** user_history_risk, non_original_image, manual_review_required

## Claim Details
**User's Original Claim:**
> Customer: Need to file a car damage claim. | Agent: What part of the car? | Customer: Door. | Agent: Scratch, dent, or paint issue? | Customer: A deep dent on the door panel. It was not there before.

**Submitted Images:**
`images/test/case_003/img_1.jpg`

## AI Assessment
**Justification:**
> The image provided (img_1) is a stock photo commonly used to illustrate minor car door edge chips, meaning it does not show the claimant's actual vehicle. Furthermore, the minor nick shown in the stock photo does not match the description of a 'deep dent on the door panel'. Due to the non-original evidence and the user's history of risk, the claim cannot be verified. (High confidence: 0.85)

---

## Customer Experience (CX) Fallback Draft
*If this claim is rejected, the following automated email will be sent to the customer:*

> Thank you for submitting your claim. We need a bit more visual evidence to fully assess this claim. Please upload additional photos.
