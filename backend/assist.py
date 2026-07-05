from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from backend.llm import TextResponse, get_text_provider
from worker.verify import verify_lean


MAX_ASSISTANT_RETRIES = max(0, int(os.environ.get("PROOFPILOT_ASSISTANT_RETRIES", "2")))

ASSISTANT_MODES = {
    "auto": "choose the most useful response style for the request",
    "explain": "explain a mathematical concept or proof idea in natural language",
    "sketch": "produce a proof sketch without requiring Lean",
    "formalize": "translate the problem into Lean and verify the generated code",
    "repair": "explain or repair existing Lean code when Lean context is supplied",
}


@dataclass
class AssistantAnswer:
    mode: str
    proof_sketch: str
    formalization_plan: str
    lean_code: str
    explanation: str
    provider: str
    model: str
    structured_explanation: dict[str, str] = field(default_factory=dict)
    guided_formalization: dict[str, str] = field(default_factory=dict)
    proof_flow: list[dict[str, str]] = field(default_factory=list)
    raw_text: str = ""
    provider_error: str | None = None
    attempted_provider: str | None = None
    attempted_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["structured_explanation"] = _normalize_structured_explanation(
            self.structured_explanation or _structured_explanation_from_answer(self)
        )
        payload["guided_formalization"] = _normalize_guided_formalization(
            self.guided_formalization or _guided_formalization_from_answer(self, payload["structured_explanation"])
        )
        payload["proof_flow"] = _normalize_proof_flow(
            self.proof_flow or _proof_flow_from_answer(self, payload["structured_explanation"])
        )
        return payload


def create_assistant_response(
    prompt: str,
    *,
    mode: str = "auto",
    lean_code_context: str = "",
) -> dict[str, Any]:
    prompt = prompt.strip()
    mode = _normalize_mode(mode)
    lean_code_context = lean_code_context.strip()
    provider = get_text_provider()

    if provider is None:
        answer = _local_assistant_response(prompt, mode, lean_code_context)
        verification = _verify_generated_code(answer.lean_code)
        retry_rounds: list[dict[str, Any]] = []
    else:
        response = provider.generate_text(_build_assistant_prompt(prompt, mode, lean_code_context))
        answer = _answer_from_llm_response(response, prompt, mode, lean_code_context)
        verification = _verify_generated_code(answer.lean_code)
        retry_rounds = _retry_with_verifier_feedback(
            provider,
            prompt,
            mode,
            lean_code_context,
            answer,
            verification,
        )
        if retry_rounds:
            final_round = retry_rounds[-1]
            answer = final_round["answer"]
            verification = final_round["verification"]

    payload = answer.to_dict()
    payload["verification"] = verification
    payload["missing_lemma_suggestions"] = _missing_lemma_suggestions(verification, answer.lean_code)
    payload["retry_count"] = len(retry_rounds)
    payload["retry_rounds"] = [_public_retry_round(round_payload) for round_payload in retry_rounds]
    return payload


def _build_assistant_prompt(user_prompt: str, mode: str, lean_code_context: str) -> str:
    lean_context = (
        f"\nExisting Lean context, if relevant:\n```lean\n{lean_code_context}\n```\n"
        if lean_code_context
        else ""
    )
    return f"""You are ProofPilot, a careful theorem-proving assistant.

The user may describe a math theorem, proof problem, or concept in ordinary language.
Help without requiring the user to know Lean.
Selected mode: {mode} ({ASSISTANT_MODES[mode]}).

Return exactly one JSON object with these string fields:
- mode: one of auto, explain, sketch, formalize, repair.
- proof_sketch: a concise natural-language proof sketch that a student can read without knowing Lean.
- formalization_plan: how the statement maps into Lean objects, variables, hypotheses, and theorem shape. Use plain language first, then Lean names.
- lean_code: one complete Lean 4 snippet when a compact formalization is reasonable. Use an empty string if the request is too ambiguous.
- explanation: a plain-English explanation of why the proof works. Do not merely restate the Lean code.
- structured_explanation: an object with string fields `assumptions`, `goal`, `strategy`, `lean_translation`, and `step_explanation`.
- guided_formalization: an object with string fields `cleaned_statement`, `assumptions`, `conclusion`, `lean_statement`, and `verification_expectation`.
- proof_flow: an array of 2-6 objects. Each object has string fields `label` and `detail`, showing the proof as a simple flow from assumptions to conclusion.

Rules:
- Do not use sorry or admit.
- Write for a smart beginner who may not know Lean.
- Avoid symbol-only explanations. If you mention `hp : p`, explain that `hp` is a name for evidence/proof that `p` is true.
- Explain the math idea before the Lean tactic or constructor.
- Prefer everyday wording like "to prove an and statement, prove both parts" before terms like constructor.
- Prefer small self-contained Lean examples using core Lean syntax.
- If the request is a concept question rather than a theorem, explain the concept and leave lean_code empty.
- If the user asks to prove or formalize something but does not give an exact statement, ask for the missing variables, assumptions, and conclusion instead of inventing a theorem.
- In explain mode, prioritize intuition and leave lean_code empty unless the user explicitly asks for Lean.
- In sketch mode, prioritize the proof argument and only include Lean if the formalization is obvious.
- In formalize mode, prioritize a complete Lean snippet and verification-friendly syntax.
- In repair mode, use the existing Lean context when supplied and return a corrected complete snippet if possible.
- If the problem is ambiguous, ask for the missing theorem details in explanation and leave lean_code empty.
- Do not include markdown fences around the JSON.
- Always fill every structured_explanation field, even if the value is "Not applicable".
- In structured_explanation:
  - assumptions: state what the user is allowed to use in ordinary language.
  - goal: state what must be shown in ordinary language.
  - strategy: explain the proof idea without assuming Lean knowledge.
  - lean_translation: translate the proof idea into Lean names and syntax, with brief definitions.
  - step_explanation: explain the generated code line by line or step by step.
- In guided_formalization:
  - cleaned_statement: rewrite the user's theorem in precise ordinary language.
  - assumptions: list available assumptions in ordinary language.
  - conclusion: state the exact conclusion.
  - lean_statement: provide the theorem header or complete Lean statement.
  - verification_expectation: say whether Lean code was generated and should verify, or what is missing.
- In proof_flow, keep labels short, such as "Assume", "Use rule", "Conclude".

User request:
{user_prompt}
{lean_context}
"""


