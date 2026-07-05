from __future__ import annotations

import mimetypes
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT / "frontend"


def resolve_static_path(request_path: str) -> tuple[Path | None, str | None]:
    filename = "index.html" if request_path == "/" else request_path.lstrip("/")
    path = (FRONTEND_DIR / filename).resolve()
    frontend_root = FRONTEND_DIR.resolve()

    if path != frontend_root and frontend_root not in path.parents:
        return None, None
    if not path.exists() or not path.is_file():
        return None, None

    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return path, content_type
