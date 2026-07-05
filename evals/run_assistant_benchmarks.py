from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Keep this eval deterministic and free of external API calls.
os.environ["GEMINI_API_KEY"] = ""

from backend.assist import create_assistant_response  # noqa: E402


BENCHMARK_PATH = Path(__file__).with_name("assistant_benchmarks.json")
STRUCTURED_EXPLANATION_KEYS = (
    "assumptions",
    "goal",
    "strategy",
    "lean_translation",
    "step_explanation",
)
GUIDED_FORMALIZATION_KEYS = (
    "cleaned_statement",
    "assumptions",
    "conclusion",
    "lean_statement",
    "verification_expectation",
)


def run() -> dict[str, Any]:
    cases = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    results = [_run_case(case) for case in cases]
    passed = sum(1 for result in results if result["passed"])
    return {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": passed / len(results) if results else 0,
            "avg_elapsed_ms": round(sum(result["elapsed_ms"] for result in results) / len(results)) if results else 0,
        },
        "results": results,
    }


def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    response = create_assistant_response(
        str(case["prompt"]),
        mode=str(case.get("mode", "auto")),
        lean_code_context=str(case.get("lean_code_context", "")),
    )
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    verification = response.get("verification", {})
    actual_verification = verification.get("status") if isinstance(verification, dict) else "unknown"
    required_lean = str(case.get("required_lean", ""))
    required_text = str(case.get("required_text", ""))
    text_blob = "\n".join(
        str(response.get(key, ""))
        for key in ("proof_sketch", "formalization_plan", "explanation")
    )

    verification_passed = actual_verification == case.get("expected_verification")
    lean_passed = not required_lean or required_lean in str(response.get("lean_code", ""))
    text_passed = not required_text or required_text.lower() in text_blob.lower()
    structured = response.get("structured_explanation", {})
    structured_passed = isinstance(structured, dict) and all(
        str(structured.get(key, "")).strip() for key in STRUCTURED_EXPLANATION_KEYS
    )
    guided = response.get("guided_formalization", {})
    guided_passed = isinstance(guided, dict) and all(
        str(guided.get(key, "")).strip() for key in GUIDED_FORMALIZATION_KEYS
    )
    flow = response.get("proof_flow", [])
    flow_passed = isinstance(flow, list) and len(flow) > 0 and all(
        isinstance(step, dict)
        and str(step.get("label", "")).strip()
        and str(step.get("detail", "")).strip()
        for step in flow
    )

    return {
        "id": case["id"],
        "mode": case.get("mode", "auto"),
        "expected_verification": case.get("expected_verification"),
        "actual_verification": actual_verification,
        "verification_passed": verification_passed,
        "lean_passed": lean_passed,
        "text_passed": text_passed,
        "structured_passed": structured_passed,
        "guided_passed": guided_passed,
        "flow_passed": flow_passed,
        "passed": verification_passed and lean_passed and text_passed and structured_passed and guided_passed and flow_passed,
        "elapsed_ms": elapsed_ms,
        "provider": response.get("provider"),
        "model": response.get("model"),
    }


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["summary"]["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
