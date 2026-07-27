# -*- coding: utf-8 -*-
"""
同域嵌入代理：把 TrendRadar / Paperclip Web UI 嵌进 LeadForge（去掉 X-Frame-Options）。

作用: 浏览器只访问 LeadForge，iframe 加载 /embed/*，无需新开标签跳转。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urljoin

import httpx
from fastapi import HTTPException, Request, Response

from app.settings import get_settings

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "content-length",
}


def _strip_frame_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
    """复制响应头并允许 iframe 嵌入。"""

    out: dict[str, str] = {}
    for k, v in headers:
        lk = k.lower()
        if lk in _HOP_BY_HOP:
            continue
        if lk in ("x-frame-options", "content-security-policy", "content-security-policy-report-only"):
            continue
        out[k] = v
    out["Content-Security-Policy"] = "frame-ancestors 'self'"
    return out


async def proxy_embed(request: Request, *, upstream_base: str, path: str) -> Response:
    """
    反向代理上游站点到同域路径。

    Args:
        request: FastAPI Request。
        upstream_base: 上游根 URL。
        path: 相对路径。

    Returns:
        代理后的 Response。
    """

    base = (upstream_base or "").strip().rstrip("/")
    if not base:
        raise HTTPException(503, "嵌入上游未配置")
    target = urljoin(base + "/", path or "")
    if request.url.query:
        target = f"{target}?{request.url.query}"

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            upstream = await client.request(
                request.method,
                target,
                headers=headers,
                content=body if body else None,
            )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"嵌入代理失败: {exc}") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_strip_frame_headers(upstream.headers.items()),
        media_type=upstream.headers.get("content-type"),
    )


def trendradar_web_base() -> str:
    """TrendRadar Web UI 地址。"""

    return (get_settings().trendradar_web_url or "").strip().rstrip("/")


def paperclip_web_base() -> str:
    """Paperclip Web UI 地址。"""

    return (get_settings().paperclip_base_url or "").strip().rstrip("/")
