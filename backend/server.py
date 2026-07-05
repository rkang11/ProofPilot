from __future__ import annotations

import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.env import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from backend.db import (  # noqa: E402
    attempt_to_dict,
    create_attempt,
    get_metrics,
    get_attempt,
    init_db,
    list_attempts,
    list_repair_attempts,
    repair_to_dict,
    reset_dev_data,
)
from backend.assist import create_assistant_response  # noqa: E402
from backend.repair_service import create_repairs_for_attempt  # noqa: E402
from backend.static import resolve_static_path  # noqa: E402
from worker.verify import verify_lean  # noqa: E402


HOST = os.environ.get("PROOFPILOT_HOST", "127.0.0.1")
PORT = int(os.environ.get("PROOFPILOT_PORT", os.environ.get("PORT", "8000")))
MAX_REPAIR_ROUNDS = max(1, int(os.environ.get("PROOFPILOT_REPAIR_ROUNDS", "3")))


class ProofPilotHandler(BaseHTTPRequestHandler):
    server_version = "ProofPilot/0.1"

    def do_GET(self) -> None:
        if self.path == "/" or self.path in {"/app.js", "/styles.css"}:
            self._send_static_file(self.path)
            return

        if self.path == "/health":
            self._send_json({"status": "ok"})
            return

        if self.path == "/api/metrics":
            self._send_json(get_metrics())
            return

        if self.path == "/api/proof-attempts":
            attempts = list_attempts()
            self._send_json({"attempts": [attempt_to_dict(attempt) for attempt in attempts]})
            return

        repair_route = self._match_repair_route()
        if repair_route is not None:
            attempt_id = repair_route
            if get_attempt(attempt_id) is None:
                self._send_json({"error": "Proof attempt not found."}, status=404)
                return
            repairs = list_repair_attempts(attempt_id)
            self._send_json({"repairs": [repair_to_dict(repair) for repair in repairs]})
            return

        if self.path.startswith("/api/proof-attempts/"):
            attempt_id = self.path.rsplit("/", 1)[-1]
            attempt = get_attempt(attempt_id)
            if attempt is None:
                self._send_json({"error": "Proof attempt not found."}, status=404)
                return
            self._send_json(attempt_to_dict(attempt))
            return

        self._send_json({"error": "Route not found."}, status=404)

    def do_POST(self) -> None:
        if self.path == "/api/dev/reset":
            self._send_json({"status": "reset", **reset_dev_data()})
            return

        if self.path == "/api/verify":
            self._verify_code()
            return

        if self.path == "/api/assist":
            self._assist()
            return

        repair_route = self._match_repair_route()
        if repair_route is not None:
            self._create_repair(repair_route)
            return

        if self.path != "/api/proof-attempts":
            self._send_json({"error": "Route not found."}, status=404)
            return

        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        code = str(payload.get("code", "")).strip()
        statement = str(payload.get("statement", "")).strip()
        title = str(payload.get("title", "Untitled proof")).strip() or "Untitled proof"

        if not code:
            self._send_json({"error": "`code` is required."}, status=400)
            return

        result = verify_lean(code)
        attempt = create_attempt(
            attempt_id=str(uuid.uuid4()),
            title=title,
            statement=statement,
            code=code,
            status=result.status,
            result=result.to_dict(),
        )
        self._send_json(attempt_to_dict(attempt), status=201)

    def _verify_code(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        code = str(payload.get("code", "")).strip()
        if not code:
            self._send_json({"error": "`code` is required."}, status=400)
            return

        self._send_json(verify_lean(code).to_dict())

    def _assist(self) -> None:
        try:
            payload = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
            return

        prompt = str(payload.get("prompt", "")).strip()
        mode = str(payload.get("mode", "auto")).strip()
        lean_code_context = str(payload.get("lean_code_context", "")).strip()
        if not prompt:
            self._send_json({"error": "`prompt` is required."}, status=400)
            return

        self._send_json(
            create_assistant_response(
                prompt,
                mode=mode,
                lean_code_context=lean_code_context,
            )
        )

    def _create_repair(self, attempt_id: str) -> None:
        attempt = get_attempt(attempt_id)
        if attempt is None:
            self._send_json({"error": "Proof attempt not found."}, status=404)
            return

        if attempt.status == "verified":
            self._send_json({"error": "Verified proofs do not need repair."}, status=409)
            return

        self._send_json(create_repairs_for_attempt(attempt, MAX_REPAIR_ROUNDS), status=201)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            raise ValueError("Request body is required.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_static_file(self, request_path: str) -> None:
        path, content_type = resolve_static_path(request_path)
        if path is None or content_type is None:
            self._send_json({"error": "Route not found."}, status=404)
            return

        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _match_repair_route(self) -> str | None:
        parts = self.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "proof-attempts" and parts[3] == "repairs":
            return parts[2]
        return None


def run() -> None:
    init_db()
    try:
        server = ThreadingHTTPServer((HOST, PORT), ProofPilotHandler)
    except OSError as exc:
        if exc.errno == 48:
            print(
                f"Port {PORT} is already in use. Stop the existing server or run with "
                "`PROOFPILOT_PORT=8001 python3 backend/server.py`.",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
    print(f"ProofPilot API listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    run()
