# -*- coding: utf-8 -*-
# 功能：预下载 MinerU(magic-pdf 1.3.12) 所需模型权重 + 生成 ~/magic-pdf.json。
"""
模型预下载脚本（等价于官方 magic_pdf-1.3.12 的 scripts/download_models.py）。

背景：服务器网络 Docker Hub 直连超时，官方 MinerU 镜像不可达；改用
magic-pdf[full]==1.3.12 pip 安装（阿里云镜像可达）。模型权重从 ModelScope
（modelscope.cn 服务器可直连）下载到持久化缓存目录，并生成 magic-pdf.json
指向权重与 layoutreader。

幂等：modelscope snapshot_download 已存在的文件会跳过/校验，可重复执行。

官方等价实现（magic_pdf-1.3.12-released/scripts/download_models.py）：
  snapshot_download('opendatalab/PDF-Extract-Kit-1.0', allow_patterns=mineru_patterns)
  snapshot_download('ppaanngggg/layoutreader')
  写 ~/magic-pdf.json（models-dir + layoutreader-model-dir）

版本对齐修正：PDF-Extract-Kit-1.0 仓库已为 MinerU 2.x 更新，OCR 检测模型
不再提供 ch_PP-OCRv3_det_infer.pth（改为 ch_PP-OCRv5 / Multilingual v3）。
magic-pdf 1.3.12 的 models_config.yml 仍要求 ch_PP-OCRv3_det_infer.pth。
同代架构（PP-OCRv3 DBNet）的多语言检测权重 Multilingual_PP-OCRv3_det_infer.pth
可等价替换，此处用软链补齐缺失文件名，避免改源码。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ.setdefault("MODELSCOPE_CACHE", "/root/.cache/modelscope")

# magic-pdf 1.3.12 默认 layout=DocLayout-YOLO、MFD=YOLOv8、MFR=unimernet_hf_small_2503、
# OCR=paddleocr_torch（ch / ch_server）。只下载中文所需权重，控制体积。
MINERU_PATTERNS = [
    "models/Layout/YOLO/*",
    "models/MFD/YOLO/*",
    "models/MFR/unimernet_hf_small_2503/*",
    "models/OCR/paddleocr_torch/*",
]

# 缺失的 det 文件名 -> 仓库中同架构等价权重（PP-OCRv3 DBNet 多语言版）
DET_SYMLINKS = {
    "ch_PP-OCRv3_det_infer.pth": "Multilingual_PP-OCRv3_det_infer.pth",
    "en_PP-OCRv3_det_infer.pth": "Multilingual_PP-OCRv3_det_infer.pth",
}

CONFIG_PATH = Path(os.path.expanduser("~")) / "magic-pdf.json"
TEMPLATE_URL = (
    "https://gcore.jsdelivr.net/gh/opendatalab/MinerU@master/magic-pdf.template.json"
)


def _download_json(url: str) -> dict:
    import requests

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _write_config(models_dir: str, layoutreader_dir: str) -> Path:
    try:
        data = _download_json(TEMPLATE_URL)
    except Exception:
        # 模板拉取失败时用最小可用配置兜底
        data = {
            "bucket_info": {"[default]": [None, None, None]},
            "config_version": "1.3.2",
        }
    # 模板可能缺以下关键项，逐个补齐（magic-pdf 缺失时会退回 cpu/layoutlmv3，
    # 后者又依赖未随 magic-pdf[full] 安装的 detectron2，导致推理起不来）。
    data["models-dir"] = models_dir
    data["layoutreader-model-dir"] = layoutreader_dir
    data.setdefault("device-mode", "cuda")
    data.setdefault("layout-config", {"model": "doclayout_yolo"})
    data.setdefault(
        "formula-config",
        {"mfd_model": "yolo_v8_mfd", "mfr_model": "unimernet_small", "enable": True},
    )
    data.setdefault("table-config", {"model": "rapid_table", "enable": False, "max_time": 400})
    CONFIG_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
    )
    return CONFIG_PATH


def _link_missing_det(models_dir: str) -> None:
    ocr_dir = Path(models_dir) / "OCR" / "paddleocr_torch"
    for missing, target in DET_SYMLINKS.items():
        link = ocr_dir / missing
        if link.exists() or link.is_symlink():
            continue
        target_path = ocr_dir / target
        if not target_path.exists():
            print(f"WARNING: symlink target missing: {target_path}", flush=True)
            continue
        link.symlink_to(target_path.name)  # 相对软链，同目录
        print(f"linked {missing} -> {target}", flush=True)


def main() -> int:
    from modelscope import snapshot_download

    print("downloading PDF-Extract-Kit-1.0 (pipeline models)...", flush=True)
    model_dir = snapshot_download(
        "opendatalab/PDF-Extract-Kit-1.0",
        allow_patterns=MINERU_PATTERNS,
    )
    print("downloading ppaanngggg/layoutreader ...", flush=True)
    layoutreader_dir = snapshot_download("ppaanngggg/layoutreader")

    models_dir = str(Path(model_dir) / "models")
    print(f"models_dir: {models_dir}", flush=True)
    print(f"layoutreader_model_dir: {layoutreader_dir}", flush=True)

    _link_missing_det(models_dir)

    config_path = _write_config(models_dir, layoutreader_dir)
    print(f"wrote config: {config_path}", flush=True)

    # 自检：关键权重必须存在
    required = [
        "Layout/YOLO/doclayout_yolo_docstructbench_imgsz1280_2501.pt",
        "MFD/YOLO/yolo_v8_ft.pt",
        "OCR/paddleocr_torch/ch_PP-OCRv4_rec_server_doc_infer.pth",
        "OCR/paddleocr_torch/ch_PP-OCRv5_rec_server_infer.pth",
    ]
    missing = [r for r in required if not (Path(models_dir) / r).exists()]
    if missing:
        print(f"WARNING: missing required weights: {missing}", flush=True)
        return 1
    print("preload complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