def _build_assistant_retry_prompt(
    user_prompt: str,
    mode: str,
    lean_code_context: str,
    previous_answer: AssistantAnswer,
    verification: dict[str, Any],
) -> str:
    diagnostics = verification.get("stderr") or verification.get("stdout") or verification.get("error") or "(empty)"
    lean_context = (
        f"\nOriginal Lean context:\n```lean\n{lean_code_context}\n```\n"
        if lean_code_context
        else ""
    )
    return f"""You are ProofPilot, repairing your previous Lean formalization.

The user asked:
{user_prompt}

Selected mode: {mode} ({ASSISTANT_MODES[mode]}).
{lean_context}
Your previous Lean code failed verification:
```lean
{previous_answer.lean_code}
```

Lean verifier diagnostics:
```text
{diagnostics}
```

Return exactly one JSON object with these string fields:
- mode: one of auto, explain, sketch, formalize, repair.
- proof_sketch: concise natural-language proof sketch.
- formalization_plan: corrected Lean formalization plan.
- lean_code: one complete corrected Lean 4 snippet, or an empty string if the theorem is ambiguous.
- explanation: explain what changed and why.
- structured_explanation: an object with string fields `assumptions`, `goal`, `strategy`, `lean_translation`, and `step_explanation`.
- guided_formalization: an object with string fields `cleaned_statement`, `assumptions`, `conclusion`, `lean_statement`, and `verification_expectation`.
- proof_flow: an array of 2-6 objects with string fields `label` and `detail`.

Rules:
- Preserve the mathematical intent.
- Do not use sorry or admit.
- Write for a smart beginner who may not know Lean.
- Explain the math idea before the Lean syntax.
- Prefer small self-contained Lean examples using core Lean syntax.
- Do not include markdown fences around the JSON.
- Always fill every structured_explanation field.
"""


def _answer_from_llm_response(
    response: TextResponse,
    user_prompt: str,
    mode: str,
    lean_code_context: str,
) -> AssistantAnswer:
    if response.error or not response.text.strip():
        fallback = _local_assistant_response(user_prompt, mode, lean_code_context)
        fallback.raw_text = response.text
        fallback.provider_error = response.error or "Gemini returned an empty response."
        fallback.attempted_provider = response.provider
        fallback.attempted_model = response.model
        return fallback

    parsed = _parse_json_object(response.text)
    if parsed is None:
        fallback = _local_assistant_response(user_prompt, mode, lean_code_context)
        fallback.raw_text = response.text
        fallback.provider_error = "Gemini response was not valid assistant JSON."
        fallback.attempted_provider = response.provider
        fallback.attempted_model = response.model
        return fallback

    return AssistantAnswer(
        mode=_normalize_mode(str(parsed.get("mode", mode))),
        proof_sketch=str(parsed.get("proof_sketch", "")).strip(),
        formalization_plan=str(parsed.get("formalization_plan", "")).strip(),
        lean_code=str(parsed.get("lean_code", "")).strip(),
        explanation=str(parsed.get("explanation", "")).strip(),
        structured_explanation=_structured_explanation_from_parsed(parsed),
        guided_formalization=_guided_formalization_from_parsed(parsed),
        proof_flow=_proof_flow_from_parsed(parsed),
        provider=response.provider,
        model=response.model,
        raw_text=response.text,
    )


def _retry_with_verifier_feedback(
    provider: Any,
    prompt: str,
    mode: str,
    lean_code_context: str,
    answer: AssistantAnswer,
    verification: dict[str, Any],
) -> list[dict[str, Any]]:
    retry_rounds: list[dict[str, Any]] = []
    current_answer = answer
    current_verification = verification

    for round_index in range(1, MAX_ASSISTANT_RETRIES + 1):
        if not _should_retry_generated_code(current_answer, current_verification):
            break

        response = provider.generate_text(
            _build_assistant_retry_prompt(
                prompt,
                mode,
                lean_code_context,
                current_answer,
                current_verification,
            ),
            temperature=0.1,
        )
        next_answer = _answer_from_llm_response(response, prompt, mode, lean_code_context)
        next_verification = _verify_generated_code(next_answer.lean_code)
        retry_rounds.append(
            {
                "round": round_index,
                "answer": next_answer,
                "verification": next_verification,
                "provider_error": next_answer.provider_error,
            }
        )
        current_answer = next_answer
        current_verification = next_verification

    return retry_rounds


