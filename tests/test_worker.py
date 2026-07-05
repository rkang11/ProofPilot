from __future__ import annotations

import unittest

from worker.verify import verify_lean


class WorkerTests(unittest.TestCase):
    def test_valid_lean_verifies(self) -> None:
        result = verify_lean("example : 1 = 1 := by\n  rfl", timeout_seconds=8)
        self.assertEqual(result.status, "verified")
        self.assertEqual(result.classification["category"], "verified")

    def test_invalid_lean_fails(self) -> None:
        result = verify_lean("example : 1 = 2 := by\n  rfl", timeout_seconds=8)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.classification["category"], "likely_false_statement")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("ProofPilotAttempt.lean", result.stdout)
        self.assertNotIn("/var/", result.stdout)

    def test_sorry_is_rejected(self) -> None:
        result = verify_lean("example : 1 = 2 := by\n  sorry", timeout_seconds=8)
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.classification["category"], "placeholder")
        self.assertIn("placeholder", result.error)
        self.assertIsNone(result.exit_code)

    def test_syntax_error_is_classified(self) -> None:
        result = verify_lean("example : 1 = 1 := by 2", timeout_seconds=8)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.classification["category"], "syntax")


if __name__ == "__main__":
    unittest.main()
