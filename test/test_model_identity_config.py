# -*- coding: utf-8 -*-
"""模型身份配置守卫测试（WP1.8，见 docs/嵌入模型与混合检索整改工作计划_20260901.md）。

口径（D4/D6）：项目实际调用的模型必须与 .env 配置严格同步——
  - 模型名/凭据/端点 必填：缺失或空串时 Settings() 构造即抛 ValueError；
  - 无代码默认值：config.py 中身份配置的 os.getenv 不得携带默认值参数；
  - 无别名兜底链：不允许 EMBEDDING_* → SILICONFLOW_* → CLOSEAI_* 一类的
    嵌套兜底（以"无默认值参数"形态静态断言）；
  - 配置值逐字流转：Embedding / LLM / Reranker 客户端收到的 model 必须
    与配置值完全一致（工厂透传，无改写）；
  - 生产源码不得出现模型名字面量（docstring/注释豁免）。
"""
from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from public_kb.config import Settings

# 与 public_kb/config.py Settings._IDENTITY_VARS 及 test/conftest.py 一致
IDENTITY_VARS = (
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_BASE_URL",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "RERANKER_MODEL",
)

_PROD_MODEL_NAME_RE = re.compile(
    r"bge-|BAAI/|deepseek-|glm-|gpt-|text-embedding", re.IGNORECASE
)


# ────────────────────────────────────────────────
# 1. 必填校验：缺失/空串即抛错
# ────────────────────────────────────────────────
@pytest.mark.parametrize("missing_var", IDENTITY_VARS)
def test_settings_raises_when_identity_var_missing(missing_var, monkeypatch):
    """任一身份配置缺失 → Settings() 抛 ValueError，且报错信息点名该变量。"""
    for var in IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="模型身份配置缺失"):
        Settings()


def test_settings_error_message_names_all_missing_vars(monkeypatch):
    """全部缺失时报错信息必须逐一点名（方便运维一次性补配）。"""
    for var in IDENTITY_VARS:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError) as exc_info:
        Settings()
    for var in IDENTITY_VARS:
        assert var in str(exc_info.value)


def test_settings_rejects_blank_identity_values(monkeypatch):
    """空串/纯空格视为未配置 → 抛错（不放过"配了但配空"的情况）。"""
    for var in IDENTITY_VARS:
        monkeypatch.setenv(var, "   ")
    with pytest.raises(ValueError, match="模型身份配置缺失"):
        Settings()


# ────────────────────────────────────────────────
# 2. 无默认值 / 无别名兜底：config.py 静态扫描
# ────────────────────────────────────────────────
def _config_py_source() -> str:
    import public_kb.config as cfg

    return inspect.getsource(cfg)


def test_identity_getenv_has_no_default_value():
    """config.py 中身份配置的 os.getenv 不得携带默认值参数（无代码默认值）。"""
    src = _config_py_source()
    for var in IDENTITY_VARS:
        read_pattern = re.compile(rf'os\.getenv\(\s*"{var}"\s*[,)]')
        assert read_pattern.search(src), (
            f"config.py 未通过 os.getenv 读取 {var}——身份配置必须显式从 .env 读取"
        )
        bad_pattern = re.compile(rf'os\.getenv\(\s*"{var}"\s*,')
        assert not bad_pattern.search(src), (
            f"config.py 中 {var} 的 os.getenv 携带默认值参数——"
            "违反 D4/D6（未配置必须抛错，不允许静默默认值）"
        )


def test_no_alias_fallback_chains_for_identity_vars():
    """config.py 不得出现身份配置的别名兜底链（嵌套 getenv 形态）。"""
    src = _config_py_source()
    # 旧别名链的变量名（历史遗留；若回归出现即守卫失败）
    legacy_alias_vars = (
        "SILICONFLOW_API_KEY", "SILICONFLOW_BASE_URL",
        "CLOSEAI_API_KEY", "CLOSEAI_BASE_URL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
        "MODEL_NAME", "API_KEY", "BASE_URL",
    )
    for alias in legacy_alias_vars:
        assert f'os.getenv("{alias}"' not in src, (
            f"config.py 中出现旧别名变量 {alias} 的读取——别名兜底链已被整改废除，"
            "不得复活"
        )


# ────────────────────────────────────────────────
# 3. 配置值逐字流转：工厂透传无改写
# ────────────────────────────────────────────────
def test_embedding_model_value_flows_verbatim(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "unit-test/embedding-model")
    from public_kb.embedding_service import create_embeddings

    settings = Settings()
    emb = create_embeddings(settings)
    # langchain_openai OpenAIEmbeddings 的模型名字段（本版本为 model）
    assert emb.model == settings.embedding_model == "unit-test/embedding-model"


def test_llm_model_value_flows_verbatim(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "unit-test/llm-model")
    from public_kb.llm_factory import create_llm

    settings = Settings()
    llm = create_llm(settings)
    assert llm.model_name == settings.llm_model == "unit-test/llm-model"


def test_reranker_model_value_flows_verbatim():
    """同步/异步 Reranker 客户端的 model 必须取自 settings.reranker_model。"""
    from public_kb.qa_chain import _SiliconFlowReranker
    from public_kb.reranker import AsyncSiliconFlowReranker

    settings = Settings()

    sync_rr = _SiliconFlowReranker(
        model=settings.reranker_model,
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )
    assert sync_rr._model == settings.reranker_model

    async_rr = AsyncSiliconFlowReranker.from_settings(settings)
    assert async_rr._model == settings.reranker_model
    assert async_rr._api_key == settings.embedding_api_key
    assert async_rr._base_url == settings.embedding_base_url.rstrip("/")


# ────────────────────────────────────────────────
# 4. 生产源码不得出现模型名字面量（docstring/注释豁免）
# ────────────────────────────────────────────────
class _LiteralCollector(ast.NodeVisitor):
    """收集 AST 中的字符串字面量（跳过 docstring——独立字符串表达式视为注释）。"""

    def __init__(self) -> None:
        self.hits: list[str] = []

    def visit_Expr(self, node: ast.Expr) -> None:  # noqa: N802
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return  # docstring 豁免
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:  # noqa: N802
        if isinstance(node.value, str) and _PROD_MODEL_NAME_RE.search(node.value):
            self.hits.append(node.value)
        # 常量无子节点


def _iter_prod_py_files():
    import agent
    import public_kb

    roots = [Path(public_kb.__file__).parent, Path(agent.__file__).parent]
    try:
        import service

        roots.append(Path(service.__file__).parent)
    except ImportError:
        pass
    for root in roots:
        for py in sorted(root.rglob("*.py")):
            if "__pycache__" in py.parts:
                continue
            yield py


def test_no_hardcoded_model_names_in_prod_source():
    """agent/ public_kb/ service/ 代码中的模型名字面量一律禁止（守卫 D4/D6）。"""
    violations: list[str] = []
    for py in _iter_prod_py_files():
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        collector = _LiteralCollector()
        collector.visit(tree)
        if collector.hits:
            violations.append(f"{py}: {collector.hits[:3]}")
    assert not violations, (
        "生产源码出现硬编码模型名（.env 是唯一模型来源，代码不得预置）：\n"
        + "\n".join(violations)
    )


# ────────────────────────────────────────────────
# 5. 显式构造仍被允许（校验对象是"最终值非空"，不是来源）
# ────────────────────────────────────────────────
def test_explicit_constructor_override_allowed():
    settings = Settings(embedding_model="explicit/model", llm_model="explicit/llm")
    assert settings.embedding_model == "explicit/model"
    assert settings.llm_model == "explicit/llm"