def _should_retry_generated_code(answer: AssistantAnswer, verification: dict[str, Any]) -> bool:
    if answer.provider_error or not answer.lean_code.strip():
        return False
    return verification.get("status") in {"failed", "rejected", "timeout"}


def _public_retry_round(round_payload: dict[str, Any]) -> dict[str, Any]:
    answer = round_payload["answer"]
    verification = round_payload["verification"]
    return {
        "round": round_payload["round"],
        "provider": answer.provider,
        "model": answer.model,
        "verification_status": verification.get("status"),
        "provider_error": round_payload.get("provider_error"),
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidates = [text.strip()]
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    object_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _structured_explanation_from_parsed(parsed: dict[str, Any]) -> dict[str, str]:
    structured = parsed.get("structured_explanation")
    if isinstance(structured, dict):
        return _normalize_structured_explanation(
            {
                key: str(structured.get(key, "")).strip()
                for key in _structured_explanation_keys()
            }
        )
    return _normalize_structured_explanation(
        {
            "assumptions": "See the formalization plan.",
            "goal": str(parsed.get("proof_sketch", "")).strip() or "See the proof sketch.",
            "strategy": str(parsed.get("proof_sketch", "")).strip() or "Use the proof sketch.",
            "lean_translation": str(parsed.get("formalization_plan", "")).strip() or "See the generated Lean code.",
            "step_explanation": str(parsed.get("explanation", "")).strip() or "No step explanation was provided.",
        }
    )


def _structured_explanation_from_answer(answer: AssistantAnswer) -> dict[str, str]:
    if "And.intro hp hq" in answer.lean_code and "p ∧ q" in answer.lean_code:
        return _make_structured_explanation(
            assumptions=(
                "You are allowed to use two facts: `hp` is a proof that `p` is true, "
                "and `hq` is a proof that `q` is true."
            ),
            goal="Show that `p ∧ q` is true, meaning both `p` and `q` are true at the same time.",
            strategy=(
                "An `and` statement is proved by giving both halves. Use `hp` for the left half "
                "and `hq` for the right half."
            ),
            lean_translation=(
                "`p ∧ q` is Lean's notation for `p and q`. `And.intro hp hq` combines the proof "
                "of `p` and the proof of `q` into one proof of `p ∧ q`."
            ),
            step_explanation=(
                "The first line names the propositions and the two available proofs. The proof line "
                "says: build the conjunction by putting `hp` on the left and `hq` on the right."
            ),
        )

    assumptions = _infer_assumptions(answer.formalization_plan, answer.lean_code)
    return _make_structured_explanation(
        assumptions=assumptions,
        goal=answer.proof_sketch or "Understand the requested mathematical goal.",
        strategy=answer.proof_sketch or "Choose the proof rule that matches the goal shape.",
        lean_translation=answer.formalization_plan or "No Lean translation was generated.",
        step_explanation=answer.explanation or "No additional explanation was generated.",
    )


def _normalize_structured_explanation(values: dict[str, str]) -> dict[str, str]:
    return {
        key: values.get(key, "").strip() or "Not applicable."
        for key in _structured_explanation_keys()
    }


def _structured_explanation_keys() -> tuple[str, ...]:
    return ("assumptions", "goal", "strategy", "lean_translation", "step_explanation")


def _guided_formalization_keys() -> tuple[str, ...]:
    return ("cleaned_statement", "assumptions", "conclusion", "lean_statement", "verification_expectation")


def _guided_formalization_from_parsed(parsed: dict[str, Any]) -> dict[str, str]:
    guided = parsed.get("guided_formalization")
    if isinstance(guided, dict):
        return _normalize_guided_formalization(
            {key: str(guided.get(key, "")).strip() for key in _guided_formalization_keys()}
        )
    return _normalize_guided_formalization(
        {
            "cleaned_statement": str(parsed.get("proof_sketch", "")).strip(),
            "assumptions": "See the assumptions in the proof breakdown.",
            "conclusion": "See the proof goal.",
            "lean_statement": _first_lean_line(str(parsed.get("lean_code", "")).strip()),
            "verification_expectation": "Lean code was generated and sent to the verifier."
            if str(parsed.get("lean_code", "")).strip()
            else "No Lean code was generated because the request needs clarification or is explanation-only.",
        }
    )


def _guided_formalization_from_answer(answer: AssistantAnswer, structured: dict[str, str]) -> dict[str, str]:
    return _normalize_guided_formalization(
        {
            "cleaned_statement": answer.proof_sketch or "The theorem statement needs clarification.",
            "assumptions": structured.get("assumptions", ""),
            "conclusion": structured.get("goal", ""),
            "lean_statement": _first_lean_line(answer.lean_code),
            "verification_expectation": "Generated Lean should verify."
            if answer.lean_code.strip()
            else "No Lean was generated because this is explanation-only or underspecified.",
        }
    )


def _normalize_guided_formalization(values: dict[str, str]) -> dict[str, str]:
    return {
        key: values.get(key, "").strip() or "Not applicable."
        for key in _guided_formalization_keys()
    }


def _proof_flow_from_parsed(parsed: dict[str, Any]) -> list[dict[str, str]]:
    flow = parsed.get("proof_flow")
    if isinstance(flow, list):
        return _normalize_proof_flow(flow)
    structured = _structured_explanation_from_parsed(parsed)
    return _proof_flow_from_structured(structured)


def _proof_flow_from_answer(answer: AssistantAnswer, structured: dict[str, str]) -> list[dict[str, str]]:
    if "And.intro hp hq" in answer.lean_code and "p ∧ q" in answer.lean_code:
        return [
            {"label": "Given", "detail": "You have proof that `p` is true and proof that `q` is true."},
            {"label": "Use and-rule", "detail": "An `and` statement is proved by proving both parts."},
            {"label": "Conclude", "detail": "`And.intro hp hq` combines both proofs into `p ∧ q`."},
        ]
    if "h hp" in answer.lean_code:
        return [
            {"label": "Given", "detail": "You have an implication `p -> q` and a proof of `p`."},
            {"label": "Apply", "detail": "Use the implication like a function on the proof of `p`."},
            {"label": "Conclude", "detail": "`h hp` gives a proof of `q`."},
        ]
    if "cases h" in answer.lean_code:
        return [
            {"label": "Split", "detail": "A proof of `p or q` gives two possible cases."},
            {"label": "Left case", "detail": "If `p` is true, use the proof that `p` implies the goal."},
            {"label": "Right case", "detail": "If `q` is true, use the proof that `q` implies the goal."},
            {"label": "Conclude", "detail": "Both cases prove the same target."},
        ]
    return _proof_flow_from_structured(structured)


def _proof_flow_from_structured(structured: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"label": "Assumptions", "detail": structured.get("assumptions", "Identify what can be used.")},
        {"label": "Goal", "detail": structured.get("goal", "Identify what must be shown.")},
        {"label": "Main idea", "detail": structured.get("strategy", "Choose the proof rule that connects them.")},
        {"label": "Conclusion", "detail": structured.get("step_explanation", "Finish the proof.")},
    ]


