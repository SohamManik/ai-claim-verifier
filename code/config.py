"""
config.py — Central configuration for the Multi-Modal Evidence Review system.

All constants, allowed values, paths, and schemas live here so every other
module can import from a single source of truth.
"""

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Optional: load .env if python-dotenv is available
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv

    _code_dir = Path(__file__).resolve().parent
    _repo_root = _code_dir.parent

    for _env_path in [_code_dir / ".env", _repo_root / ".env"]:
        if _env_path.exists():
            load_dotenv(_env_path, override=False)
            break
except ImportError:
    _code_dir = Path(__file__).resolve().parent
    _repo_root = _code_dir.parent

# ---------------------------------------------------------------------------
# API keys  (read from environment — never hardcoded)
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("gemini_api_key") or os.getenv("GEMINI_API_KEY") or ""
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY") or os.getenv("groq_api_key") or ""

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-2.5-flash"
GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
GEMINI_TEMPERATURE = 0.0
GROQ_TEMPERATURE = 0.0

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = _repo_root
CODE_DIR = _code_dir
DATASET_DIR = _repo_root / "dataset"
SAMPLE_CLAIMS_PATH = DATASET_DIR / "sample_claims.csv"
CLAIMS_PATH = DATASET_DIR / "claims.csv"
USER_HISTORY_PATH = DATASET_DIR / "user_history.csv"
EVIDENCE_REQ_PATH = DATASET_DIR / "evidence_requirements.csv"
SAMPLE_IMAGES_DIR = DATASET_DIR / "images" / "sample"
TEST_IMAGES_DIR = DATASET_DIR / "images" / "test"
OUTPUT_PATH = _repo_root / "output.csv"
SAMPLE_OUTPUT_PATH = _repo_root / "sample_output.csv"

# ---------------------------------------------------------------------------
# Image processing thresholds
# ---------------------------------------------------------------------------
MAX_IMAGE_DIMENSION = 1536   # Resize longest edge to this before sending
MIN_IMAGE_DIMENSION = 10     # Reject images smaller than 10×10
MIN_IMAGE_FILE_SIZE = 1024   # 1 KB — anything smaller is not a real photo
JPEG_QUALITY = 85            # Re-encode quality when resizing

# ---------------------------------------------------------------------------
# API call settings
# ---------------------------------------------------------------------------
API_TIMEOUT_SECONDS = 60
API_RETRY_DELAY_SECONDS = 3
RATE_LIMIT_DELAY_SECONDS = 4   # Min gap between consecutive API calls
MAX_RETRIES = 1                # Retry once on the same provider, then fallback

# ---------------------------------------------------------------------------
# Allowed values — copied verbatim from problem_statement.md
# ---------------------------------------------------------------------------
ALLOWED_CLAIM_STATUS = {"supported", "contradicted", "not_enough_information"}

ALLOWED_ISSUE_TYPES = {
    "dent", "scratch", "crack", "glass_shatter", "broken_part",
    "missing_part", "torn_packaging", "crushed_packaging",
    "water_damage", "stain", "none", "unknown",
}

ALLOWED_SEVERITY = {"none", "low", "medium", "high", "unknown"}

ALLOWED_RISK_FLAGS = {
    "none", "blurry_image", "cropped_or_obstructed", "low_light_or_glare",
    "wrong_angle", "wrong_object", "wrong_object_part", "damage_not_visible",
    "claim_mismatch", "possible_manipulation", "non_original_image",
    "text_instruction_present", "user_history_risk", "manual_review_required",
}

ALLOWED_OBJECT_PARTS: dict[str, set[str]] = {
    "car": {
        "front_bumper", "rear_bumper", "door", "hood", "windshield",
        "side_mirror", "headlight", "taillight", "fender",
        "quarter_panel", "body", "unknown",
    },
    "laptop": {
        "screen", "keyboard", "trackpad", "hinge", "lid",
        "corner", "port", "base", "body", "unknown",
    },
    "package": {
        "box", "package_corner", "package_side", "seal",
        "label", "contents", "item", "unknown",
    },
}

ALLOWED_CLAIM_OBJECTS = {"car", "laptop", "package"}

# ---------------------------------------------------------------------------
# Output CSV column order — must match problem_statement.md exactly
# ---------------------------------------------------------------------------
OUTPUT_COLUMNS: list[str] = [
    "user_id", "image_paths", "user_claim", "claim_object",
    "evidence_standard_met", "evidence_standard_met_reason",
    "risk_flags", "issue_type", "object_part", "claim_status",
    "claim_status_justification", "supporting_image_ids",
    "valid_image", "severity",
]

