from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import backend.db as db


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.tmp.name) / "proofpilot-test.sqlite3"
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_metrics_and_reset(self) -> None:
        db.create_attempt(
            attempt_id="attempt-1",
            title="Valid",
            statement="example : 1 = 1 := by rfl",
            code="example : 1 = 1 := by rfl",
            status="verified",
            result={"elapsed_ms": 20},
        )
        db.create_repair_attempt(
            repair_id="repair-1",
            proof_attempt_id="attempt-1",
            prompt="prompt",
            candidate_code="example : 1 = 1 := by rfl",
            provider="local",
            model="deterministic-rfl",
            round_index=1,
            status="candidate_verified",
            result={"elapsed_ms": 10},
        )

        metrics = db.get_metrics()
        self.assertEqual(metrics["proofs"]["total"], 1)
        self.assertEqual(metrics["proofs"]["verified"], 1)
        self.assertEqual(metrics["repairs"]["total"], 1)
        self.assertEqual(metrics["repairs"]["providers"], {"local": 1})

        reset = db.reset_dev_data()
        self.assertEqual(reset["proof_attempts_deleted"], 1)
        self.assertEqual(reset["repair_attempts_deleted"], 1)
        self.assertEqual(db.get_metrics()["proofs"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
