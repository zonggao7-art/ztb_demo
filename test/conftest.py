# -*- coding: utf-8 -*-
"""pytest 全局夹具。

WP1.9（模型身份配置必填化，见 docs/嵌入模型与混合检索整改工作计划_20260901.md）：
Settings() 的模型身份配置（EMBEDDING_MODEL/LLM_MODEL/RERANKER_MODEL 及
凭据/端点）缺失即抛错。本夹具在环境未提供这些变量时统一注入测试值，
使测试套件在无 .env 的环境（如 CI）下也能离线运行——
测试显式配置等同于 .env 配置，不违反"未配置即抛错"口径。
"""
from __future__ import annotations

import os

import pytest

# 与 public_kb/config.py Settings._IDENTITY_VARS 保持一致
IDENTITY_ENV_VARS = (
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "RERANKER_MODEL",
)

_TEST_IDENTITY_ENV = {
    "EMBEDDING_MODEL": "BAAI/bge-m3",
    "EMBEDDING_API_KEY": "sk-test-embedding",
    "EMBEDDING_BASE_URL": "https://api.siliconflow.cn/v1",
    "LLM_MODEL": "z-ai/glm-5.3-flash",
    "LLM_API_KEY": "sk-test-llm",
    "LLM_BASE_URL": "https://openrouter.ai/api/v1",
    "RERANKER_MODEL": "BAAI/bge-reranker-v2-m3",
}


@pytest.fixture(autouse=True)
def _identity_env_defaults(monkeypatch):
    """注入缺失的身份配置变量（不覆盖环境中已有的值）。"""
    for key in IDENTITY_ENV_VARS:
        if not os.getenv(key):
            monkeypatch.setenv(key, _TEST_IDENTITY_ENV[key])
    yield
