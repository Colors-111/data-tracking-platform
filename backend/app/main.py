"""FastAPI 应用：/api/* 路由 + 前端 SPA 静态托管（frontend/dist 存在时）。"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import connect, init_db
from .api import jobs, movements, series, sources


@asynccontextmanager
async def lifespan(_app: FastAPI):
    conn = connect()
    init_db(conn)  # 保证新库自动建表+种子
    conn.close()
    yield


app = FastAPI(title="数据追踪平台", lifespan=lifespan)
for r in (sources.router, series.router, movements.router, jobs.router):
    app.include_router(r)

# frontend/dist 判断在模块导入时执行——容器内存在则挂载；本机无 dist 则跳过，API 测试不受影响
FE_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if FE_DIR.exists():
    # 占位 dist 只有 index.html 没有 assets/，StaticFiles(directory=不存在) 会抛错，需一并判断
    if (FE_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=FE_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        # 未知 API 路径返回 404——否则前端 fetch 写错 URL 拿到 HTML 200，静默失败难排查
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (FE_DIR / path).resolve()
        # 防 path traversal：解析后必须仍在 FE_DIR 内，否则回落 index.html
        if path and f.is_file() and f.is_relative_to(FE_DIR.resolve()):
            return FileResponse(f)
        return FileResponse(FE_DIR / "index.html")  # SPA fallback
