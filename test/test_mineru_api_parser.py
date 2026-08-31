# 功能：MinerU 远程解析客户端单元测试（httpx 打桩，不连真实服务）。
"""Tests for MinerUApiParser (HTTP client for remote MinerU service).

覆盖（对齐部署补充方案 §4.1 协议）：
  - 未配置服务地址时 fail-fast；
  - POST /parse 成功返回 Markdown 并落本地缓存；
  - 缓存命中时不重复 POST；
  - Authorization: Bearer 头与 page_range 透传；
  - 服务端非 200 抛 RuntimeError。
"""

from __future__ import annotations

import pytest

import public_kb.services.mineru_api_parser as parser_mod
from public_kb.config import Settings
from public_kb.services.mineru_api_parser import MinerUApiParser


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def json(self):
        return self._json


class FakeHttpx:
    """替身 httpx 模块：记录调用，可编程返回。"""

    HTTPError = Exception

    def __init__(self):
        self.calls = []
        self.health_json = {"status": "ok", "parser_version": "2.0.0"}
        self.parse_json = {"markdown": "# 第一条 正文\n"}

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        return FakeResponse(200, self.health_json)

    def post(self, url, headers=None, data=None, files=None, timeout=None):
        self.calls.append(("POST", url, headers, data, files))
        return FakeResponse(200, self.parse_json)


@pytest.fixture
def fake_httpx(monkeypatch):
    fake = FakeHttpx()
    monkeypatch.setattr(parser_mod, "httpx", fake)
    return fake


def _settings(tmp_path, base_url="http://8.153.82.13:8002", token="sekret"):
    s = Settings()
    s.mineru_api_base_url = base_url
    s.mineru_api_token = token
    s.mineru_output_dir = str(tmp_path)
    return s


def test_parse_requires_base_url(tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path, base_url=""))
    with pytest.raises(RuntimeError, match="未配置"):
        parser.parse(pdf)


def test_parse_success_and_cache(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path))

    md1 = parser.parse(pdf, page_range="12-18")
    assert md1 == "# 第一条 正文\n"
    assert any(call[0] == "POST" for call in fake_httpx.calls)

    # 第二次命中本地缓存，不再 POST
    fake_httpx.calls.clear()
    md2 = parser.parse(pdf, page_range="12-18")
    assert md2 == md1
    assert all(call[0] != "POST" for call in fake_httpx.calls)


def test_authorization_header_and_page_range(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    parser = MinerUApiParser(_settings(tmp_path))
    parser.parse(pdf, page_range="5-9")

    post_call = next(c for c in fake_httpx.calls if c[0] == "POST")
    _, url, headers, data, files = post_call
    assert headers["Authorization"] == "Bearer sekret"
    assert data["page_range"] == "5-9"
    assert files["file"][0] == "sub.pdf"


def test_server_error_raises(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    fake_httpx.post = lambda url, headers=None, data=None, files=None, timeout=None: FakeResponse(500, {}, "boom")
    parser = MinerUApiParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="500"):
        parser.parse(pdf)


def test_empty_markdown_raises(fake_httpx, tmp_path):
    pdf = tmp_path / "sub.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake bytes")
    fake_httpx.parse_json = {"markdown": ""}
    parser = MinerUApiParser(_settings(tmp_path))
    with pytest.raises(RuntimeError, match="空 Markdown"):
        parser.parse(pdf)
