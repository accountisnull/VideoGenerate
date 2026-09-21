from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import runtime


@asynccontextmanager
async def lifespan(app):
    runtime.initialize()
    yield


app = FastAPI(title="VideoGenerate 本机环境", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def same_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and origin
        and urlparse(origin).netloc != request.headers.get("host")
    ):
        return JSONResponse({"detail": "仅允许本机页面同源操作"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {
        "application": "video-generate-local",
        "api": "ok",
        "database": "ok" if runtime.checks() is not None else "error",
        "worker": "online" if runtime.worker_online() else "offline",
        "media": {name: bool(path) for name, path in runtime.media_tools().items()},
        "production_ready": False,
        "blockers": [
            "第 1 人内容 HTTP 服务尚未接入，百炼模型与密钥尚未配置",
            "第 2 人配音 HTTP 服务尚未接入",
            "第 3 人数字人 HTTP 服务与人物形象模块尚未接入",
            "发布规则、抖音应用与账号授权尚未接入",
        ],
    }


@app.get("/api/checks")
def list_checks():
    return runtime.checks()


@app.post("/api/checks", status_code=202)
def create_check():
    if not runtime.worker_online():
        raise HTTPException(503, "独立 Worker 未在线，请运行启动脚本")
    return runtime.create_check()


@app.get("/api/checks/{check_id}/video")
def check_video(check_id: str):
    check = runtime.get_check(check_id)
    if not check or check["state"] != "succeeded" or not check["artifact"]:
        raise HTTPException(404, "没有可播放的自检文件")
    path = (runtime.DATA / check["artifact"]).resolve()
    if not path.is_relative_to(runtime.DATA / "assets") or not path.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type="video/mp4")


frontend = runtime.ROOT / "frontend" / "dist"
if frontend.exists():
    app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
