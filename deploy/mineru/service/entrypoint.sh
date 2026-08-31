#!/bin/bash
# MinerU 解析服务启动入口：首次/每次启动预下载模型（幂等），然后启动 FastAPI。
set -e

# 模型预下载（modelscope snapshot_download 幂等：已存在会快速校验跳过）。
# 预下载完成后会写入 /root/magic-pdf.json（magic-pdf CLI 的模型目录配置）。
echo "[entrypoint] preloading MinerU models..."
python /app/preload_models.py

echo "[entrypoint] starting uvicorn on :8000"
exec uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
