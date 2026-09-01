# 功能：调用本地 MinerU CLI 把 PDF 解析为 Markdown。
"""
MinerU PDF 解析器 — 通过 subprocess 调用本地 magic-pdf 命令行。

约定：
- 本地已安装 GPU 版 MinerU，magic-pdf 命令可直接调用
- 解析输出为标准 Markdown 文件，保留完整标题层级
- 解析结果缓存到 config.mineru_output_dir，支持断点续跑
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from ..config import Settings

logger = logging.getLogger(__name__)


class MinerUParser:
    """封装 magic-pdf 命令行调用，将 PDF 解析为 Markdown。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # 确保输出目录存在
        Path(self._settings.mineru_output_dir).mkdir(parents=True, exist_ok=True)

    def parse(self, pdf_path: str | Path) -> str:
        """解析单个 PDF，返回 Markdown 文本。

        Args:
            pdf_path: PDF 文件绝对路径。

        Returns:
            完整保留标题层级的 Markdown 字符串。

        Raises:
            FileNotFoundError: PDF 文件不存在。
            RuntimeError: magic-pdf 执行失败。
        """
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        # 检查缓存 —— 若已解析过则直接读取
        cached_md = self._find_cached_markdown(pdf_path)
        if cached_md is not None:
            logger.info("命中缓存，跳过解析: %s", pdf_path.name)
            return cached_md.read_text(encoding="utf-8")

        logger.info("开始解析 PDF: %s (大小: %.1f MB)",
                     pdf_path.name, pdf_path.stat().st_size / 1024 / 1024)

        try:
            result = subprocess.run(
                [
                    "magic-pdf",
                    "-p", str(pdf_path),
                    "-o", str(self._settings.mineru_output_dir),
                ],
                capture_output=True,
                text=True,
                timeout=self._settings.mineru_timeout,
                encoding="utf-8",
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()[-500:] if result.stderr else "无错误输出"
                raise RuntimeError(
                    f"magic-pdf 解析失败 (returncode={result.returncode}):\n{stderr}"
                )

            logger.info("magic-pdf 执行成功: %s", pdf_path.name)

        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"magic-pdf 解析超时 ({self._settings.mineru_timeout}s): {pdf_path.name}"
            )
        except FileNotFoundError:
            raise RuntimeError(
                "未找到 magic-pdf 命令。请确认 MinerU 已正确安装并加入 PATH。"
            )

        # 读取解析结果
        md_path = self._find_cached_markdown(pdf_path)
        if md_path is None:
            raise RuntimeError(
                f"magic-pdf 执行完毕但未找到输出 .md 文件: {pdf_path.name}\n"
                f"请检查输出目录: {self._settings.mineru_output_dir}"
            )

        return md_path.read_text(encoding="utf-8")

    def _find_cached_markdown(self, pdf_path: Path) -> Optional[Path]:
        """在输出目录中查找已缓存的 .md 文件。

        magic-pdf 通常在同名子目录中生成 .md，如:
            output_dir/pdf_stem/auto/pdf_stem.md
        """
        output_root = Path(self._settings.mineru_output_dir)
        stem = pdf_path.stem

        # 先尝试最常见的路径模式
        candidates = [
            output_root / stem / "auto" / f"{stem}.md",
            output_root / stem / f"{stem}.md",
            output_root / f"{stem}.md",
        ]
        for cand in candidates:
            if cand.exists():
                return cand

        # 广度搜索（兜底）
        for md in sorted(output_root.rglob(f"{stem}*.md")):
            return md

        return None