# ---------------------------------------------------------------------------
# Prompt-injection detection patterns (compiled regexes)
# ---------------------------------------------------------------------------
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"\b(?:approve|accept)\s+(?:this|the|my)\s+(?:claim|request)",
        re.IGNORECASE,
    ),
    re.compile(r"\bapprove\s+(?:it|immediately|right\s+away)\b", re.IGNORECASE),
    re.compile(r"\b(?:skip|bypass)\s+(?:manual\s+)?review\b", re.IGNORECASE),
    re.compile(r"\bmark\s+(?:this\s+)?(?:as\s+)?(?:supported|approved)\b", re.IGNORECASE),
    re.compile(
        r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|rules?)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bdisregard\b", re.IGNORECASE),
    re.compile(r"\bfollow\s+(?:the\s+note|it)\s+and\b", re.IGNORECASE),
    re.compile(r"\bsystem\s+reading\s+this\s+should\b", re.IGNORECASE),
    re.compile(r"\bautomatically\s+(?:approve|accept)\b", re.IGNORECASE),
    re.compile(r"\bdo\s+(?:not|n.t)\s+(?:comply|reject|deny)\b", re.IGNORECASE),
    re.compile(r"\boverride\b", re.IGNORECASE),
    re.compile(r"\bapprove\s+the\s+claim\b", re.IGNORECASE),
    re.compile(r"\bfollow\s+(?:it|the\s+note)\b.*\bapprove\b", re.IGNORECASE),
    re.compile(r"\bnote\s+(?:says?|is)\b.*\bapprove\b", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Gemini structured-output JSON schema
# ---------------------------------------------------------------------------
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "per_image": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "image_id": {
                        "type": "string",
                        "description": "Filename without extension, e.g. img_1",
                    },
                    "object_visible": {
                        "type": "string",
                        "description": "What main object is visible: car, laptop, package, or describe otherwise",
                    },
                    "part_visible": {
                        "type": "string",
                        "description": "Which part of that object is most prominently visible",
                    },
                    "damage_observed": {
                        "type": "string",
                        "description": "Type of damage visible (dent, scratch, crack, etc.) or none",
                    },
                    "severity_estimate": {
                        "type": "string",
                        "description": "none, low, medium, high, or unknown",
                    },
                    "quality_flags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Image quality issues from allowed risk_flags list",
                    },
                    "is_original": {
                        "type": "boolean",
                        "description": "true if this looks like an original photo; false if stock, AI-generated, screenshot, or watermarked",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-sentence description of what the image shows",
                    },
                },
                "required": [
                    "image_id", "object_visible", "part_visible",
                    "damage_observed", "severity_estimate", "quality_flags",
                    "is_original", "description",
                ],
            },
        },
        "draft_issue_type": {"type": "string"},
        "draft_object_part": {"type": "string"},
        "draft_claim_status": {
            "type": "string",
            "description": "supported, contradicted, or not_enough_information",
        },
        "draft_severity": {
            "type": "string",
            "description": "none, low, medium, high, or unknown",
        },
        "draft_evidence_met": {"type": "boolean"},
        "draft_evidence_reason": {"type": "string"},
        "draft_supporting_image_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
        "draft_risk_flags": {
            "type": "array",
            "items": {"type": "string"},
        },
        "draft_justification": {"type": "string"},
        "text_instruction_detected": {"type": "boolean"},
        "text_instruction_details": {"type": "string"},
        "confidence": {
            "type": "number",
            "description": "0.0 to 1.0 — how confident you are in this assessment",
        },
        "confidence_reasoning": {"type": "string"},
        "multi_claim_detected": {"type": "boolean"},
        "sub_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "part": {"type": "string"},
                    "issue": {"type": "string"},
                    "evidence_found": {"type": "boolean"},
                },
                "required": ["part", "issue", "evidence_found"],
            },
        },
        "primary_sub_claim_index": {"type": "integer"},
    },
    "required": [
        "per_image", "draft_issue_type", "draft_object_part",
        "draft_claim_status", "draft_severity", "draft_evidence_met",
        "draft_evidence_reason", "draft_supporting_image_ids",
        "draft_risk_flags", "draft_justification",
        "text_instruction_detected", "confidence", "multi_claim_detected",
    ],
}

# ---------------------------------------------------------------------------
# Issue-type to evidence-requirement family mapping
# Used by the rules engine to find the right evidence requirement row.
# ---------------------------------------------------------------------------
ISSUE_TO_EVIDENCE_FAMILY: dict[str, str] = {
    "dent": "dent or scratch",
    "scratch": "dent or scratch",
    "crack": "crack, broken, or missing part",
    "glass_shatter": "crack, broken, or missing part",
    "broken_part": "crack, broken, or missing part",
    "missing_part": "crack, broken, or missing part",
    "torn_packaging": "crushed, torn, or seal damage",
    "crushed_packaging": "crushed, torn, or seal damage",
    "water_damage": "water, stain, or label damage",
    "stain": "water, stain, or label damage",
}

# ---------------------------------------------------------------------------
# Fuzzy-match helpers for enum validation
# Maps common model misstatements to the canonical allowed value.
# ---------------------------------------------------------------------------
ISSUE_TYPE_ALIASES: dict[str, str] = {
    "scratches": "scratch",
    "scratched": "scratch",
    "dented": "dent",
    "dents": "dent",
    "cracked": "crack",
    "cracks": "crack",
    "shatter": "glass_shatter",
    "shattered": "glass_shatter",
    "glass_crack": "glass_shatter",
    "broken": "broken_part",
    "missing": "missing_part",
    "torn": "torn_packaging",
    "crushed": "crushed_packaging",
    "wet": "water_damage",
    "liquid_damage": "water_damage",
    "oil_stain": "stain",
    "stained": "stain",
    "no_damage": "none",
    "no damage": "none",
    "not_visible": "unknown",
    "unclear": "unknown",
}

SEVERITY_ALIASES: dict[str, str] = {
    "minor": "low",
    "moderate": "medium",
    "severe": "high",
    "critical": "high",
    "not_applicable": "none",
    "n/a": "none",
    "unclear": "unknown",
    "not_visible": "unknown",
}

CLAIM_STATUS_ALIASES: dict[str, str] = {
    "support": "supported",
    "supports": "supported",
    "contradict": "contradicted",
    "contradicts": "contradicted",
    "insufficient": "not_enough_information",
    "insufficient_evidence": "not_enough_information",
    "not_enough_info": "not_enough_information",
    "nei": "not_enough_information",
    "unclear": "not_enough_information",
}
