"""配置中心收口守卫 — 防止绕过 Settings 直接读环境变量或绕过工厂直连客户端。

项目约定（2026-08 起）：
  1. 所有环境变量（os.getenv / os.environ / load_dotenv）只允许在
     public_kb/config.py 中读取 —— 运行时配置变更只应改 .env；
  2. ChatOpenAI 只允许在 public_kb/llm_factory.py 中构造 ——
     对话模型切换（DeepSeek → OpenRouter 等）只应改 .env；
  3. OpenAIEmbeddings / _SafeEmbeddings 只允许在
     public_kb/embedding_service.py 中构造。

本测试静态扫描 agent/ public_kb/ service/ 三个生产目录的源码。
诊断脚本（test/、scripts/、archive/）不在此列——它们是独立工具，
允许自读环境变量，但不得影响线上配置口径。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 唯一例外：配置中心自身
ENV_READ_ALLOWED = {PROJECT_ROOT / "public_kb" / "config.py"}
# 唯一允许构造 ChatOpenAI 的文件（LLM 工厂）
CHAT_LLM_ALLOWED = {PROJECT_ROOT / "public_kb" / "llm_factory.py"}
# 唯一允许构造 Embedding 客户端的文件（Embedding 工厂）
EMBEDDING_ALLOWED = {PROJECT_ROOT / "public_kb" / "embedding_service.py"}

ENV_READ_RE = re.compile(r"os\.getenv|os\.environ|load_dotenv")
CHAT_LLM_RE = re.compile(r"ChatOpenAI\s*\(|_SafeEmbeddings\s*\(")
EMBEDDING_RE = re.compile(r"OpenAIEmbeddings\s*\(|_SafeEmbeddings\s*\(")


def _iter_py_files():
    for rel in ("agent", "public_kb", "service"):
        root = PROJECT_ROOT / rel
        if not root.exists():
            continue
        yield from sorted(root.rglob("*.py"))


def _find_violations(pattern: re.Pattern, allowed: set) -> list[str]:
    violations = []
    for path in _iter_py_files():
        if path in allowed:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # 跳过整行注释（文档性提及不算构造/读 env）
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line):
                violations.append(
                    f"{path.relative_to(PROJECT_ROOT)}:{lineno}: {line.strip()[:120]}"
                )
    return violations


def test_env_reads_only_in_config_center():
    """生产代码中只有 public_kb/config.py 允许读环境变量。"""
    violations = _find_violations(ENV_READ_RE, ENV_READ_ALLOWED)
    assert not violations, (
        "发现绕过配置中心的环境变量读取（应改为消费 public_kb.config.Settings，"
        "或在 Settings 中新增字段）：\n" + "\n".join(violations)
    )


def test_chat_llm_construction_only_in_llm_factory():
    violations = _find_violations(CHAT_LLM_RE, CHAT_LLM_ALLOWED)
    assert not violations, (
        "对话模型必须经 public_kb.llm_factory.create_llm 构造（含 OpenRouter "
        "base_url 规范化），禁止散落直构 ChatOpenAI：\n" + "\n".join(violations)
    )


def test_embedding_construction_only_in_embedding_service():
    violations = _find_violations(EMBEDDING_RE, EMBEDDING_ALLOWED)
    assert not violations, (
        "Embedding 客户端必须经 public_kb.embedding_service.create_embeddings "
        "构造：\n" + "\n".join(violations)
    )
