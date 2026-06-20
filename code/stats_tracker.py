"""
stats_tracker.py — Operational metrics tracker for the Multi-Modal Evidence
Review system.

Tracks API call counts, token usage, image counts, per-claim timing, and
estimated cost.
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class StatsTracker:
    """Tracks operational metrics: API calls, tokens, timing, and images processed.

    All counters are plain ints/floats — no locking — suitable for the
    single-threaded pipeline.

    Attributes:
        start_time: Wall-clock time when tracking started.
        api_calls: Nested dict of ``{provider: {success: int, failure: int}}``.
        calls_saved_by_gates: Number of claims short-circuited before an API call.
        total_images_sent: Total images successfully sent to a model.
        total_images_failed: Total images that failed to load / process.
        total_input_tokens: Cumulative prompt-token count across all calls.
        total_output_tokens: Cumulative completion-token count across all calls.
        claims_processed: Number of claims fully processed (API or gate).
        per_claim_times: List of per-claim elapsed seconds.
    """

    # Gemini Flash pricing (per million tokens)
    _INPUT_COST_PER_M: float = 0.075
    _OUTPUT_COST_PER_M: float = 0.30

    def __init__(self) -> None:
        self.start_time: float = time.time()
        self.api_calls: dict[str, dict[str, int]] = {
            "gemini": {"success": 0, "failure": 0},
            "groq": {"success": 0, "failure": 0},
        }
        self.calls_saved_by_gates: int = 0
        self.total_images_sent: int = 0
        self.total_images_failed: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.claims_processed: int = 0
        self.per_claim_times: list[float] = []

    # ── recording helpers ────────────────────────────────────────────────

    def record_api_call(
        self,
        provider: str,
        success: bool,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Record an API call attempt.

        Parameters:
            provider: ``"gemini"`` or ``"groq"``.
            success: Whether the call returned a valid response.
            input_tokens: Prompt tokens consumed (0 if unknown).
            output_tokens: Completion tokens consumed (0 if unknown).
        """
        bucket = self.api_calls.setdefault(
            provider, {"success": 0, "failure": 0}
        )
        if success:
            bucket["success"] += 1
        else:
            bucket["failure"] += 1

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

    def record_gate_skip(self) -> None:
        """Record that a claim was skipped by a guard gate (no API call made)."""
        self.calls_saved_by_gates += 1

    def record_images(self, sent: int, failed: int) -> None:
        """Record image counts for a single claim.

        Parameters:
            sent: Number of images successfully sent to the model.
            failed: Number of images that could not be loaded or processed.
        """
        self.total_images_sent += sent
        self.total_images_failed += failed

    def record_claim_time(self, elapsed: float) -> None:
        """Record processing time (seconds) for one claim."""
        self.claims_processed += 1
        self.per_claim_times.append(elapsed)

    # ── summary / reporting ──────────────────────────────────────────────

    def get_summary(self) -> dict:
        """Return a summary dict with all collected metrics.

        Includes:
            * ``total_runtime_seconds``
            * ``total_api_calls`` (gemini + groq, success + failure)
            * ``calls_saved_by_gates``
            * ``total_images_sent``, ``total_images_failed``
            * ``total_input_tokens``, ``total_output_tokens``
            * ``claims_processed``
            * ``avg_claim_time_seconds``
            * ``estimated_cost`` (Gemini Flash pricing)

        Returns:
            A JSON-serialisable ``dict``.
        """
        total_runtime = time.time() - self.start_time

        total_api = sum(
            counts["success"] + counts["failure"]
            for counts in self.api_calls.values()
        )

        avg_claim_time = (
            sum(self.per_claim_times) / len(self.per_claim_times)
            if self.per_claim_times
            else 0.0
        )

        estimated_cost = (
            (self.total_input_tokens / 1_000_000) * self._INPUT_COST_PER_M
            + (self.total_output_tokens / 1_000_000) * self._OUTPUT_COST_PER_M
        )

        return {
            "total_runtime_seconds": round(total_runtime, 2),
            "total_api_calls": total_api,
            "api_calls_detail": self.api_calls,
            "calls_saved_by_gates": self.calls_saved_by_gates,
            "total_images_sent": self.total_images_sent,
            "total_images_failed": self.total_images_failed,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "claims_processed": self.claims_processed,
            "avg_claim_time_seconds": round(avg_claim_time, 3),
            "estimated_cost": round(estimated_cost, 6),
        }

    def save_to_file(self, path: Path) -> None:
        """Save the summary as a JSON file.

        Parameters:
            path: Destination file path; parent directories are created if
                needed.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.get_summary(), fh, indent=2)
        print(f"[stats] Saved summary -> {path}")

    def print_summary(self) -> None:
        """Print a human-readable summary to stdout."""
        summary = self.get_summary()
        print("\n" + "=" * 60)
        print("  PIPELINE METRICS SUMMARY")
        print("=" * 60)
        print(f"  Total runtime ........... {summary['total_runtime_seconds']:.1f}s")
        print(f"  Claims processed ........ {summary['claims_processed']}")
        print(f"  Avg claim time .......... {summary['avg_claim_time_seconds']:.3f}s")
        print(f"  Total API calls ......... {summary['total_api_calls']}")
        gemini = summary["api_calls_detail"].get("gemini", {})
        groq = summary["api_calls_detail"].get("groq", {})
        print(
            f"    Gemini  success/fail .. {gemini.get('success', 0)}"
            f"/{gemini.get('failure', 0)}"
        )
        print(
            f"    Groq    success/fail .. {groq.get('success', 0)}"
            f"/{groq.get('failure', 0)}"
        )
        print(f"  Calls saved by gates .... {summary['calls_saved_by_gates']}")
        print(
            f"  Images sent / failed .... {summary['total_images_sent']}"
            f" / {summary['total_images_failed']}"
        )
        print(f"  Input tokens ............ {summary['total_input_tokens']:,}")
        print(f"  Output tokens ........... {summary['total_output_tokens']:,}")
        print(f"  Estimated cost .......... ${summary['estimated_cost']:.4f}")
        print("=" * 60 + "\n")
