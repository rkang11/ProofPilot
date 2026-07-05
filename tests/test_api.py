from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.port = _free_port()
        env = os.environ.copy()
        env["PROOFPILOT_PORT"] = str(self.port)
        env["PROOFPILOT_DB"] = str(Path(self.tmp.name) / "api-test.sqlite3")
        env["GEMINI_API_KEY"] = ""
        self.process = subprocess.Popen(
            [sys.executable, "backend/server.py"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.base_url = f"http://127.0.0.1:{self.port}"
        _wait_for_health(self.base_url)

    def tearDown(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=5)
        self.tmp.cleanup()

    def test_health_metrics_and_reset(self) -> None:
        health = _get_json(f"{self.base_url}/health")
        self.assertEqual(health["status"], "ok")

        response = _post_json(
            f"{self.base_url}/api/proof-attempts",
            {
                "title": "API test",
                "statement": "example : 1 = 1 := by rfl",
                "code": "example : 1 = 1 := by rfl",
            },
        )
        self.assertEqual(response["status"], "verified")

        direct = _post_json(
            f"{self.base_url}/api/verify",
            {"code": "example : 1 = 1 := by rfl"},
        )
        self.assertEqual(direct["status"], "verified")
        self.assertEqual(direct["classification"]["category"], "verified")
        self.assertIn("Lean", direct["verifier_version"])

        assistant = _post_json(
            f"{self.base_url}/api/assist",
            {"prompt": "Prove that if p and q are true, then p and q is true.", "mode": "formalize"},
        )
        self.assertEqual(assistant["mode"], "formalize")
        self.assertEqual(assistant["provider"], "local")
        self.assertIn("And.intro", assistant["lean_code"])
        self.assertEqual(assistant["verification"]["status"], "verified")

        explanation = _post_json(
            f"{self.base_url}/api/assist",
            {"prompt": "Explain conjunction", "mode": "explain"},
        )
        self.assertEqual(explanation["mode"], "explain")
        self.assertEqual(explanation["lean_code"], "")
        self.assertEqual(explanation["verification"]["status"], "not_generated")

        metrics = _get_json(f"{self.base_url}/api/metrics")
        self.assertEqual(metrics["proofs"]["total"], 1)

        reset = _post_json(f"{self.base_url}/api/dev/reset", {})
        self.assertEqual(reset["status"], "reset")
        self.assertEqual(_get_json(f"{self.base_url}/api/metrics")["proofs"]["total"], 0)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_health(base_url: str) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if _get_json(f"{base_url}/health")["status"] == "ok":
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("API test server did not start.")


def _get_json(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))



if __name__ == "__main__":
    unittest.main()
