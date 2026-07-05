from __future__ import annotations

import unittest

from backend.db import ProofAttempt
from backend.repair import build_local_repair_candidate, build_repair_prompt, build_retry_prompt


class RepairTests(unittest.TestCase):
    def test_local_repair_uses_rfl_for_simple_equality(self) -> None:
        attempt = _attempt("example : 1 = 1 := by exact 2")
        self.assertEqual(
            build_local_repair_candidate(attempt),
            "example : 1 = 1 := by\n  rfl",
        )

    def test_local_repair_handles_simple_conjunction(self) -> None:
        code = "example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by exact hp"
        attempt = _attempt(code)
        self.assertEqual(
            build_local_repair_candidate(attempt),
            "example (p q : Prop) (hp : p) (hq : q) : p ∧ q := by\n  exact And.intro hp hq",
        )

    def test_prompts_include_diagnostics(self) -> None:
        attempt = _attempt("example : 1 = 1 := by exact 2")
        prompt = build_repair_prompt(attempt)
        self.assertIn("Lean verifier diagnostics", prompt)
        self.assertIn("bad proof", prompt)
        self.assertIn("Category-specific repair guidance", prompt)

        retry = build_retry_prompt(
            base_prompt=prompt,
            failed_candidate="example : 1 = 1 := by\n  exact 2",
            verification_result={
                "stdout": "still bad",
                "classification": {"category": "type_mismatch"},
            },
            round_index=1,
        )
        self.assertIn("Previous repair candidate round 1 failed", retry)
        self.assertIn("still bad", retry)
        self.assertIn("Compare expected and actual types", retry)

    def test_prompt_guidance_uses_classification(self) -> None:
        attempt = _attempt(
            "example : 1 = 1 := by 2",
            classification={"category": "syntax"},
        )
        prompt = build_repair_prompt(attempt)
        self.assertIn("category: syntax", prompt)
        self.assertIn("Prioritize fixing malformed Lean syntax", prompt)

    def test_local_repair_does_not_pretend_false_literal_equality_is_fixed(self) -> None:
        code = "example : 1 = 2 := by rfl"
        attempt = _attempt(
            code,
            classification={"category": "likely_false_statement"},
        )
        self.assertEqual(build_local_repair_candidate(attempt), code)


def _attempt(code: str, classification: dict[str, str] | None = None) -> ProofAttempt:
    return ProofAttempt(
        id="attempt",
        title="Test",
        statement=code,
        code=code,
        status="failed",
        created_at=0,
        result={"stdout": "bad proof", "elapsed_ms": 1, "classification": classification or {}},
    )


if __name__ == "__main__":
    unittest.main()
