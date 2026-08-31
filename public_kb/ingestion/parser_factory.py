# 功能：根据 Settings 选择 PDF 解析器（MinerUParser vs PdfRouter 三档路由）。
"""PDF 解析器工厂（L6 接线点）。

根据 settings.pdf_tiered_routing_enabled 选择：
  - 关闭（默认）→ MinerUParser（M1 全量 MinerU 行为）；
  - 开启 → PdfRouter（profile → classify → fast_text/MinerU → T4 装配）。

只依赖 Services + Transforms 子树，不引入 langchain_core，避免 RAG
引擎核心依赖污染 PDF 解析路径。
"""
from __future__ import annotations

import logging

from ..config import Settings
from ..services.mineru_parser import MinerUParser
from .transforms.pdf_router import PdfRouter

logger = logging.getLogger(__name__)


def build_pdf_parser(settings: Settings) -> MinerUParser | PdfRouter:
    """根据 settings.pdf_tiered_routing_enabled 选择 PDF 解析器。"""
    if settings.pdf_tiered_routing_enabled:
        logger.info("PDF 三档路由已启用 (pdf_tiered_routing_enabled=true)")
        return PdfRouter(settings)
    logger.info("PDF 三档路由未启用，回退 MinerUParser (M1 全量)")
    return MinerUParser(settings)
