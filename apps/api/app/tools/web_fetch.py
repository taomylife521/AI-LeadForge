# -*- coding: utf-8 -*-
"""
真实网页抓取与正文抽取。

作用: HTTP 拉取 URL 并提取可读文本；失败明确报错，不伪造正文。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

import httpx


class FetchError(RuntimeError):
    """网页抓取失败。"""


_BLOCKED_HOST_HINTS = (
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
)


def _strip_html(html: str) -> str:
    """粗粒度去标签，保留中文正文。"""

    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?is)<!--.*?-->", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def fetch_url_text(
    url: str,
    *,
    max_chars: int = 6000,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """
    抓取单个 URL 的标题与正文摘要。

    Args:
        url: 目标地址（http/https）。
        max_chars: 正文截断长度。
        timeout: 超时秒数。

    Returns:
        {url, status, title, text, ok, error?}
    """

    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise FetchError(f"非法 URL: {url}")
    host = (urlparse(url).hostname or "").lower()
    if any(host == h or host.endswith(h) for h in _BLOCKED_HOST_HINTS):
        raise FetchError(f"拒绝抓取本地地址: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 LeadForgeBot/1.0"
        ),
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"请求失败 {url}: {exc}") from exc

    content_type = (resp.headers.get("content-type") or "").lower()
    if resp.status_code >= 400:
        return {
            "url": str(resp.url),
            "status": resp.status_code,
            "title": "",
            "text": "",
            "ok": False,
            "error": f"HTTP {resp.status_code}",
        }
    if "text/html" not in content_type and "text/plain" not in content_type and content_type:
        return {
            "url": str(resp.url),
            "status": resp.status_code,
            "title": "",
            "text": "",
            "ok": False,
            "error": f"不支持的 content-type: {content_type}",
        }

    raw = resp.text or ""
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else ""
    text = _strip_html(raw)[: max(500, int(max_chars))]
    if not text:
        return {
            "url": str(resp.url),
            "status": resp.status_code,
            "title": title,
            "text": "",
            "ok": False,
            "error": "正文为空",
        }
    return {
        "url": str(resp.url),
        "status": resp.status_code,
        "title": title,
        "text": text,
        "ok": True,
    }
