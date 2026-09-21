"""Check the installed Windows runtime without starting services or calling models."""

import argparse
import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--content", action="store_true")
    parser.add_argument("--media-test", action="store_true")
    args = parser.parse_args()
    if sys.platform != "win32" or sys.maxsize <= 2**32:
        raise RuntimeError("This release supports Windows x64 only")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Python 3.11 is required; run scripts/setup.ps1")
    for module in ("fastapi", "uvicorn", "httpx", "pydantic", "pydantic_settings", "python_multipart"):
        importlib.import_module(module)
    if args.content:
        importlib.import_module("deepagents")
        importlib.import_module("langchain_openai")
    from app import runtime
    from app.media import self_check

    with sqlite3.connect(":memory:") as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1
    tools = runtime.media_tools()
    for name, path in tools.items():
        if not path:
            raise RuntimeError(
                f"{name} not found. Run setup.ps1 -FfmpegDirectory <directory containing both executables>"
            )
        result = subprocess.run(
            [path, "-version"], check=True, capture_output=True, timeout=15,
            text=True, encoding="utf-8", errors="replace",
        )
        print(result.stdout.splitlines()[0])
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise RuntimeError("Frontend build missing; run scripts/setup.ps1")
    runtime.DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="install-check-", dir=runtime.DATA) as folder:
        if args.media_test:
            output, _ = self_check(Path(folder), tools)
            assert output.stat().st_size > 0
            print("PASS: actual H.264/AAC MP4 generation and inspection")
    print(json.dumps({"runtime": "ready", "python": sys.version.split()[0], "data": str(runtime.DATA)}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, TypeError, RuntimeError, ImportError, AssertionError, subprocess.SubprocessError) as error:
        print(f"Environment check failed: {error}", file=sys.stderr)
        sys.exit(1)
