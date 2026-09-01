# 功能：通过 HTTP 调用远端 MinerU 解析服务（对齐部署补充方案 §4.1 协议）。
"""MinerU 远程解析客户端 — 调用服务器上的 FastAPI 解析服务。

与 MinerUParser（本地 magic-pdf CLI）互补：本类只依赖 HTTP 协议
（POST /parse、GET /health），不感知 MinerU 装在哪台机器。换部署位置只改
base_url，代码零改动（对齐部署补充方案 §4.2）。

协议（见 docs/execution_plans/pdf_tiered_server_deploy_supplement_20260831.md §4.1）：
  POST /parse    multipart/file 子 PDF + page_range? → {"markdown": ...}
  GET  /health   → {"status": "ok", "parser": "mineru", "parser_version": ...}
  Header: Authorization: Bearer <token>

缓存 key（本地/服务器双侧一致，见 §4.3）：
  md5(source_pdf_bytes) | page_range | parser_version
本类在本地也落一份缓存，避免同一子 PDF 重复上传。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)


class MinerUApiParser:
    """通过 HTTP 调用 MinerU 解析服务，把（子）PDF 解析为 Markdown。"""

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self._settings = settings
        self._base_url = (base_url or settings.mineru_api_base_url).rstrip("/")
        self._token = token if token is not None else settings.mineru_api_token
        self._timeout = (
            timeout if timeout is not None else settings.mineru_api_timeout
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    # ── 对外接口 ───────────────────────────────────────────

    def health(self) -> dict:
        """GET /health，返回服务状态（含 parser_version）。"""
        return self._request("GET", "/health")

    def parse(
        self,
        pdf_path: str | Path,
        *,
        page_range: Optional[str] = None,
    ) -> str:
        """POST /parse 上传（子）PDF，返回该范围的 Markdown。

        Args:
            pdf_path: 源 PDF 或子 PDF 的绝对路径。
            page_range: 可选页范围（透传给服务，同时参与本地缓存 key）。

        Returns:
            解析出的 Markdown 字符串。

        Raises:
            FileNotFoundError: PDF 文件不存在。
            RuntimeError: 未配置服务地址、服务不可达、解析失败或返回空。
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if not self._base_url:
            raise RuntimeError(
                "未配置 MinerU 解析服务地址（MINERU_API_BASE_URL / "
                "PDF_PARSE_BASE_URL）。请先完成服务器部署并在 .env 配置。"
            )

        parser_version = self._parser_version()
        cached = self._find_cached(pdf_path, page_range, parser_version)
        if cached is not None:
            logger.info("命中远程解析缓存: %s", pdf_path.name)
            return cached.read_text(encoding="utf-8")

        payload = self._request(
            "POST", "/parse", file_path=pdf_path, page_range=page_range
        )
        markdown = (payload or {}).get("markdown") or ""
        if not markdown.strip():
            raise RuntimeError(f"MinerU 服务返回空 Markdown: {pdf_path.name}")
        self._write_cache(pdf_path, page_range, parser_version, markdown)
        return markdown

    # ── 内部实现 ───────────────────────────────────────────

    def _parser_version(self) -> str:
        """从 /health 探测 parser_version（失败时用空串，不阻断解析）。"""
        try:
            return str(self._request("GET", "/health").get("parser_version") or "")
        except Exception:
            return ""

    def _request(
        self,
        method: str,
        path: str,
        *,
        file_path: Optional[Path] = None,
        page_range: Optional[str] = None,
    ) -> dict:
        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        try:
            if method == "GET":
                resp = httpx.get(url, headers=headers, timeout=self._timeout)
            else:
                file_bytes = Path(file_path).read_bytes()
                resp = httpx.post(
                    url,
                    headers=headers,
                    data={"page_range": page_range or ""},
                    files={
                        "file": (
                            Path(file_path).name,
                            file_bytes,
                            "application/pdf",
                        )
                    },
                    timeout=self._timeout,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"MinerU 服务不可达 ({url}): {exc}") from exc

        if resp.status_code != 200:
            raise RuntimeError(
                f"MinerU 服务返回 {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    def _cache_key(self, pdf_path: Path, page_range: Optional[str], version: str) -> str:
        # 用组合串再哈希一次得到纯 hex 文件名，避免 `|` 等在 Windows 文件系统非法。
        digest = hashlib.md5(pdf_path.read_bytes()).hexdigest()
        composite = f"{digest}|{page_range or ''}|{version}"
        return hashlib.md5(composite.encode("utf-8")).hexdigest()

    def _cache_dir(self) -> Path:
        return Path(self._settings.mineru_output_dir) / "_mineru_api_cache"

    def _find_cached(
        self, pdf_path: Path, page_range: Optional[str], version: str
    ) -> Optional[Path]:
        md = self._cache_dir() / f"{self._cache_key(pdf_path, page_range, version)}.md"
        return md if md.exists() else None

    def _write_cache(
        self,
        pdf_path: Path,
        page_range: Optional[str],
        version: str,
        markdown: str,
    ) -> None:
        cache_dir = self._cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        md = cache_dir / f"{self._cache_key(pdf_path, page_range, version)}.md"
        md.write_text(markdown, encoding="utf-8")
        # 落一份轻量元数据，便于排查（与 .md 同前缀）
        meta = {
            "source": str(pdf_path),
            "page_range": page_range,
            "parser_version": version,
            "bytes": md.stat().st_size,
        }
        md.with_suffix(".json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
