from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path


DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("PROOFPILOT_VERIFY_TIMEOUT", "8.0"))
PROOF_FILENAME = "ProofPilotAttempt.lean"
PLACEHOLDER_PATTERN = re.compile(r"\b(sorry|admit)\b")


@dataclass
class VerificationResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    elapsed_ms: int
    verifier: str
    verifier_version: str | None = None
    error: str | None = None
    classification: dict[str, str] | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_lean(code: str, timeout_seconds: float | None = None) -> VerificationResult:
    start = time.perf_counter()
    timeout = timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
    lean_path = shutil.which("lean")

    if lean_path is None:
        return VerificationResult(
            status="tool_missing",
            exit_code=None,
            stdout="",
            stderr="Lean executable was not found on PATH.",
            elapsed_ms=_elapsed_ms(start),
            verifier="lean",
            verifier_version=None,
            error="Install Lean 4 and ensure `lean` is available on PATH.",
            classification=_classify_diagnostics(
                status="tool_missing",
                stdout="",
                stderr="Lean executable was not found on PATH.",
                error="Install Lean 4 and ensure `lean` is available on PATH.",
            ),
        )

    verifier_info = _validate_lean_executable(lean_path)
    if verifier_info["error"] is not None:
        return VerificationResult(
            status="tool_missing",
            exit_code=None,
            stdout="",
            stderr=verifier_info["error"],
            elapsed_ms=_elapsed_ms(start),
            verifier="lean",
            verifier_version=None,
            error="Install Lean 4 and ensure the theorem prover binary appears before other `lean` commands on PATH.",
            classification=_classify_diagnostics(
                status="tool_missing",
                stdout="",
                stderr=verifier_info["error"],
                error="Install Lean 4 and ensure the theorem prover binary appears before other `lean` commands on PATH.",
            ),
        )

    if _contains_placeholder(code):
        return VerificationResult(
            status="rejected",
            exit_code=None,
            stdout="",
            stderr="Proof contains a forbidden placeholder: `sorry` or `admit`.",
            elapsed_ms=_elapsed_ms(start),
            verifier="lean",
            verifier_version=verifier_info["version"],
            error="Proofs with placeholders are not accepted as verified output.",
            classification=_classify_diagnostics(
                status="rejected",
                stdout="",
                stderr="Proof contains a forbidden placeholder: `sorry` or `admit`.",
                error="Proofs with placeholders are not accepted as verified output.",
            ),
        )

    with tempfile.TemporaryDirectory(prefix="proofpilot-lean-") as tmp:
        proof_file = Path(tmp) / PROOF_FILENAME
        proof_file.write_text(code, encoding="utf-8")

        try:
            completed = subprocess.run(
                [lean_path, str(proof_file)],
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return VerificationResult(
                status="timeout",
                exit_code=None,
                stdout=_sanitize_output(exc.stdout or "", proof_file),
                stderr=_sanitize_output(exc.stderr or "", proof_file),
                elapsed_ms=_elapsed_ms(start),
                verifier="lean",
                verifier_version=verifier_info["version"],
                error=f"Verification exceeded {timeout:.1f}s.",
                classification=_classify_diagnostics(
                    status="timeout",
                    stdout=_sanitize_output(exc.stdout or "", proof_file),
                    stderr=_sanitize_output(exc.stderr or "", proof_file),
                    error=f"Verification exceeded {timeout:.1f}s.",
                ),
            )

    stdout = _sanitize_output(completed.stdout, proof_file)
    stderr = _sanitize_output(completed.stderr, proof_file)
    status = "verified" if completed.returncode == 0 else "failed"
    return VerificationResult(
        status=status,
        exit_code=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        elapsed_ms=_elapsed_ms(start),
        verifier="lean",
        verifier_version=verifier_info["version"],
        classification=_classify_diagnostics(
            status=status,
            stdout=stdout,
            stderr=stderr,
            error=None,
        ),
    )


def _elapsed_ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


@lru_cache(maxsize=8)
def _validate_lean_executable(lean_path: str) -> dict[str, str | None]:
    try:
        completed = subprocess.run(
            [lean_path, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3.0,
        )
    except subprocess.TimeoutExpired:
        return {"version": None, "error": f"`{lean_path} --version` timed out."}

    output = f"{completed.stdout}\n{completed.stderr}".strip()
    if completed.returncode != 0:
        return {"version": None, "error": f"`{lean_path} --version` failed:\n{output}"}

    if "Lean" not in output or "version" not in output:
        return {"version": None, "error": f"`{lean_path}` does not look like the Lean theorem prover:\n{output}"}

    return {"version": output, "error": None}


def _contains_placeholder(code: str) -> bool:
    return PLACEHOLDER_PATTERN.search(_strip_line_comments(code)) is not None


def _strip_line_comments(code: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in code.splitlines())


def _sanitize_output(output: str, proof_file: Path) -> str:
    if not output:
        return ""
    sanitized = output.replace(str(proof_file), PROOF_FILENAME)
    return re.sub(r"/[^\s:]+/proofpilot-lean-[^/\s:]+/" + re.escape(PROOF_FILENAME), PROOF_FILENAME, sanitized)


def _classify_diagnostics(
    *,
    status: str,
    stdout: str,
    stderr: str,
    error: str | None,
) -> dict[str, str]:
    output = "\n".join(part for part in [stdout, stderr, error or ""] if part)
    lowered = output.lower()

    if status == "verified":
        return {
            "category": "verified",
            "summary": "Lean accepted this proof.",
            "hint": "No repair is needed.",
        }
    if status == "tool_missing":
        return {
            "category": "tool_missing",
            "summary": "Lean is not available to run verification.",
            "hint": "Install Lean 4 with elan and ensure `lean` is on PATH.",
        }
    if status == "timeout":
        return {
            "category": "timeout",
            "summary": "Lean verification exceeded the time limit.",
            "hint": "Try simplifying the proof or increasing PROOFPILOT_VERIFY_TIMEOUT.",
        }
    if status == "rejected" or "sorry" in lowered or "admit" in lowered or "placeholder" in lowered:
        return {
            "category": "placeholder",
            "summary": "The proof contains a forbidden placeholder.",
            "hint": "Replace `sorry` or `admit` with a complete proof.",
        }
    if "unexpected token" in lowered or "expected command" in lowered or "expected" in lowered and "token" in lowered:
        return {
            "category": "syntax",
            "summary": "Lean found a syntax error.",
            "hint": "Check tactic syntax, punctuation, and whether the proof body is valid Lean.",
        }
    if "unsolved goals" in lowered or "goals" in lowered and "⊢" in output:
        return {
            "category": "unsolved_goal",
            "summary": "The proof ended before all goals were solved.",
            "hint": "Add proof steps that directly prove the remaining goal shown by Lean.",
        }
    if "type mismatch" in lowered or "application type mismatch" in lowered:
        return {
            "category": "type_mismatch",
            "summary": "A proof term or tactic produced the wrong type.",
            "hint": "Compare the expected type with the term Lean says was provided.",
        }
    if "tactic `rfl` failed" in lowered or "tactic 'rfl' failed" in lowered:
        return {
            "category": "likely_false_statement",
            "summary": "The statement does not appear provable by reflexivity, and may be false as written.",
            "hint": "Check whether the theorem statement is correct or whether an extra assumption is missing.",
        }
    if "unknown identifier" in lowered or "unknown constant" in lowered or "unknown declaration" in lowered:
        return {
            "category": "unknown_identifier",
            "summary": "Lean could not find a referenced name.",
            "hint": "Check spelling, imports, namespace qualification, or whether a needed lemma exists.",
        }
    if "failed to synthesize" in lowered or "typeclass" in lowered:
        return {
            "category": "missing_instance",
            "summary": "Lean could not synthesize a required instance.",
            "hint": "Add type annotations, imports, or required assumptions.",
        }
    if "invalid field notation" in lowered or "function expected" in lowered:
        return {
            "category": "wrong_shape",
            "summary": "The proof is using an expression as if it had a different shape.",
            "hint": "Inspect whether the value is a function, pair, proposition, or structure before applying it.",
        }
    return {
        "category": "likely_missing_lemma",
        "summary": "Lean rejected the proof, but the failure does not match a simple syntax or type category.",
        "hint": "A missing lemma, missing assumption, or stronger intermediate claim may be needed.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Lean code for ProofPilot.")
    parser.add_argument("--code-file", required=True, help="Path to a Lean source file.")
    parser.add_argument("--timeout", type=float, default=8.0, help="Verification timeout in seconds.")
    args = parser.parse_args()

    code = Path(args.code_file).read_text(encoding="utf-8")
    result = verify_lean(code, timeout_seconds=args.timeout)
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    main()
