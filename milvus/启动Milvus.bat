@echo off
chcp 65001 >nul
title 启动 Milvus 向量数据库
echo ============================================================
echo   正在启动 Milvus 向量数据库 ...
echo   首次启动会下载镜像, 可能需要 3-5 分钟
echo ============================================================
echo.
cd /d "%~dp0"
docker compose up -d
echo.
echo ============================================================
echo   ✅ Milvus 启动完成！
echo.
echo   连接地址: localhost:19530
echo   Attu可视化: http://localhost:3000 (需单独安装)
echo.
echo   按任意键关闭此窗口 (不会停止Milvus) ...
echo ============================================================
pause >nul
