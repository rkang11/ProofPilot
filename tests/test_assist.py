from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.assist import create_assistant_response
from backend.llm import TextResponse


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


class FakeRetryProvider:
    name = "fake"
    model = "retry-test"

    def __init__(self) -> None:
        self.calls = 0

    def generate_text(self, prompt: str, **kwargs: object) -> TextResponse:
        self.calls += 1
        if self.calls == 1:
            text = """
            {
              "mode": "formalize",
              "proof_sketch": "Use reflexivity.",
              "formalization_plan": "Close the equality goal.",
              "lean_code": "example : 1 = 1 := by 2",
              "explanation": "This intentionally has a broken proof body."
            }
            """
        else:
            self.assert_retry_prompt(prompt)
            text = """
            {
              "mode": "formalize",
              "proof_sketch": "Use reflexivity.",
              "formalization_plan": "Close the equality goal with rfl.",
              "lean_code": "example : 1 = 1 := by\\n  rfl",
              "explanation": "The failed proof body was replaced with rfl."
            }
            """
        return TextResponse(text=text, provider=self.name, model=self.model)

    def assert_retry_prompt(self, prompt: str) -> None:
        if "Lean verifier diagnostics" not in prompt:
            raise AssertionError("retry prompt did not include verifier diagnostics")


class AssistantRetryTests(unittest.TestCase):
    def test_assistant_retries_failed_generated_lean(self) -> None:
        provider = FakeRetryProvider()
        with patch("backend.assist.get_text_provider", return_value=provider):
            response = create_assistant_response("Prove 1 equals 1.", mode="formalize")

        self.assertEqual(provider.calls, 2)
        self.assertEqual(response["verification"]["status"], "verified")
        self.assertEqual(response["retry_count"], 1)
        self.assertEqual(response["retry_rounds"][0]["verification_status"], "verified")
        self.assertIn("rfl", response["lean_code"])
        self.assert_has_structured_explanation(response)

    def test_local_assistant_covers_common_math_shapes(self) -> None:
        cases = [
            ("If p implies q and p is true, prove q.", "h hp"),
            ("Prove p iff q from p implies q and q implies p.", "Iff.intro"),
            ("For all x, P x is true. Use that to prove P a for a particular a.", "h a"),
            ("Prove that 1 + 1 = 2.", "1 + 1 = 2"),
            ("Given p is true and not p, prove a contradiction.", "hnp hp"),
            ("Given p or q, and both cases prove r, prove r.", "cases h"),
            ("Prove there exists n such that P n by using witness 0 and proof P 0.", "Exists.intro"),
            ("Rewrite using a = b to prove a + 1 = b + 1.", "rw [h]"),
            ("Prove that 2 + 2 = 4.", "2 + 2 = 4"),
        ]

        for prompt, expected_lean in cases:
            with self.subTest(prompt=prompt):
                response = create_assistant_response(prompt, mode="formalize")
                self.assertEqual(response["verification"]["status"], "verified")
                self.assertIn(expected_lean, response["lean_code"])
                self.assert_has_structured_explanation(response)
                self.assert_has_guided_formalization(response)
                self.assert_has_proof_flow(response)

    def test_local_explain_mode_returns_structured_explanation_without_lean(self) -> None:
        response = create_assistant_response("Explain proof by cases for p or q.", mode="explain")

        self.assertEqual(response["verification"]["status"], "not_generated")
        self.assertEqual(response["lean_code"], "")
        self.assert_has_structured_explanation(response)
        self.assert_has_guided_formalization(response)
        self.assert_has_proof_flow(response)

    def test_auto_concept_question_does_not_generate_lean(self) -> None:
        response = create_assistant_response("What is proof by contradiction?", mode="auto")

        self.assertEqual(response["mode"], "explain")
        self.assertEqual(response["verification"]["status"], "not_generated")
        self.assertEqual(response["lean_code"], "")
        self.assertIn("contradiction", response["explanation"].lower())
        self.assert_has_structured_explanation(response)
        self.assert_has_guided_formalization(response)
        self.assert_has_proof_flow(response)

    def test_underspecified_formalization_asks_for_clarification(self) -> None:
        response = create_assistant_response("Prove something about prime numbers.", mode="formalize")

        self.assertEqual(response["mode"], "formalize")
        self.assertEqual(response["verification"]["status"], "not_generated")
        self.assertEqual(response["lean_code"], "")
        self.assertIn("variables", response["explanation"].lower())
        self.assertIn("underspecified", response["structured_explanation"]["lean_translation"].lower())
        self.assert_has_structured_explanation(response)
        self.assert_has_guided_formalization(response)
        self.assert_has_proof_flow(response)

    def test_failed_generated_lean_includes_missing_lemma_suggestions(self) -> None:
        provider = FakeBrokenProvider()
        with patch("backend.assist.get_text_provider", return_value=provider):
            response = create_assistant_response("Generate a broken proof.", mode="formalize")

        self.assertEqual(response["verification"]["status"], "failed")
        self.assertGreater(len(response["missing_lemma_suggestions"]), 0)

    def assert_has_structured_explanation(self, response: dict[str, object]) -> None:
        structured = response.get("structured_explanation")
        self.assertIsInstance(structured, dict)
        assert isinstance(structured, dict)
        for key in STRUCTURED_EXPLANATION_KEYS:
            self.assertTrue(str(structured.get(key, "")).strip(), key)

    def assert_has_guided_formalization(self, response: dict[str, object]) -> None:
        guided = response.get("guided_formalization")
        self.assertIsInstance(guided, dict)
        assert isinstance(guided, dict)
        for key in GUIDED_FORMALIZATION_KEYS:
            self.assertTrue(str(guided.get(key, "")).strip(), key)

    def assert_has_proof_flow(self, response: dict[str, object]) -> None:
        flow = response.get("proof_flow")
        self.assertIsInstance(flow, list)
        assert isinstance(flow, list)
        self.assertGreater(len(flow), 0)
        for step in flow:
            self.assertIsInstance(step, dict)
            assert isinstance(step, dict)
            self.assertTrue(str(step.get("label", "")).strip())
            self.assertTrue(str(step.get("detail", "")).strip())


class FakeBrokenProvider:
    name = "fake"
    model = "broken-test"

    def generate_text(self, prompt: str, **kwargs: object) -> TextResponse:
        text = """
        {
          "mode": "formalize",
          "proof_sketch": "Try to prove the false equality.",
          "formalization_plan": "State a false equality.",
          "lean_code": "example : 1 = 2 := by\\n  rfl",
          "explanation": "This should fail.",
          "structured_explanation": {
            "assumptions": "No assumptions.",
            "goal": "Show 1 = 2.",
            "strategy": "Use reflexivity.",
            "lean_translation": "Use rfl.",
            "step_explanation": "rfl tries to close the equality."
          }
        }
        """
        return TextResponse(text=text, provider=self.name, model=self.model)


if __name__ == "__main__":
    unittest.main()
