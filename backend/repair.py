from __future__ import annotations

from backend.db import ProofAttempt
from backend.llm import CandidateResponse, get_repair_provider


def build_local_repair_candidate(attempt: ProofAttempt) -> str:
    source = attempt.code.strip() or attempt.statement.strip()
    if ":= by" not in source:
        return source

    theorem_header = source.split(":= by", 1)[0].rstrip()
    category = _category_from_result(attempt.result)
    if category in {"likely_missing_lemma", "likely_false_statement"} and _looks_like_impossible_literal_equality(theorem_header):
        return source

    if "p ∧ q" in theorem_header and "(hp : p)" in theorem_header and "(hq : q)" in theorem_header:
        return f"{theorem_header} := by\n  exact And.intro hp hq"

    return f"{theorem_header} := by\n  rfl"


def build_repair_candidate(attempt: ProofAttempt, prompt: str) -> CandidateResponse:
    provider = get_repair_provider()
    provider_error = None
    if provider is not None:
        response = provider.generate_repair_candidate(prompt)
        if response.code:
            return response
        provider_error = response.error or "Gemini returned no repair candidate."

    code = build_local_repair_candidate(attempt)
    return CandidateResponse(
        code=code,
        provider="local",
        model="deterministic-rfl",
        raw_text=code,
        error=provider_error,
    )


def build_repair_prompt(attempt: ProofAttempt) -> str:
    diagnostics = _diagnostics_from_result(attempt.result)
    classification = _classification_from_result(attempt.result)
    guidance = _repair_guidance(attempt.result)
    return "\n".join(
        [
            "You are repairing a Lean 4 proof.",
            "",
            "Goal:",
            "Return a corrected Lean 4 proof. Preserve the theorem statement unless the statement itself is inconsistent.",
            "",
            "Title:",
            attempt.title,
            "",
            "Theorem or exercise statement:",
            attempt.statement or "(not provided)",
            "",
            "Current Lean code:",
            "```lean",
            attempt.code,
            "```",
            "",
            "Lean verifier diagnostics:",
            "```text",
            diagnostics or "(Lean returned no diagnostics.)",
            "```",
            "",
            "ProofPilot failure classification:",
            classification,
            "",
            "Category-specific repair guidance:",
            guidance,
            "",
            "Repair checklist:",
            "- Explain the likely failure in one sentence.",
            "- Produce one complete Lean 4 replacement proof.",
            "- Avoid using `sorry`.",
            "",
            "Current deterministic repair candidate:",
            "```lean",
            build_local_repair_candidate(attempt),
            "```",
        ]
    )


def build_retry_prompt(
    *,
    base_prompt: str,
    failed_candidate: str,
    verification_result: dict[str, object],
    round_index: int,
) -> str:
    diagnostics = _diagnostics_from_result(verification_result)
    classification = _classification_from_result(verification_result)
    guidance = _repair_guidance(verification_result)
    return "\n".join(
        [
            base_prompt,
            "",
            f"Previous repair candidate round {round_index} failed Lean verification.",
            "",
            "Failed candidate:",
            "```lean",
            failed_candidate,
            "```",
            "",
            "Lean diagnostics for the failed candidate:",
            "```text",
            diagnostics or "(Lean returned no diagnostics.)",
            "```",
            "",
            "ProofPilot failure classification:",
            classification,
            "",
            "Category-specific repair guidance:",
            guidance,
            "",
            "Try a different complete Lean 4 proof. Return only the replacement proof.",
        ]
    )


def _diagnostics_from_result(result: dict[str, object]) -> str:
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    error = str(result.get("error") or "").strip()
    return "\n\n".join(part for part in [stderr, stdout, error] if part)


def _classification_from_result(result: dict[str, object]) -> str:
    classification = result.get("classification")
    if not isinstance(classification, dict):
        return "(unclassified)"
    category = str(classification.get("category") or "unknown")
    summary = str(classification.get("summary") or "").strip()
    hint = str(classification.get("hint") or "").strip()
    parts = [f"category: {category}"]
    if summary:
        parts.append(f"summary: {summary}")
    if hint:
        parts.append(f"hint: {hint}")
    return "\n".join(parts)


def _repair_guidance(result: dict[str, object]) -> str:
    category = _category_from_result(result)
    guidance = {
        "syntax": "Prioritize fixing malformed Lean syntax. Preserve the theorem header and replace only the invalid proof body when possible.",
        "unsolved_goal": "Inspect the remaining goal and provide tactics or proof terms that directly construct that goal.",
        "type_mismatch": "Compare expected and actual types. Replace the proof term with one whose type exactly matches the goal.",
        "placeholder": "Replace every placeholder with a complete proof. Do not use `sorry` or `admit`.",
        "unknown_identifier": "Check spelling, namespaces, and missing imports. Prefer core Lean names when possible.",
        "missing_instance": "Add required assumptions or type annotations rather than guessing unrelated lemmas.",
        "wrong_shape": "Inspect whether the expression is a function, proposition, pair, or structure before applying it.",
        "likely_false_statement": "Do not produce a cosmetic proof change. Explain that the statement may be false or missing assumptions.",
        "likely_missing_lemma": "Do not blindly try `rfl`. Identify whether the theorem is false, needs a missing assumption, or requires a nontrivial lemma.",
        "timeout": "Simplify the proof and avoid expensive search tactics.",
        "tool_missing": "Lean is unavailable, so explain the likely repair without claiming verification.",
        "verified": "No repair is required.",
    }
    return guidance.get(category, "Use the diagnostics to repair the smallest possible part of the proof.")


def _category_from_result(result: dict[str, object]) -> str:
    classification = result.get("classification")
    if isinstance(classification, dict):
        return str(classification.get("category") or "unknown")
    return "unknown"


def _looks_like_impossible_literal_equality(theorem_header: str) -> bool:
    compact = " ".join(theorem_header.split())
    return ": 1 = 2" in compact or ": 2 = 1" in compact
