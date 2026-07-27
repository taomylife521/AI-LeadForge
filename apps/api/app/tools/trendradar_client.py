# -*- coding: utf-8 -*-
"""
TrendRadar MCP 客户端（独立 sidecar，不 import GPL 源码）。

作用: 经 HTTP 调用 TrendRadar MCP 的 get_latest_news / get_trending_topics，
      归一化为 LeadForge hotspot 条目；失败时由上层回退免费热点源。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.settings import get_settings
from app.tools.hotspot_sources import _item


def _mcp_base_url() -> str:
    """返回 MCP 端点（可含 /mcp 后缀）。"""

    url = (get_settings().trendradar_mcp_url or "").strip().rstrip("/")
    return url or "http://127.0.0.1:3333/mcp"


def _parse_sse_or_json(text: str) -> Any:
    """
    解析 MCP HTTP 响应（JSON 或 SSE data 行）。

    Args:
        text: 原始响应体。

    Returns:
        解析后的对象；失败返回 None。
    """

    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    # SSE: data: {...}
    payloads: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            payloads.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    if not payloads:
        return None
    return payloads[-1] if len(payloads) == 1 else payloads


async def _mcp_call(tool_name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
    """
    调用 MCP tools/call（兼容 JSON-RPC 与部分 FastMCP HTTP 形态）。

    Args:
        tool_name: 工具名。
        arguments: 工具参数。

    Returns:
        result 内容（可能是 list/dict/str）。

    Raises:
        RuntimeError: 请求失败或无结果。
    """

    endpoint = _mcp_base_url()
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments or {}},
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        resp = await client.post(endpoint, json=payload, headers=headers)
        if resp.status_code >= 400:
            # 部分实现路径无 /mcp 后缀
            alt = endpoint[:-4] if endpoint.endswith("/mcp") else f"{endpoint}/mcp"
            if alt != endpoint:
                resp = await client.post(alt, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"TrendRadar MCP HTTP {resp.status_code}: {resp.text[:200]}")
        body = _parse_sse_or_json(resp.text)
        if body is None:
            raise RuntimeError("TrendRadar MCP 空响应")
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(str(body["error"])[:300])
        if isinstance(body, dict) and "result" in body:
            result = body["result"]
            # MCP content blocks
            if isinstance(result, dict) and isinstance(result.get("content"), list):
                texts = []
                for block in result["content"]:
                    if isinstance(block, dict) and block.get("type") == "text":
                        texts.append(str(block.get("text") or ""))
                joined = "\n".join(texts).strip()
                if joined.startswith("{") or joined.startswith("["):
                    try:
                        return json.loads(joined)
                    except json.JSONDecodeError:
                        return joined
                return joined or result
            return result
        return body


def _normalize_news_rows(raw: Any, *, limit: int) -> list[dict[str, Any]]:
    """把 MCP 返回结构压成 hotspot items。"""

    rows: list[Any] = []
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict):
        for key in ("items", "news", "data", "results", "list"):
            if isinstance(raw.get(key), list):
                rows = raw[key]
                break
        if not rows and raw.get("title"):
            rows = [raw]
    elif isinstance(raw, str):
        # 尝试从 markdown/文本抽标题行
        for line in raw.splitlines():
            m = re.match(r"^[\-\*\d\.]+\s+(.+)$", line.strip())
            if m:
                rows.append({"title": m.group(1)[:200]})
            if len(rows) >= limit:
                break

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or row.get("name") or row.get("topic") or "").strip()
        url = str(row.get("url") or row.get("link") or row.get("mobileUrl") or "").strip()
        if not title:
            continue
        key = url or title
        if key in seen:
            continue
        seen.add(key)
        platform = str(row.get("platform") or row.get("source") or row.get("provider") or "trendradar")
        rank = row.get("rank") or row.get("hot") or row.get("heat")
        try:
            heat = float(rank) if rank is not None else max(0.0, 100.0 - i)
        except (TypeError, ValueError):
            heat = max(0.0, 100.0 - i)
        snippet = str(row.get("snippet") or row.get("desc") or row.get("summary") or "")[:240]
        items.append(
            _item(
                title=title,
                url=url or f"trendradar://{platform}/{i}",
                snippet=snippet,
                provider=f"trendradar:{platform}",
                heat=heat,
                meta={"platform": platform, "raw_keys": list(row.keys())[:12]},
            )
        )
        if len(items) >= limit:
            break
    return items


async def check_trendradar() -> dict[str, Any]:
    """
    探测 TrendRadar MCP 是否可达。

    Returns:
        {ok, url, detail}
    """

    url = _mcp_base_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # 健康：根或 /mcp GET
            base = url[:-4] if url.endswith("/mcp") else url
            for candidate in (url, base, f"{base}/health", f"{base}/mcp"):
                try:
                    resp = await client.get(candidate)
                    if resp.status_code < 500:
                        return {"ok": True, "url": url, "detail": f"HTTP {resp.status_code} @ {candidate}"}
                except Exception:  # noqa: BLE001
                    continue
        return {"ok": False, "url": url, "detail": "unreachable"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "detail": str(exc)[:160]}


async def fetch_trendradar_hotspots(
    *,
    limit: int = 20,
    platforms: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    拉取 TrendRadar 最新热榜并归一化。

    Args:
        limit: 条数上限。
        platforms: 可选平台 id 列表。

    Returns:
        {items, sources_used, errors, generated_at, provider}

    Raises:
        RuntimeError: MCP 调用失败且无条目。
    """

    limit = max(1, min(int(limit or 20), 50))
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    args: dict[str, Any] = {"limit": limit, "include_url": True}
    if platforms:
        args["platforms"] = platforms

    try:
        raw = await _mcp_call("get_latest_news", args)
        items = _normalize_news_rows(raw, limit=limit)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"get_latest_news: {exc}")

    if len(items) < max(3, limit // 3):
        try:
            raw2 = await _mcp_call(
                "get_trending_topics",
                {"top_n": limit, "mode": "current", "extract_mode": "auto_extract"},
            )
            extra = _normalize_news_rows(raw2, limit=limit)
            seen = {x.get("url") for x in items}
            for row in extra:
                if row.get("url") not in seen:
                    items.append(row)
                    seen.add(row.get("url"))
                if len(items) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001
            errors.append(f"get_trending_topics: {exc}")

    if not items:
        raise RuntimeError("; ".join(errors) or "TrendRadar 无热点数据（请先跑爬虫 sidecar）")

    return {
        "items": items[:limit],
        "sources_used": ["trendradar_mcp"],
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "trendradar",
        "count": min(len(items), limit),
    }
