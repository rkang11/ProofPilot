from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from worker.verify import verify_lean  # noqa: E402


@dataclass
class BenchmarkResult:
    id: str
    title: str
    topic: str
    expected: str
    actual: str
    passed: bool
    elapsed_ms: int
    stderr: str


def run_benchmarks(path: Path) -> dict[str, object]:
    benchmarks = json.loads(path.read_text(encoding="utf-8"))
    results: list[BenchmarkResult] = []

    for benchmark in benchmarks:
        verification = verify_lean(benchmark["code"])
        expected = benchmark["expected"]
        actual = verification.status
        results.append(
            BenchmarkResult(
                id=benchmark["id"],
                title=benchmark["title"],
                topic=benchmark["topic"],
                expected=expected,
                actual=actual,
                passed=actual == expected,
                elapsed_ms=verification.elapsed_ms,
                stderr=verification.stderr,
            )
        )

    passed_count = sum(1 for result in results if result.passed)
    total_elapsed = sum(result.elapsed_ms for result in results)

    return {
        "summary": {
            "total": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
            "pass_rate": passed_count / len(results) if results else 0,
            "avg_elapsed_ms": round(total_elapsed / len(results)) if results else 0,
        },
        "results": [asdict(result) for result in results],
    }


def main() -> None:
    report = run_benchmarks(ROOT / "evals" / "benchmarks.json")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
