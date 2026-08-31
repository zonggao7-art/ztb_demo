# MinerU 解析服务镜像
#
# 基础镜像：daocloud 镜像源的 pytorch 2.3.1 + CUDA 12.1（服务器网络下 Docker Hub
# 直连 i/o timeout，已确认 docker.m.daocloud.io/pytorch/pytorch:2.3.1-cuda12.1-cudnn8-devel
# 可拉取，且自带 torch/torchvision，省去 2.5G 的 torch 重复下载）。
#
# MinerU 本体改用 pip 安装 magic-pdf（阿里云 PyPI 镜像可达，官方 MinerU 镜像在
# 可达镜像源上均不存在）。magic-pdf CLI 与 deploy/service/app.py 的调用约定一致：
#   magic-pdf -p <pdf> -o <outdir>  →  产出 <outdir>/<stem>/auto/<stem>.md
#
# 模型下载源走 hf-mirror.com（huggingface.co 在服务器网络不可达）。
#
# 自定义基础镜像时：
#   docker build --build-arg BASE_IMAGE=your-registry/pytorch:tag -t ztb-mineru-api:latest .
ARG BASE_IMAGE=docker.m.daocloud.io/pytorch/pytorch:2.3.1-cuda12.1-cudnn8-devel

FROM ${BASE_IMAGE}

# pip 走阿里云镜像；HF 走 hf-mirror；模型缓存目录与 compose 卷对齐
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/root/.cache/huggingface \
    MODELSCOPE_CACHE=/root/.cache/modelscope \
    PYTHONUNBUFFERED=1

# X11 相关共享库：magic-pdf 内 cv2 加载需要 libxcb/libGL 等，devel 基础镜像未带，
# 缺了会在推理启动时 ImportError: libxcb.so.1 / libGL.so.1。
RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        libgl1 libglib2.0-0 libxcb1 libxcb-render0 libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# magic-pdf 1.3.12（MinerU 经典 CLI 版）。基础镜像已含 torch 2.3.1 / torchvision，
# 满足 magic-pdf[full] 的 torch>=2.2.2,<3 约束，不会重下 torch。
# 注意：transformers 必须 <4.52，否则 torch.load 安全检查（CVE-2025-32434）会
# 在加载 layoutreader（.bin 权重）时抛 ValueError；4.51.2 验证可跑。
RUN pip install --no-cache-dir "magic-pdf[full]==1.3.12" "transformers==4.51.2"

# FastAPI 服务依赖（fastapi / uvicorn / python-multipart / modelscope）
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 服务代码 + 模型预下载脚本 + 启动入口
COPY app.py /app/app.py
COPY preload_models.py /app/preload_models.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh
WORKDIR /app

# 容器内固定 8000 端口（宿主机映射见 docker-compose.yml）
EXPOSE 8000

# 启动入口：先幂等预下载模型（modelscope），再起 uvicorn 单进程
ENTRYPOINT ["bash", "/app/entrypoint.sh"]
