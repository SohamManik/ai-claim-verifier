"""
model_client.py — Manages Gemini (primary) and Groq (fallback) API calls for
the Multi-Modal Evidence Review system.

Handles retries, timeouts, rate limiting, and structured-JSON response parsing.
"""

from __future__ import annotations

import base64
import io
import json
import time
from typing import Any

from PIL import Image
from google import genai
from google.genai import types
from groq import Groq

from config import (
    API_RETRY_DELAY_SECONDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TEMPERATURE,
    GROQ_API_KEY,
    GROQ_MODEL,
    GROQ_TEMPERATURE,
    RATE_LIMIT_DELAY_SECONDS,
    RESPONSE_SCHEMA,
)


class ModelClient:
    """Manages Gemini (primary) and Groq (fallback) API calls.

    Handles retries, timeouts, rate limiting, and response parsing.

    Attributes:
        gemini_client: The ``google.genai.Client`` instance.
        groq_client: The ``groq.Groq`` instance.
        stats: Optional stats-tracker for recording metrics.
        last_call_time: Timestamp of the most recent API call (for rate
            limiting).
    """

    def __init__(self, stats_tracker: Any | None = None) -> None:
        """Initialise Gemini and Groq API clients.

        Parameters:
            stats_tracker: An optional :class:`StatsTracker` instance for
                recording API-call metrics.
        """
        self.gemini_client = genai.Client(api_key=GEMINI_API_KEY)
        self.groq_client = Groq(api_key=GROQ_API_KEY)
        self.stats = stats_tracker
        self.last_call_time: float = 0.0

    # ── rate limiting ────────────────────────────────────────────────────

    def _rate_limit_wait(self) -> None:
        """Ensure at least ``RATE_LIMIT_DELAY_SECONDS`` between API calls."""
        elapsed = time.time() - self.last_call_time
        if elapsed < RATE_LIMIT_DELAY_SECONDS:
            wait = RATE_LIMIT_DELAY_SECONDS - elapsed
            print(f"[rate-limit] sleeping {wait:.1f}s")
            time.sleep(wait)
        self.last_call_time = time.time()

    # ── Gemini ───────────────────────────────────────────────────────────

    def _call_gemini(
        self,
        images: list[Image.Image],
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """Make a Gemini API call and return the parsed JSON response.

        Parameters:
            images: List of ``PIL.Image.Image`` objects to include.
            system_prompt: The system instruction string.
            user_prompt: The per-claim user prompt string.

        Returns:
            Parsed JSON ``dict`` from the model response.

        Raises:
            Exception: Re-raised so ``analyze_claim`` can handle
                retry / fallback.
        """
        contents: list[Any] = [user_prompt] + list(images)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=GEMINI_TEMPERATURE,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )

        response = self.gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=config,
        )

        # Record token usage if metadata is present
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        if self.stats:
            self.stats.record_api_call(
                "gemini",
                success=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        parsed: dict = json.loads(response.text)
        return parsed

    # ── Groq (fallback) ──────────────────────────────────────────────────

    def _call_groq(
        self,
        images_b64: list[dict[str, str]],
        system_prompt: str,
        user_prompt: str,
    ) -> dict:
        """Make a Groq API call (fallback) and return the parsed JSON response.

        Parameters:
            images_b64: List of dicts with ``image_id`` and ``base64`` keys.
            system_prompt: The system instruction string.
            user_prompt: The per-claim user prompt string.

        Returns:
            Parsed JSON ``dict`` from the model response.

        Raises:
            Exception: Re-raised so ``analyze_claim`` can handle fallback.
        """
        # Build the JSON-schema reminder for Groq (no native schema param)
        schema_reminder = (
            "\n\nYou MUST respond with valid JSON matching this schema:\n"
            + json.dumps(RESPONSE_SCHEMA, indent=2)
        )

        # Construct user content parts: text first, then images
        user_content_parts: list[dict[str, Any]] = [
            {"type": "text", "text": user_prompt},
        ]
        for img_item in images_b64:
            user_content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_item['base64']}",
                    },
                }
            )

        messages = [
            {"role": "system", "content": system_prompt + schema_reminder},
            {"role": "user", "content": user_content_parts},
        ]

        response = self.groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=GROQ_TEMPERATURE,
            response_format={"type": "json_object"},
        )

        # Record token usage
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0

        if self.stats:
            self.stats.record_api_call(
                "groq",
                success=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        raw_text: str = response.choices[0].message.content
        try:
            parsed: dict = json.loads(raw_text)
        except json.JSONDecodeError:
            healed_text = self._heal_json(raw_text)
            parsed: dict = json.loads(healed_text)

        return parsed

    def _heal_json(self, raw_text: str) -> str:
        """Attempt to fix broken JSON returned by models (especially Groq)."""
        import re
        # Remove markdown ticks
        text = re.sub(r"```json\s*", "", raw_text)
        text = re.sub(r"```\s*", "", text)
        
        # Extract the outermost JSON object if there's conversational prefix
        match = re.search(r"(\{.*\})", text, re.DOTALL)
        if match:
            text = match.group(1)
            
        # Fix trailing commas
        text = re.sub(r",\s*([\]}])", r"\1", text)
        return text.strip()

    # ── main entry point ─────────────────────────────────────────────────

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        """Check if the exception is a 429 rate-limit error."""
        exc_str = str(exc)
        return "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str or "rate" in exc_str.lower()

    def analyze_claim(
        self,
        images_pil: list[Image.Image],
        images_b64: list[dict[str, str]],
        system_prompt: str,
        user_prompt: str,
    ) -> dict | None:
        """Analyse a single claim with multi-model fallback chain.

        Flow:
            1. Try Gemini with primary model (gemini-2.5-flash).
            2. If rate-limited (429), try Gemini with fallback model (gemini-2.0-flash).
            3. If that also fails, try Groq.
            4. If all fail, return None.

        Parameters:
            images_pil: PIL images for Gemini.
            images_b64: Base64-encoded images for Groq.
            system_prompt: System instruction string.
            user_prompt: Per-claim user prompt string.

        Returns:
            Parsed response ``dict``, or ``None`` if all providers fail.
        """
        gemini_models = [GEMINI_MODEL, "gemini-2.0-flash", "gemini-3.5-flash", "gemini-2.0-flash-lite"]

        for model_name in gemini_models:
            self._rate_limit_wait()
            try:
                print(f"[model] Gemini ({model_name}) ...")
                result = self._call_gemini_with_model(
                    images_pil, system_prompt, user_prompt, model_name
                )
                return result
            except Exception as exc:
                print(f"[model] Gemini ({model_name}) failed ({type(exc).__name__}: {str(exc)[:120]})")
                if self.stats:
                    self.stats.record_api_call("gemini", success=False)

                # If NOT a rate limit error, retry same model once
                if not self._is_rate_limit_error(exc):
                    time.sleep(API_RETRY_DELAY_SECONDS)
                    self._rate_limit_wait()
                    try:
                        print(f"[model] Gemini ({model_name}) retry ...")
                        result = self._call_gemini_with_model(
                            images_pil, system_prompt, user_prompt, model_name
                        )
                        return result
                    except Exception as exc2:
                        print(f"[model] Gemini ({model_name}) retry failed ({type(exc2).__name__})")
                        if self.stats:
                            self.stats.record_api_call("gemini", success=False)
                # If rate-limited, skip retry and move to next model

        # -- Groq fallback --
        self._rate_limit_wait()
        try:
            print("[model] Groq fallback ...")
            return self._call_groq(images_b64, system_prompt, user_prompt)
        except Exception as exc:
            print(f"[model] Groq fallback failed ({type(exc).__name__}: {exc})")
            if self.stats:
                self.stats.record_api_call("groq", success=False)

        # All providers exhausted
        print("[model] All providers failed -- returning None")
        return None

    def _call_gemini_with_model(
        self,
        images: list[Image.Image],
        system_prompt: str,
        user_prompt: str,
        model_name: str,
    ) -> dict:
        """Make a Gemini API call with a specific model name."""
        contents: list[Any] = [user_prompt] + list(images)

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=GEMINI_TEMPERATURE,
            response_mime_type="application/json",
            response_schema=RESPONSE_SCHEMA,
        )

        response = self.gemini_client.models.generate_content(
            model=model_name,
            contents=contents,
            config=config,
        )

        # Record token usage if metadata is present
        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        if self.stats:
            self.stats.record_api_call(
                "gemini",
                success=True,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        parsed: dict = json.loads(response.text)
        return parsed

