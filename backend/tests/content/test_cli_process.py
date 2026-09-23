"""真实 CLI 与 MCP 子进程使用临时配置，普通测试不加载模型。"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def environment(tmp_path):
    return os.environ | {
        "SIMILARITY_MODEL_PATH": str(tmp_path / "not-loaded-model"),
        "SIMILARITY_MODEL_REVISION": "local-sha256:" + "a" * 64,
        "SIMILARITY_QDRANT_PATH": str(tmp_path / "qdrant"),
        "SIMILARITY_QDRANT_URL": "",
        "SIMILARITY_REQUIRED_CHECKS": "keywords,content_audit,similarity",
    }


def test_cli_initialize_and_reconcile_without_paid_call(tmp_path):
    env = environment(tmp_path)
    req = {"script": "测试稿", "job_id": str(uuid4()), "content_version_id": str(uuid4())}
    def command(action):
        return subprocess.run([sys.executable, "-X", "utf8", "-m", "app.content.cli", action],
            env=env, input=json.dumps(req), capture_output=True, text=True, encoding="utf-8",
            timeout=20, check=False)
    missing = command("reconcile")
    assert missing.returncode == 1
    assert json.loads(missing.stdout)["error"]["code"] == "INDEX_NOT_READY"
    assert command("init").returncode == 0
    result = command("reconcile")
    assert result.returncode == 0
    assert json.loads(result.stdout)["data"]["saved"] is False


def test_mcp_stdio_real_handshake_and_reconcile(tmp_path):
    env = environment(tmp_path)
    init = subprocess.run([sys.executable, "-X", "utf8", "-m", "app.content.cli", "init"],
                         env=env, capture_output=True, timeout=20, check=False)
    assert init.returncode == 0

    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-X", "utf8", "-m", "app.content.mcp_server"], env=env,
            cwd=str(Path.cwd()))
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("reconcile", arguments={"request": {
                "script": "测试稿", "job_id": str(uuid4()), "content_version_id": str(uuid4()),
            }})
            payload = json.loads(result.content[0].text)
            assert payload["success"] and payload["data"]["saved"] is False
    asyncio.run(asyncio.wait_for(scenario(), timeout=25))
