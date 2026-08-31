# -*- coding: utf-8 -*-
"""MinerU 解析服务 — FastAPI 封装 magic-pdf。

协议（见部署补充方案 §4.1）：
  POST /parse    上传子 PDF（multipart file），返回该范围的 Markdown
  GET  /health   健康检查

缓存：按「上传字节 MD5 | page_range | parser_version」缓存到 MINERU_CACHE_DIR，
     命中直接返回，避免同一子 PDF 重复走 GPU。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI(title="ztb-mineru-api", version="0.1.0")

TOKEN = os.getenv("MINERU_API_TOKEN", "")
OUTPUT_DIR = Path(os.getenv("MINERU_OUTPUT_DIR", "/workspace/output"))
CACHE_DIR = Path(os.getenv("MINERU_CACHE_DIR", "/workspace/cache"))
UPLOAD_DIR = OUTPUT_DIR / "uploads"
MINERU_CMD = os.getenv("MINERU_CMD", "magic-pdf")
TIMEOUT = int(os.getenv("MINERU_TIMEOUT", "1800"))


def _probe_version() -> str:
    try:
        result = subprocess.run(
            [MINERU_CMD, "--version"], capture_output=True, text=True, timeout=30
        )
        return result.stdout.strip() or result.stderr.strip() or "unknown"
    except Exception:
        return "unknown"


PARSER_VERSION = os.getenv("MINERU_PARSER_VERSION", "").strip() or _probe_version()


def _find_output_md(output_root: Path, stem: str) -> Optional[Path]:
    """在 magic-pdf 输出目录中查找解析出的 Markdown（沿用本地 MinerUParser 逻辑）。"""
    candidates = [
        output_root / stem / "auto" / f"{stem}.md",
        output_root / stem / f"{stem}.md",
        output_root / f"{stem}.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    for md in sorted(output_root.rglob(f"{stem}*.md")):
        return md
    return None


def _authorize(authorization: Optional[str]) -> None:
    if TOKEN:
        expected = f"Bearer {TOKEN}"
        if authorization != expected:
            raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "parser": "mineru",
        "parser_version": PARSER_VERSION,
        "gpu_mode": os.getenv("MINERU_DEVICE_MODE", "cuda"),
    }


@app.post("/parse")
async def parse(
    file: UploadFile = File(...),
    page_range: Optional[str] = Form(default=None),
    authorization: Optional[str] = Header(default=None),
) -> JSONResponse:
    _authorize(authorization)

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")

    digest = hashlib.md5(raw).hexdigest()
    cache_key = f"{digest}|{page_range or ''}|{PARSER_VERSION}"
    cache_md = CACHE_DIR / f"{cache_key}.md"
    if cache_md.exists():
        return JSONResponse(
            {
                "markdown": cache_md.read_text(encoding="utf-8"),
                "page_range": page_range,
                "parser": "mineru",
                "parser_version": PARSER_VERSION,
                "warnings": [],
                "cached": True,
            }
        )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    work_dir = OUTPUT_DIR / f"run_{digest[:12]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_pdf = work_dir / f"{digest}.pdf"
    input_pdf.write_bytes(raw)

    try:
        result = subprocess.run(
            [MINERU_CMD, "-p", str(input_pdf), "-o", str(work_dir)],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            encoding="utf-8",
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()[-500:] if result.stderr else "no stderr"
            raise HTTPException(
                status_code=500,
                detail=f"magic-pdf failed (rc={result.returncode}): {stderr}",
            )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail=f"magic-pdf timeout after {TIMEOUT}s")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="magic-pdf not found in container")

    stem = input_pdf.stem
    md_path = _find_output_md(work_dir, stem)
    if md_path is None:
        raise HTTPException(status_code=500, detail="magic-pdf produced no markdown")

    markdown = md_path.read_text(encoding="utf-8")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_md.write_text(markdown, encoding="utf-8")

    # 清理本次运行中间产物（保留缓存与上传）
    shutil.rmtree(work_dir, ignore_errors=True)

    return JSONResponse(
        {
            "markdown": markdown,
            "page_range": page_range,
            "parser": "mineru",
            "parser_version": PARSER_VERSION,
            "warnings": [],
            "cached": False,
        }
    )