def _normalize_proof_flow(flow: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in flow[:6]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label", "")).strip()
        detail = str(item.get("detail", "")).strip()
        if label or detail:
            normalized.append(
                {
                    "label": label or f"Step {len(normalized) + 1}",
                    "detail": detail or "No detail provided.",
                }
            )
    return normalized or [
        {"label": "Start", "detail": "Identify the assumptions and goal."},
        {"label": "Finish", "detail": "Apply the proof idea to reach the conclusion."},
    ]


def _first_lean_line(lean_code: str) -> str:
    return next((line.strip() for line in lean_code.splitlines() if line.strip()), "")


def _make_structured_explanation(
    *,
    assumptions: str,
    goal: str,
    strategy: str,
    lean_translation: str,
    step_explanation: str,
) -> dict[str, str]:
    return _normalize_structured_explanation(
        {
            "assumptions": assumptions,
            "goal": goal,
            "strategy": strategy,
            "lean_translation": lean_translation,
            "step_explanation": step_explanation,
        }
    )


def _infer_assumptions(formalization_plan: str, lean_code: str) -> str:
    if not formalization_plan.strip() and not lean_code.strip():
        return "No formal assumptions were provided."

    assumption_match = re.search(
        r"assumptions? as (?P<assumptions>.*?)(?:,\s+and\s+prove|,\s+then\s+|\.|$)",
        formalization_plan,
        flags=re.IGNORECASE,
    )
    if assumption_match:
        return f"Use {assumption_match.group('assumptions').strip()}."

    hypothesis_match = re.search(
        r"hypothesis as (?P<hypothesis>.*?)(?:,\s+the\s+object|,\s+and\s+prove|\.|$)",
        formalization_plan,
        flags=re.IGNORECASE,
    )
    if hypothesis_match:
        return f"Use {hypothesis_match.group('hypothesis').strip()}."

    if lean_code and re.search(r"example\s*:", lean_code):
        return "No extra assumptions are needed; the statement is closed."

    return "Use the variables and hypotheses named in the formalization plan."


def _verify_generated_code(lean_code: str) -> dict[str, Any]:
    if not lean_code.strip():
        return {
            "status": "not_generated",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "elapsed_ms": 0,
            "verifier": "lean",
            "verifier_version": None,
            "error": "No Lean code was generated for this request.",
        }
    return verify_lean(lean_code).to_dict()


def _missing_lemma_suggestions(verification: dict[str, Any], lean_code: str) -> list[str]:
    status = verification.get("status")
    if status in {"verified", "not_generated"}:
        return []

    classification = verification.get("classification") if isinstance(verification.get("classification"), dict) else {}
    category = str(classification.get("category", ""))
    output = "\n".join(
        str(verification.get(key, ""))
        for key in ("stdout", "stderr", "error")
        if verification.get(key)
    ).lower()
    code = lean_code.lower()

    if category == "unknown_identifier":
        return [
            "Check whether the referenced theorem, function, or variable is spelled correctly.",
            "If the name comes from a library, add the needed import or namespace qualification.",
            "If the name is meant to be an assumption, add it explicitly to the theorem statement.",
        ]
    if category == "unsolved_goal":
        suggestions = [
            "Look at the remaining goal after `⊢`; that is the exact statement still missing a proof.",
            "Add an intermediate lemma or hypothesis whose conclusion matches the remaining goal.",
        ]
        if "∧" in lean_code or " and " in code:
            suggestions.append("For an `and` goal, prove both sides with `And.intro`, `constructor`, or separate subgoals.")
        if "∨" in lean_code or " or " in code:
            suggestions.append("For an `or` assumption, split into cases with `cases`; for an `or` goal, choose left or right.")
        return suggestions
    if category == "type_mismatch":
        return [
            "Compare the type Lean expected with the type of the term you supplied.",
            "You may need a lemma that converts your current fact into the exact goal shape.",
            "If the goal is an implication, introduce the assumption before applying the proof.",
        ]
    if category == "likely_false_statement":
        return [
            "Check whether the theorem statement is true as written.",
            "If it should be true under extra conditions, add those assumptions to the statement.",
            "If both sides are meant to simplify to the same expression, add the rewriting or arithmetic lemma that makes them match.",
        ]
    if category == "missing_instance":
        return [
            "Add the typeclass assumption Lean needs, such as an order, algebraic structure, or decidability instance.",
            "Add type annotations so Lean knows which structure or operation you intend.",
        ]
    if "unknown" in output:
        return [
            "Lean could not find a name. Check spelling, imports, namespaces, or add the missing hypothesis.",
        ]
    return [
        "Identify the exact remaining goal or expected type from Lean's diagnostic.",
        "Add a lemma, assumption, or rewrite step whose conclusion matches that goal.",
        "If the statement itself is too strong, weaken it or add the missing hypotheses.",
    ]


def _local_assistant_response(prompt: str, mode: str, lean_code_context: str = "") -> AssistantAnswer:
    normalized = prompt.lower()
    if _needs_clarification(normalized, mode):
        return _local_clarification_response(prompt, mode)

    if _looks_like_concept_question(normalized, mode):
        return _local_concept_response(normalized, mode)

    if mode == "explain":
        return _local_concept_response(normalized, mode)

    if mode == "repair" and lean_code_context:
        repair_answer = _local_repair_response(prompt, lean_code_context)
        if repair_answer is not None:
            return repair_answer

    if _looks_like_reflexive_equality(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="A value is equal to itself by reflexivity.",
            formalization_plan="State the goal as a Lean equality and close it with `rfl`, Lean's reflexivity proof.",
            lean_code="" if mode == "sketch" else "example : 1 = 1 := by\n  rfl",
            explanation="The `rfl` tactic proves goals where both sides reduce to the same expression.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_iff_intro(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="To prove an if-and-only-if statement, prove each implication direction separately.",
            formalization_plan="Represent `p ↔ q` as two functions, `p -> q` and `q -> p`, then combine them with `Iff.intro`.",
            lean_code="" if mode == "sketch" else "example (p q : Prop) (hpq : p -> q) (hqp : q -> p) : p ↔ q := by\n  exact Iff.intro hpq hqp",
            explanation="`Iff.intro` constructs a biconditional from a proof of the forward direction and a proof of the backward direction.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_implication_elim(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="Use the implication proof as a function: apply the proof of `p -> q` to the proof of `p` to obtain `q`.",
            formalization_plan="Represent the implication as `h : p -> q`, the assumption as `hp : p`, and prove `q` with `h hp`.",
            lean_code="" if mode == "sketch" else "example (p q : Prop) (h : p -> q) (hp : p) : q := by\n  exact h hp",
            explanation="In Lean, an implication proof can be applied like a function. Since `h` turns proofs of `p` into proofs of `q`, `h hp` proves `q`.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_negation_contradiction(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="A contradiction follows from having both a proof of `p` and a proof that `p` is false.",
            formalization_plan="Represent negation as `hnp : ¬ p`; since `¬ p` means `p -> False`, apply `hnp` to `hp`.",
            lean_code="" if mode == "sketch" else "example (p : Prop) (hp : p) (hnp : ¬ p) : False := by\n  exact hnp hp",
            explanation="In Lean, `¬ p` unfolds to `p -> False`, so `hnp hp` proves `False` from contradictory assumptions.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_disjunction_cases(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="Use proof by cases on the disjunction: handle the `p` case and the `q` case separately, producing `r` in both.",
            formalization_plan="Represent the disjunction as `h : p ∨ q`, branch with `cases h`, and use the corresponding implication in each branch.",
            lean_code="" if mode == "sketch" else "example (p q r : Prop) (h : p ∨ q) (hp : p -> r) (hq : q -> r) : r := by\n  cases h with\n  | inl hp' => exact hp hp'\n  | inr hq' => exact hq hq'",
            explanation="A proof of `p ∨ q` gives either a proof of `p` or a proof of `q`. If both cases imply `r`, then `r` follows.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_conjunction_intro(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="Assume both propositions are true, then introduce a conjunction using those two proofs.",
            formalization_plan="Represent the propositions as `p q : Prop`, the assumptions as `hp : p` and `hq : q`, and prove `p ∧ q` with `And.intro hp hq`.",
            lean_code="" if mode == "sketch" else "example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by\n  exact And.intro hp hq",
            explanation="`And.intro` takes a proof of the left side and a proof of the right side, then constructs a proof of the conjunction.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_existential_intro(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="To prove an existential statement, provide a witness and then prove the predicate holds for that witness.",
            formalization_plan="Use `0` as the witness for `∃ n, P n`, together with the assumption `h : P 0`.",
            lean_code="" if mode == "sketch" else "example (P : Nat -> Prop) (h : P 0) : ∃ n, P n := by\n  exact Exists.intro 0 h",
            explanation="`Exists.intro 0 h` proves that there exists an `n` satisfying `P n` by choosing `n = 0` and giving `h : P 0`.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_forall_specialization(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="A universal statement applies to every object, so apply it to the particular object you care about.",
            formalization_plan="Represent the universal hypothesis as `h : ∀ x, P x`, the object as `a : α`, and prove `P a` with `h a`.",
            lean_code="" if mode == "sketch" else "example (α : Type) (P : α -> Prop) (h : ∀ x, P x) (a : α) : P a := by\n  exact h a",
            explanation="The hypothesis `h` is a proof that every `x` satisfies `P x`. Applying it to `a` gives a proof of `P a`.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_equality_rewrite(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="Use the equality hypothesis to rewrite one side of the goal into the other.",
            formalization_plan="Represent the equality as `h : a = b`, then use `rw [h]` to replace `a` with `b` in the goal.",
            lean_code="" if mode == "sketch" else "example (a b : Nat) (h : a = b) : a + 1 = b + 1 := by\n  rw [h]",
            explanation="The rewrite tactic uses `h : a = b` to replace occurrences of `a` with `b`, after which both sides match.",
            provider="local",
            model="assistant-fallback",
        )

    if _looks_like_simple_arithmetic(normalized):
        return AssistantAnswer(
            mode=mode,
            proof_sketch="Compute both sides of the arithmetic equality and observe that they reduce to the same numeral.",
            formalization_plan="State the concrete arithmetic equality in Lean and close it with `rfl` because both sides normalize to the same value.",
            lean_code="" if mode == "sketch" else _arithmetic_lean_code(normalized),
            explanation="Lean can evaluate this concrete natural-number expression, so reflexivity proves the normalized equality.",
            provider="local",
            model="assistant-fallback",
        )

    return AssistantAnswer(
        mode=mode,
        proof_sketch="Break the claim into assumptions, the desired conclusion, and the proof rule that connects them.",
        formalization_plan="This request needs a more precise theorem statement before ProofPilot can safely generate Lean.",
        lean_code="",
        explanation="Try stating the exact variables, assumptions, and conclusion. For example: `Given p and q are true, prove p and q`.",
        provider="local",
        model="assistant-fallback",
    )


def _normalize_mode(mode: str) -> str:
    return mode if mode in ASSISTANT_MODES else "auto"


def _local_concept_response(normalized: str, mode: str) -> AssistantAnswer:
    return AssistantAnswer(
        mode="explain" if mode == "auto" else mode,
        proof_sketch=_local_concept_strategy(normalized),
        formalization_plan="No Lean formalization is needed for this concept explanation. To formalize it, provide a precise theorem statement with variables, assumptions, and conclusion.",
        lean_code="",
        explanation=_local_explanation(normalized),
        structured_explanation=_make_structured_explanation(
            assumptions="No theorem assumptions were supplied; this is a concept explanation.",
            goal=_local_concept_goal(normalized),
            strategy=_local_concept_strategy(normalized),
            lean_translation="Lean is optional here. A formal version needs an exact theorem statement before verification.",
            step_explanation=_local_explanation(normalized),
        ),
        provider="local",
        model="assistant-fallback",
    )


def _local_clarification_response(prompt: str, mode: str) -> AssistantAnswer:
    return AssistantAnswer(
        mode=mode,
        proof_sketch="The request is missing the exact theorem shape, so ProofPilot should clarify before generating a proof.",
        formalization_plan="Please provide the variables, assumptions, and conclusion. For example: `Given n : Nat and h : Even n, prove Even (n + 2)`.",
        lean_code="",
        explanation=(
            "I need a precise statement before I can safely produce Lean. "
            "Tell me what variables or objects are involved, what assumptions are available, and exactly what conclusion should be proved."
        ),
        structured_explanation=_make_structured_explanation(
            assumptions="Missing. Specify the objects and hypotheses the proof may use.",
            goal="Missing. State the exact conclusion to prove.",
            strategy="Clarify the theorem first, then choose the proof rule or lemma that matches the goal.",
            lean_translation="Not generated because the theorem is underspecified.",
            step_explanation=(
                "The prompt was too broad to verify. A verifier-backed proof needs a concrete statement, not just a topic."
            ),
        ),
        provider="local",
        model="assistant-fallback",
    )


def _local_concept_goal(normalized: str) -> str:
    if "induction" in normalized:
        return "Understand how induction proves infinitely many natural-number cases using a base case and a step case."
    if "contradiction" in normalized:
        return "Understand how a contradiction proves a claim by showing the opposite assumption cannot hold."
    if "proof by cases" in normalized or "cases" in normalized or " or " in normalized:
        return "Understand how to split an argument into exhaustive alternatives and prove the goal in each branch."
    if "exists" in normalized or "existential" in normalized or "∃" in normalized:
        return "Understand why an existential proof needs a witness plus evidence that the witness works."
    if "for all" in normalized or "forall" in normalized or "∀" in normalized:
        return "Understand how universal statements apply to arbitrary or specific objects."
    if "and" in normalized or "conjunction" in normalized or "∧" in normalized:
        return "Understand how conjunctions package two separate proofs into one proof of `and`."
    return "Understand the main proof idea before translating it into formal Lean."


def _local_concept_strategy(normalized: str) -> str:
    if "induction" in normalized:
        return "Identify the base case, assume the claim for an arbitrary `n`, then prove it for `n + 1`."
    if "contradiction" in normalized:
        return "Assume the negation of the desired claim and derive `False` from that assumption."
    if "proof by cases" in normalized or "cases" in normalized or " or " in normalized:
        return "Split on the available alternatives and prove the same target in every branch."
    if "exists" in normalized or "existential" in normalized or "∃" in normalized:
        return "Choose a concrete witness, then prove that the predicate holds for that witness."
    if "for all" in normalized or "forall" in normalized or "∀" in normalized:
        return "Introduce an arbitrary object when proving a universal claim, or apply the universal hypothesis to a chosen object when using one."
    if "and" in normalized or "conjunction" in normalized or "∧" in normalized:
        return "Prove the left side and the right side separately, then combine them."
    return "Separate the assumptions from the goal, then choose the proof principle that connects them."


def _local_explanation(normalized: str) -> str:
    if "induction" in normalized:
        return "Induction proves a statement for all natural numbers by proving a base case, usually `0`, and a step case showing that if the statement holds for `n`, then it also holds for `n + 1`."
    if "exists" in normalized or "existential" in normalized or "∃" in normalized:
        return "An existential proof needs two pieces: a witness and a proof that the witness satisfies the requested property."
    if "or" in normalized or "disjunction" in normalized or "∨" in normalized:
        return "A disjunction proof represents alternatives. To use `p or q`, prove the goal once assuming `p`, and again assuming `q`."
    if "not" in normalized or "negation" in normalized or "contradiction" in normalized or "¬" in normalized:
        return "Negation `not p` means `p -> False`. A contradiction is produced by combining a proof of `p` with a proof of `not p`."
    if "if and only if" in normalized or "iff" in normalized or "↔" in normalized:
        return "An if-and-only-if proof has two directions: prove the forward implication and the backward implication, then combine them."
    if "implies" in normalized or "implication" in normalized or "->" in normalized or "→" in normalized:
        return "An implication `p -> q` means that any proof of `p` can be transformed into a proof of `q`. In Lean, implication proofs behave like functions."
    if "for all" in normalized or "forall" in normalized or "∀" in normalized or "quantifier" in normalized:
        return "A universal statement proves something for every object. To use it, provide a specific object, and it gives the corresponding specific proof."
    if "and" in normalized or "∧" in normalized or "conjunction" in normalized:
        return "A conjunction means proving two claims at once. To prove `p and q`, you separately prove `p`, separately prove `q`, and then combine those proofs."
    if "rewrite" in normalized:
        return "Equality rewriting replaces one expression with an equal expression inside the goal or hypotheses."
    if "arithmetic" in normalized or "1 + 1" in normalized or "one plus one" in normalized:
        return "Concrete arithmetic goals are often proved by computation: Lean reduces both sides to normal forms, and `rfl` closes the equality if they match."
    if "equal" in normalized or "=" in normalized:
        return "Equality proofs often show that two expressions reduce to the same value. In Lean, reflexive equalities are usually handled by `rfl`."
    return "Describe the theorem's objects, assumptions, and conclusion. Once those are explicit, ProofPilot can turn the reasoning into a proof sketch or Lean formalization."


def _looks_like_concept_question(normalized: str, mode: str) -> bool:
    if mode == "formalize" or mode == "repair":
        return False
    asks_for_concept = (
        normalized.startswith("explain")
        or normalized.startswith("what is")
        or normalized.startswith("what are")
        or normalized.startswith("how does")
        or normalized.startswith("how do")
        or "intuition" in normalized
        or "concept" in normalized
    )
    asks_to_prove = _asks_to_prove(normalized)
    return asks_for_concept and not asks_to_prove


def _needs_clarification(normalized: str, mode: str) -> bool:
    if mode == "explain" or mode == "repair":
        return False
    if not _asks_to_prove(normalized):
        return False
    if _has_known_formal_shape(normalized):
        return False
    broad_topic = any(
        topic in normalized
        for topic in (
            "something about",
            "a theorem about",
            "some theorem",
            "prime numbers",
            "groups",
            "continuity",
            "compactness",
            "derivatives",
            "topology",
            "real analysis",
            "number theory",
        )
    )
    return broad_topic or len(normalized.split()) < 8


def _asks_to_prove(normalized: str) -> bool:
    return any(word in normalized for word in ("prove", "show", "formalize", "verify"))


def _has_known_formal_shape(normalized: str) -> bool:
    return any(
        checker(normalized)
        for checker in (
            _looks_like_reflexive_equality,
            _looks_like_iff_intro,
            _looks_like_implication_elim,
            _looks_like_negation_contradiction,
            _looks_like_disjunction_cases,
            _looks_like_conjunction_intro,
            _looks_like_existential_intro,
            _looks_like_forall_specialization,
            _looks_like_equality_rewrite,
            _looks_like_simple_arithmetic,
        )
    )


def _local_repair_response(prompt: str, lean_code_context: str) -> AssistantAnswer | None:
    combined = f"{prompt}\n{lean_code_context}".lower()
    if "1 = 1" in combined:
        return AssistantAnswer(
            mode="repair",
            proof_sketch="The intended equality is reflexive, so the proof should close with reflexivity.",
            formalization_plan="Keep the same `example : 1 = 1` statement and replace the invalid proof body with `rfl`.",
            lean_code="example : 1 = 1 := by\n  rfl",
            explanation="The repaired Lean code removes the malformed tactic and uses `rfl`, which proves reflexive equality.",
            provider="local",
            model="assistant-fallback",
        )
    if "∧" in combined or "and.intro" in combined or "p and q" in combined:
        return AssistantAnswer(
            mode="repair",
            proof_sketch="To prove a conjunction, provide the left proof and the right proof.",
            formalization_plan="Use `And.intro hp hq` for a goal shaped like `p ∧ q` with assumptions `hp : p` and `hq : q`.",
            lean_code="example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by\n  exact And.intro hp hq",
            explanation="The repaired proof constructs the conjunction from the two existing hypotheses.",
            provider="local",
            model="assistant-fallback",
        )
    return None


def _looks_like_reflexive_equality(normalized: str) -> bool:
    return "1 = 1" in normalized or ("equal" in normalized and "itself" in normalized)


def _looks_like_implication_elim(normalized: str) -> bool:
    mentions_implication = "implies" in normalized or "implication" in normalized or "->" in normalized or "→" in normalized
    mentions_assumption = "p is true" in normalized or "proof of p" in normalized or "hp" in normalized or "and p" in normalized
    mentions_goal = "prove q" in normalized or "show q" in normalized or "obtain q" in normalized
    return mentions_implication and mentions_assumption and mentions_goal


def _looks_like_iff_intro(normalized: str) -> bool:
    mentions_iff = "if and only if" in normalized or "iff" in normalized or "↔" in normalized
    mentions_directions = ("p implies q" in normalized and "q implies p" in normalized) or ("p -> q" in normalized and "q -> p" in normalized)
    return mentions_iff or mentions_directions


def _looks_like_conjunction_intro(normalized: str) -> bool:
    has_props = ("p" in normalized and "q" in normalized) or "both" in normalized
    mentions_and = " and " in normalized or "∧" in normalized or "conjunction" in normalized
    mentions_truth = "true" in normalized or "prove" in normalized
    mentions_implication = "implies" in normalized or "->" in normalized or "→" in normalized
    return has_props and mentions_and and mentions_truth and not mentions_implication


def _looks_like_negation_contradiction(normalized: str) -> bool:
    mentions_negation = "not p" in normalized or "¬ p" in normalized or "negation" in normalized
    mentions_contradiction = "contradiction" in normalized or "false" in normalized
    mentions_assumptions = "p is true" in normalized or "proof of p" in normalized or "hp" in normalized
    return mentions_negation and (mentions_contradiction or mentions_assumptions)


def _looks_like_disjunction_cases(normalized: str) -> bool:
    mentions_or = "p or q" in normalized or "p ∨ q" in normalized or "disjunction" in normalized
    mentions_cases = "case" in normalized or "cases" in normalized or "both cases" in normalized
    mentions_goal = "prove r" in normalized or "show r" in normalized or "to prove r" in normalized
    return mentions_or and (mentions_cases or mentions_goal)


def _looks_like_existential_intro(normalized: str) -> bool:
    mentions_exists = "exists" in normalized or "there exists" in normalized or "existential" in normalized or "∃" in normalized
    mentions_witness = "witness" in normalized or "p 0" in normalized or "choose 0" in normalized or "n = 0" in normalized
    return mentions_exists and mentions_witness


def _looks_like_forall_specialization(normalized: str) -> bool:
    mentions_forall = "for all" in normalized or "forall" in normalized or "∀" in normalized or "universal" in normalized
    mentions_specific = "specific" in normalized or "particular" in normalized or "a satisfies" in normalized or "prove p a" in normalized
    return mentions_forall and mentions_specific


def _looks_like_equality_rewrite(normalized: str) -> bool:
    mentions_rewrite = "rewrite" in normalized or "replace" in normalized
    mentions_equality = "a = b" in normalized or "equal" in normalized
    mentions_goal = "a + 1 = b + 1" in normalized or "same after adding one" in normalized
    return mentions_rewrite and mentions_equality or ("a = b" in normalized and mentions_goal)


def _looks_like_simple_arithmetic(normalized: str) -> bool:
    return (
        "1 + 1 = 2" in normalized
        or "one plus one equals two" in normalized
        or "one plus one is two" in normalized
        or "2 + 2 = 4" in normalized
        or "two plus two equals four" in normalized
    )


def _arithmetic_lean_code(normalized: str) -> str:
    if "2 + 2 = 4" in normalized or "two plus two equals four" in normalized:
        return "example : 2 + 2 = 4 := by\n  rfl"
    return "example : 1 + 1 = 2 := by\n  rfl"
