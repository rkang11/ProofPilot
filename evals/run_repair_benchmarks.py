from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.db import ProofAttempt  # noqa: E402
from backend.repair import build_repair_candidate, build_repair_prompt, build_retry_prompt  # noqa: E402
from worker.verify import verify_lean  # noqa: E402


MAX_REPAIR_ROUNDS = max(1, int(os.environ.get("PROOFPILOT_REPAIR_ROUNDS", "3")))


@dataclass
class RepairRound:
    round_index: int
    provider: str
    model: str
    status: str
    elapsed_ms: int
    candidate_code: str


@dataclass
class RepairBenchmarkResult:
    id: str
    title: str
    topic: str
    expected_initial: str
    actual_initial: str
    initial_classification: str
    expected_repair: str
    actual_repair: str
    passed: bool
    rounds_used: int
    providers: list[str]
    total_elapsed_ms: int
    rounds: list[RepairRound]


def run_repair_benchmarks(path: Path) -> dict[str, object]:
    benchmarks = json.loads(path.read_text(encoding="utf-8"))
    results = [_run_case(benchmark) for benchmark in benchmarks]
    passed_count = sum(1 for result in results if result.passed)
    repaired_count = sum(1 for result in results if result.actual_repair == "verified")
    total_rounds = sum(result.rounds_used for result in results)
    total_elapsed = sum(result.total_elapsed_ms for result in results)

    return {
        "summary": {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "pass_rate": passed_count / len(results) if results else 0,
            "repair_success_rate": repaired_count / len(results) if results else 0,
            "avg_rounds_used": round(total_rounds / len(results), 2) if results else 0,
            "avg_elapsed_ms": round(total_elapsed / len(results)) if results else 0,
            "providers": sorted({provider for result in results for provider in result.providers}),
        },
        "results": [
            {
                **asdict(result),
                "rounds": [asdict(round_result) for round_result in result.rounds],
            }
            for result in results
        ],
    }


def _run_case(benchmark: dict[str, str]) -> RepairBenchmarkResult:
    initial = verify_lean(benchmark["code"])
    attempt = ProofAttempt(
        id=benchmark["id"],
        title=benchmark["title"],
        statement=benchmark["statement"],
        code=benchmark["code"],
        status=initial.status,
        created_at=0,
        result=initial.to_dict(),
    )

    base_prompt = build_repair_prompt(attempt)
    prompt = base_prompt
    rounds: list[RepairRound] = []
    total_elapsed = initial.elapsed_ms

    for round_index in range(1, MAX_REPAIR_ROUNDS + 1):
        candidate = build_repair_candidate(attempt, prompt)
        verification = verify_lean(candidate.code)
        result_payload = verification.to_dict()
        total_elapsed += verification.elapsed_ms
        rounds.append(
            RepairRound(
                round_index=round_index,
                provider=candidate.provider,
                model=candidate.model,
                status=verification.status,
                elapsed_ms=verification.elapsed_ms,
                candidate_code=candidate.code,
            )
        )

        if verification.status == "verified" or candidate.provider == "local":
            break

        prompt = build_retry_prompt(
            base_prompt=base_prompt,
            failed_candidate=candidate.code,
            verification_result=result_payload,
            round_index=round_index,
        )

    actual_repair = "verified" if any(round_result.status == "verified" for round_result in rounds) else "failed"
    initial_classification = _classification_category(initial.to_dict())
    passed = (
        initial.status == benchmark["expected_initial"]
        and actual_repair == benchmark["expected_repair"]
    )

    return RepairBenchmarkResult(
        id=benchmark["id"],
        title=benchmark["title"],
        topic=benchmark["topic"],
        expected_initial=benchmark["expected_initial"],
        actual_initial=initial.status,
        initial_classification=initial_classification,
        expected_repair=benchmark["expected_repair"],
        actual_repair=actual_repair,
        passed=passed,
        rounds_used=len(rounds),
        providers=[round_result.provider for round_result in rounds],
        total_elapsed_ms=total_elapsed,
        rounds=rounds,
    )


def _classification_category(result: dict[str, object]) -> str:
    classification = result.get("classification")
    if isinstance(classification, dict):
        return str(classification.get("category") or "unknown")
    return "unknown"


def main() -> None:
    report = run_repair_benchmarks(ROOT / "evals" / "repair_benchmarks.json")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
