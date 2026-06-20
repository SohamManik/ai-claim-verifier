# Manual Review Required: user_004

## Overview
- **Routing Status:** `manual_review_recommended`
- **Model Confidence:** 0.70
- **Risk Flags:** non_original_image, claim_mismatch, damage_not_visible

## Claim Details
**User's Original Claim:**
> Customer: A stone hit the front glass while driving. | Support: Are you reporting the windshield? | Customer: Yes. It looks shattered from my side. | Support: Any other part involved? | Customer: No, only the windshield shatter claim.

**Submitted Images:**
`images/test/case_004/img_1.jpg;images/test/case_004/img_2.jpg`

## AI Assessment
**Justification:**
> The claim is contradicted because img_2 shows the interior of a vehicle with a completely intact and undamaged windshield, directly contradicting the claim of a shattered windshield. Furthermore, both img_1 and img_2 appear to be non-original stock photos, and they are completely inconsistent with each other (img_1 shows a heavily cracked windshield from a different setting, while img_2 shows a clear windshield on a different road). (Medium confidence: 0.70)

---

## Customer Experience (CX) Fallback Draft
*If this claim is rejected, the following automated email will be sent to the customer:*

> Thank you for submitting your claim. Based on our review, the visual evidence does not match the claim description. Your claim has been flagged for manual review.
