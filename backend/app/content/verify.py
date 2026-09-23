"""手动真实联调：先保存原请求和键，再提交/查询；自动测试不调用此入口。"""

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from app.contracts import ContentJob, ContentRequest

from .json_data import strict_json
from .settings import load_settings


def load_receipt(path: Path, base_url: str, topic: str | None) -> dict:
    if path.exists():
        data = strict_json(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("base_url") != base_url:
            raise ValueError("联调记录的服务地址不匹配")
        UUID(data["idempotency_key"])
        request = ContentRequest.model_validate(data["request"])
        if topic is not None and request.topic != topic.strip():
            raise ValueError("已有记录的主题不同，请使用新的记录文件")
        return data
    if not topic:
        raise ValueError("首次联调必须提供 --topic")
    request = ContentRequest(task_id=uuid4(), topic=topic)
    data = {
        "base_url": base_url,
        "idempotency_key": str(uuid4()),
        "request": request.model_dump(mode="json"),
        "job": None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # 创建记录早于网络请求；已有文件绝不覆盖为新的幂等键。
    with path.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    return data


def save_receipt(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + ".tmp-" + str(uuid4()))
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


async def verify(
    path: Path,
    base_url: str,
    topic: str | None,
    *,
    token: str | None,
    wait_seconds: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    parts = urlsplit(base_url)
    if (
        parts.scheme != "http"
        or parts.hostname not in ("127.0.0.1", "localhost", "::1")
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise ValueError("联调地址必须为本机 HTTP origin")
    if not math.isfinite(wait_seconds) or wait_seconds <= 0:
        raise ValueError("等待预算必须为正数且有限")
    base_url = base_url.rstrip("/")
    data = load_receipt(path, base_url, topic)
    previous = ContentJob.model_validate(data["job"]) if data.get("job") else None
    if previous is not None and str(previous.root.task_id) != data["request"]["task_id"]:
        raise ValueError("联调记录中的作业与原任务不匹配")
    if previous is not None and previous.root.state in ("succeeded", "failed"):
        print("已有终态记录，未重新调用：" + previous.root.state)
        return 0 if previous.root.state == "succeeded" else 1
    started = time.monotonic()
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with httpx.AsyncClient(
        transport=transport, trust_env=False, follow_redirects=False
    ) as client:
        job = previous
        while time.monotonic() - started < wait_seconds:
            remaining = wait_seconds - (time.monotonic() - started)
            request_headers = {**headers, "X-Request-ID": str(uuid4())}
            try:
                if job is None:
                    response = await client.post(
                        base_url + "/v1/content/jobs",
                        json=data["request"],
                        headers={
                            **request_headers,
                            "Idempotency-Key": data["idempotency_key"],
                        },
                        timeout=min(10, remaining),
                    )
                else:
                    response = await client.get(
                        base_url + f"/v1/content/jobs/{job.root.job_id}",
                        headers=request_headers,
                        timeout=min(10, remaining),
                    )
            except httpx.TransportError:
                print("连接失败或结果不明；保留原请求及幂等键，请用同一记录文件继续")
                return 2
            if response.status_code not in (200, 202):
                print(
                    f"HTTP {response.status_code}，未更换幂等键；请核实服务后使用同一记录继续"
                )
                return 2
            job = ContentJob.model_validate(response.json())
            if str(job.root.task_id) != data["request"]["task_id"] or (
                previous is not None and job.root.job_id != previous.root.job_id
            ):
                raise ValueError("服务响应与原任务或作业不匹配")
            previous = job
            data.update(
                job=job.model_dump(mode="json"),
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
            save_receipt(path, data)
            if job.root.state in ("succeeded", "failed"):
                print(f"内容作业 {job.root.job_id}：{job.root.state}；结果已保存")
                return 0 if job.root.state == "succeeded" else 1
            await asyncio.sleep(
                min(1, max(0, wait_seconds - (time.monotonic() - started)))
            )
    print("等待预算耗尽，不代表任务停止；使用同一记录文件继续查询")
    return 2


def main() -> None:
    parser = argparse.ArgumentParser(
        description="手动内容联调，显式运行可能产生模型费用"
    )
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--topic")
    parser.add_argument("--base-url", default="http://127.0.0.1:8761")
    parser.add_argument("--wait-seconds", type=float, default=300)
    args = parser.parse_args()
    try:
        settings = load_settings()
        result = asyncio.run(
            verify(
                args.receipt,
                args.base_url,
                args.topic,
                token=settings.token.get_secret_value() if settings.token else None,
                wait_seconds=args.wait_seconds,
            )
        )
    except (ValueError, OSError, KeyError, TypeError, ValidationError):
        print("配置、联调记录或服务响应无效；未自动建立新作业，请核对后重试")
        result = 2
    raise SystemExit(result)


if __name__ == "__main__":
    main()
