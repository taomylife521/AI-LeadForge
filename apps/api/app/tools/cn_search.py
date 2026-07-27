# -*- coding: utf-8 -*-
"""
中国本土优先的真实网页搜索。

作用: 按 Bocha → Serper(CN) → Bing(zh-CN) 顺序调用真实搜索 API；无可用 Key 则失败，不伪造结果。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx


class SearchConfigError(RuntimeError):
    """未配置任何中国可用搜索 API Key。"""


class SearchRequestError(RuntimeError):
    """搜索 API 调用失败。"""


def _normalize_items(raw: list[dict[str, Any]], *, provider: str, limit: int) -> list[dict[str, Any]]:
    """统一搜索结果结构。"""

    items: list[dict[str, Any]] = []
    for row in raw:
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or row.get("link") or "").strip()
        snippet = str(row.get("snippet") or row.get("summary") or row.get("description") or "").strip()
        if not url:
            continue
        items.append(
            {
                "title": title or url,
                "url": url,
                "snippet": snippet,
                "provider": provider,
            }
        )
        if len(items) >= limit:
            break
    return items


async def _search_bocha(query: str, *, limit: int) -> Optional[list[dict[str, Any]]]:
    """博查 AI 搜索（中国本土）。"""

    key = (os.getenv("BOCHA_API_KEY") or os.getenv("BOCHAAI_API_KEY") or "").strip()
    if not key:
        return None
    url = (os.getenv("BOCHA_API_BASE") or "https://api.bochaai.com/v1/web-search").rstrip("/")
    payload = {
        "query": query,
        "freshness": "oneYear",
        "summary": True,
        "count": limit,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise SearchRequestError(f"Bocha HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    # 兼容多种返回结构
    web = (
        ((data.get("data") or {}).get("webPages") or {}).get("value")
        or (data.get("data") or {}).get("results")
        or data.get("webPages")
        or data.get("results")
        or []
    )
    rows = []
    for item in web if isinstance(web, list) else []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "title": item.get("name") or item.get("title"),
                "url": item.get("url") or item.get("displayUrl"),
                "snippet": item.get("snippet") or item.get("summary") or item.get("description"),
            }
        )
    return _normalize_items(rows, provider="bocha", limit=limit)


async def _search_serper_cn(query: str, *, limit: int) -> Optional[list[dict[str, Any]]]:
    """Serper Google 搜索，强制中国区与中文。"""

    key = (os.getenv("SERPER_API_KEY") or "").strip()
    if not key:
        return None
    payload = {
        "q": query,
        "gl": "cn",
        "hl": "zh-cn",
        "num": limit,
        "location": "China",
    }
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post("https://google.serper.dev/search", headers=headers, json=payload)
        if resp.status_code >= 400:
            raise SearchRequestError(f"Serper HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    organic = data.get("organic") or []
    rows = [
        {"title": r.get("title"), "url": r.get("link"), "snippet": r.get("snippet")}
        for r in organic
        if isinstance(r, dict)
    ]
    return _normalize_items(rows, provider="serper_cn", limit=limit)


async def _search_bing_cn(query: str, *, limit: int) -> Optional[list[dict[str, Any]]]:
    """Bing Web Search API，市场设为 zh-CN。"""

    key = (os.getenv("BING_SEARCH_API_KEY") or os.getenv("AZURE_BING_KEY") or "").strip()
    if not key:
        return None
    endpoint = (os.getenv("BING_SEARCH_ENDPOINT") or "https://api.bing.microsoft.com/v7.0/search").rstrip(
        "/"
    )
    headers = {"Ocp-Apim-Subscription-Key": key}
    params = {"q": query, "mkt": "zh-CN", "count": limit, "setLang": "zh-hans", "responseFilter": "Webpages"}
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.get(endpoint, headers=headers, params=params)
        if resp.status_code >= 400:
            raise SearchRequestError(f"Bing HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
    pages = ((data.get("webPages") or {}).get("value")) or []
    rows = [
        {"title": r.get("name"), "url": r.get("url"), "snippet": r.get("snippet")}
        for r in pages
        if isinstance(r, dict)
    ]
    return _normalize_items(rows, provider="bing_cn", limit=limit)


def build_china_search_queries(
    *,
    topic: str,
    industry_name: str = "",
    city_hint: str = "中国",
) -> list[str]:
    """
    构造本土化搜索词（商机验证/竞品/需求证据）。

    Args:
        topic: 商业主题。
        industry_name: 行业中文名。
        city_hint: 地域提示，默认全国中国市场。
    """

    base = (topic or "").strip() or (industry_name or "").strip() or "中国创业商机"
    industry = (industry_name or "").strip()
    loc = (city_hint or "中国").strip()
    queries = [
        f"{base} {industry} 市场 痛点 付费意愿 {loc}".strip(),
        f"{base} 竞品 商业模式 融资 OR 增长".strip(),
        f"{industry or base} 行业报告 OR 市场规模 OR 用户需求".strip(),
        f"{industry or base} 创业邦 OR 36氪 项目".strip(),
        f"{industry or base} 失败案例 OR 合规风险 OR 壁垒".strip(),
    ]
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


async def china_web_search(
    query: str,
    *,
    limit: int = 8,
) -> dict[str, Any]:
    """
    中国本土优先真实搜索。

    顺序: Bocha → Serper(CN) → Bing(zh-CN)。全部不可用则抛错，禁止空造结果。

    Args:
        query: 搜索词（建议中文）。
        limit: 返回条数。

    Returns:
        {provider, query, items[]}
    """

    query = (query or "").strip()
    if not query:
        raise ValueError("搜索词不能为空")
    limit = max(1, min(int(limit), 15))

    errors: list[str] = []
    for fn, name in (
        (_search_bocha, "bocha"),
        (_search_serper_cn, "serper_cn"),
        (_search_bing_cn, "bing_cn"),
    ):
        try:
            items = await fn(query, limit=limit)
        except SearchRequestError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if items is None:
            continue
        if not items:
            errors.append(f"{name}: 返回空结果")
            continue
        return {"provider": name, "query": query, "items": items, "errors": errors}

    if not any(os.getenv(k) for k in ("BOCHA_API_KEY", "BOCHAAI_API_KEY", "SERPER_API_KEY", "BING_SEARCH_API_KEY", "AZURE_BING_KEY")):
        raise SearchConfigError(
            "未配置中国可用搜索 Key。请在 .env 设置 BOCHA_API_KEY 或 SERPER_API_KEY 或 BING_SEARCH_API_KEY。"
        )
    raise SearchRequestError("所有中国搜索通道均失败: " + " | ".join(errors[:5]))
