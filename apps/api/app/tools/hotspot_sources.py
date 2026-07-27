# -*- coding: utf-8 -*-
"""
免搜索 Key 的真实热点源。

作用: GitHub Trending/Search、Hacker News、36氪等创投 RSS — 公开接口/页面，不伪造。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

import httpx


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 LeadForgeHotspot/1.0"
)

# 中国创投 / 科技公开 RSS（官方或稳定公开源）
CN_VC_RSS_FEEDS: list[dict[str, str]] = [
    {"id": "36kr_feed", "name": "36氪综合", "url": "https://36kr.com/feed"},
    {"id": "36kr_article", "name": "36氪文章", "url": "https://36kr.com/feed-article"},
    {"id": "36kr_newsflash", "name": "36氪快讯", "url": "https://36kr.com/feed-newsflash"},
]


def _item(
    *,
    title: str,
    url: str,
    snippet: str = "",
    provider: str,
    heat: float = 0.0,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "title": (title or "").strip() or url,
        "url": (url or "").strip(),
        "snippet": (snippet or "").strip(),
        "provider": provider,
        "heat": float(heat),
        "meta": meta or {},
    }


async def fetch_github_trending(*, language: str = "", since: str = "daily", limit: int = 15) -> list[dict[str, Any]]:
    """
    抓取 GitHub Trending 公开页（无需 API Key）。

    Args:
        language: 语言过滤，如 python / typescript；空为全部。
        since: daily | weekly | monthly
        limit: 条数上限。
    """

    path = f"https://github.com/trending/{language}" if language else "https://github.com/trending"
    url = f"{path}?since={since}"
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html"}
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub Trending HTTP {resp.status_code}")
        html = resp.text

    # 解析 article.Box-row 中的仓库链接与描述、stars
    blocks = re.findall(
        r'(?is)<article[^>]*class="[^"]*Box-row[^"]*"[^>]*>(.*?)</article>',
        html,
    )
    items: list[dict[str, Any]] = []
    for block in blocks:
        m_repo = re.search(r'href="(/[^"/]+/[^"/]+)"', block)
        if not m_repo:
            continue
        repo_path = m_repo.group(1)
        if repo_path.count("/") != 2:
            # /user/repo
            continue
        title = repo_path.strip("/")
        if title.count("/") != 1:
            continue
        desc_m = re.search(r'(?is)<p[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>', block)
        desc = re.sub(r"<[^>]+>", " ", desc_m.group(1)).strip() if desc_m else ""
        desc = re.sub(r"\s+", " ", desc)
        stars_m = re.search(r'(?is)(\d[\d,]*)\s*</a>\s*</span>\s*<span[^>]*>\s*stars', block) or re.search(
            r'data-view-component="true"[^>]*>\s*([\d,]+)\s*</span>\s*stars today', block
        )
        # star 今日增量
        today_m = re.search(r"([\d,]+)\s+stars?\s+today", block, flags=re.I)
        heat = 0.0
        if today_m:
            heat = float(today_m.group(1).replace(",", "") or 0)
        elif stars_m:
            heat = float(stars_m.group(1).replace(",", "") or 0) / 1000.0
        items.append(
            _item(
                title=title,
                url=f"https://github.com{repo_path}",
                snippet=desc or f"GitHub Trending ({since})",
                provider="github_trending",
                heat=heat,
                meta={"since": since, "language": language or "all"},
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_github_search_repos(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """
    GitHub 仓库搜索公开 API。

    无 Token 匿名限额极低（约 10 次/小时）；配置 GITHUB_TOKEN / GH_TOKEN 后约 30 次/分钟。
    遇到 403 限流时抛出带 rate_limited 标记的错误，调用方应停止继续打 Search。
    """

    q = (query or "").strip()
    if not q:
        return []
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "q": q,
        "sort": "stars",
        "order": "desc",
        "per_page": max(1, min(limit, 20)),
    }
    async with httpx.AsyncClient(timeout=40.0) as client:
        resp = await client.get("https://api.github.com/search/repositories", headers=headers, params=params)
        if resp.status_code == 403 or resp.status_code == 429:
            remaining = resp.headers.get("X-RateLimit-Remaining", "?")
            reset = resp.headers.get("X-RateLimit-Reset", "")
            auth = "已认证" if token else "未认证(匿名)"
            hint = (
                "请在 .env 或控制台 Key 维护中配置 GITHUB_TOKEN"
                if not token
                else "请稍后再试或降低并发搜索"
            )
            raise RuntimeError(
                f"GitHub Search 限流({auth}, remaining={remaining}, reset={reset}): {hint}"
            )
        if resp.status_code >= 400:
            raise RuntimeError(f"GitHub Search HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
    items: list[dict[str, Any]] = []
    for row in data.get("items") or []:
        if not isinstance(row, dict):
            continue
        stars = float(row.get("stargazers_count") or 0)
        items.append(
            _item(
                title=str(row.get("full_name") or ""),
                url=str(row.get("html_url") or ""),
                snippet=str(row.get("description") or ""),
                provider="github_search",
                heat=stars,
                meta={
                    "stars": stars,
                    "language": row.get("language"),
                    "updated_at": row.get("updated_at"),
                    "topics": row.get("topics") or [],
                },
            )
        )
    return items


def github_search_authenticated() -> bool:
    """是否已配置 GitHub Search Token。"""

    return bool((os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip())


async def fetch_hn_search(query: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Hacker News Algolia 公开搜索（无需 Key）。"""

    q = (query or "").strip()
    if not q:
        return []
    url = "https://hn.algolia.com/api/v1/search"
    params = {"query": q, "tags": "story", "hitsPerPage": max(1, min(limit, 20))}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params, headers={"User-Agent": USER_AGENT})
        if resp.status_code >= 400:
            raise RuntimeError(f"HN Search HTTP {resp.status_code}")
        data = resp.json()
    items: list[dict[str, Any]] = []
    for row in data.get("hits") or []:
        if not isinstance(row, dict):
            continue
        link = str(row.get("url") or f"https://news.ycombinator.com/item?id={row.get('objectID')}")
        points = float(row.get("points") or 0)
        comments = float(row.get("num_comments") or 0)
        items.append(
            _item(
                title=str(row.get("title") or ""),
                url=link,
                snippet=f"HN points={int(points)} comments={int(comments)}",
                provider="hackernews",
                heat=points + comments * 0.3,
                meta={"points": points, "comments": comments, "created_at": row.get("created_at")},
            )
        )
    return items


def _parse_rss_xml(xml_text: str, *, provider: str, limit: int) -> list[dict[str, Any]]:
    """解析 RSS/Atom 文本。"""

    root = ET.fromstring(xml_text)
    ns = {"": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    items: list[dict[str, Any]] = []

    # RSS 2.0
    for node in root.findall(".//item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        desc = (node.findtext("description") or node.findtext("summary") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        pub = (node.findtext("pubDate") or "").strip()
        if not link:
            continue
        items.append(
            _item(
                title=title,
                url=link,
                snippet=desc[:400],
                provider=provider,
                heat=1.0,
                meta={"pubDate": pub},
            )
        )
        if len(items) >= limit:
            return items

    # Atom
    atom_ns = {"a": "http://www.w3.org/2005/Atom"}
    for node in root.findall(".//a:entry", atom_ns):
        title = (node.findtext("a:title", default="", namespaces=atom_ns) or "").strip()
        link_el = node.find("a:link", atom_ns)
        link = ""
        if link_el is not None:
            link = (link_el.get("href") or "").strip()
        summary = (node.findtext("a:summary", default="", namespaces=atom_ns) or "").strip()
        summary = re.sub(r"<[^>]+>", " ", summary)
        if not link:
            continue
        items.append(
            _item(
                title=title,
                url=link,
                snippet=summary[:400],
                provider=provider,
                heat=1.0,
                meta={},
            )
        )
        if len(items) >= limit:
            break
    return items


async def fetch_cn_vc_rss(*, keyword: str = "", limit_per_feed: int = 8) -> list[dict[str, Any]]:
    """
    拉取中国创投公开 RSS（36氪等），可按关键词过滤标题/摘要。

    无需 API Key。
    """

    keyword = (keyword or "").strip().lower()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"}
    all_items: list[dict[str, Any]] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True) as client:
        for feed in CN_VC_RSS_FEEDS:
            try:
                resp = await client.get(feed["url"], headers=headers)
                if resp.status_code >= 400:
                    errors.append(f"{feed['id']}: HTTP {resp.status_code}")
                    continue
                parsed = _parse_rss_xml(resp.text, provider=f"rss:{feed['id']}", limit=limit_per_feed * 2)
                for item in parsed:
                    blob = f"{item['title']} {item['snippet']}".lower()
                    if keyword and keyword not in blob and not any(
                        k in blob for k in keyword.replace(" ", "").split() if len(k) >= 2
                    ):
                        # 宽松：无关键词时全收；有关键词则包含即收
                        chars = [c for c in keyword if "\u4e00" <= c <= "\u9fff"]
                        if chars and not any(c in blob for c in chars):
                            continue
                    item["meta"] = {**(item.get("meta") or {}), "feed": feed["name"]}
                    # 标题含融资/AI/获客等提高热度
                    heat = 1.0
                    for w, w_heat in (
                        ("融资", 3),
                        ("种子", 2),
                        ("A轮", 3),
                        ("B轮", 3),
                        ("AI", 2),
                        ("大模型", 3),
                        ("获客", 2),
                        ("本地生活", 2),
                        ("到店", 2),
                    ):
                        if w.lower() in blob or w in item["title"]:
                            heat += w_heat
                    item["heat"] = heat
                    all_items.append(item)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{feed['id']}: {exc}")
    all_items.sort(key=lambda x: float(x.get("heat") or 0), reverse=True)
    # 附带错误不抛，交给上层决定
    return all_items[: max(1, limit_per_feed * len(CN_VC_RSS_FEEDS))]


def build_github_queries(topic: str, industry_name: str = "") -> list[str]:
    """为 GitHub 构造英文+中文相关检索词。"""

    topic = (topic or "").strip()
    industry = (industry_name or "").strip()
    base = " ".join(x for x in [topic, industry] if x)
    return [
        f"{base} marketplace OR saas OR startup",
        f"{topic} business model OR opportunity",
        f"{industry or topic} china OR 创业 OR 融资",
    ]


async def collect_free_hotspots(
    *,
    topic: str,
    industry_name: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """
    汇总免 Key 热点：GitHub + HN + 36氪 RSS + 创业邦 + 中国独立开发者。

    Returns:
        {items, sources_used, errors, generated_at}
    """

    from app.tools.cyzone import fetch_cyzone_projects

    topic = (topic or "").strip()
    industry_name = (industry_name or "").strip()
    errors: list[str] = []
    sources_used: list[str] = []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_all(rows: list[dict[str, Any]], source: str) -> None:
        nonlocal merged
        if rows:
            sources_used.append(source)
        for row in rows:
            url = row.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            merged.append(row)

    # 1) GitHub Trending（全局 + python/ts 热度）
    for lang in ("", "python", "typescript"):
        try:
            add_all(await fetch_github_trending(language=lang, since="daily", limit=8), f"github_trending:{lang or 'all'}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"github_trending:{lang or 'all'}: {exc}")

    # 2) GitHub Search（无 Token 只打 1 路，遇限流立即停）
    search_n = 2 if github_search_authenticated() else 1
    for q in build_github_queries(topic, industry_name)[:search_n]:
        try:
            add_all(await fetch_github_search_repos(q, limit=8), "github_search")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            errors.append(f"github_search: {msg}")
            if "限流" in msg or "rate limit" in msg.lower():
                break

    # 3) HN
    try:
        add_all(await fetch_hn_search(topic or industry_name or "AI agent", limit=8), "hackernews")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hackernews: {exc}")

    # 4) 中国创投 RSS（36氪）
    try:
        kw = topic or industry_name
        add_all(await fetch_cn_vc_rss(keyword=kw, limit_per_feed=6), "cn_vc_rss")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cn_vc_rss: {exc}")

    # 5) 创业邦公开频道（项目/融资资讯）
    cyzone_rows: list[dict[str, Any]] = []
    try:
        cyzone = await fetch_cyzone_projects(
            keyword=topic or industry_name,
            limit=12,
            enrich=True,
        )
        cyzone_rows = list(cyzone.get("items") or [])
        # 创投源 heat 通常远低于 GitHub stars，抬升后再参与排序，避免被截断
        for row in cyzone_rows:
            row["heat"] = float(row.get("heat") or 0) + 50.0
        add_all(cyzone_rows, "cyzone")
        if cyzone.get("errors"):
            errors.extend([f"cyzone:{e}" for e in cyzone["errors"][:5]])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cyzone: {exc}")

    # 6) 中国独立开发者项目（1c7/chinese-independent-developer）
    cid_rows: list[dict[str, Any]] = []
    try:
        from app.tools.cid_indie import collect_cid_indie_clues

        cid_pack = await collect_cid_indie_clues(
            keyword=topic,
            industry=industry_name,
            limit=max(8, min(16, limit)),
        )
        cid_rows = list(cid_pack.get("hotspot_items") or [])
        add_all(cid_rows, "cid_indie")
        if cid_pack.get("error"):
            errors.append(f"cid_indie: {cid_pack['error']}")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cid_indie: {exc}")

    merged.sort(key=lambda x: float(x.get("heat") or 0), reverse=True)

    # 保底混入创业邦 + CID，防止全被 GitHub 高 star 挤出
    reserved_pool = cyzone_rows + cid_rows
    if reserved_pool:
        reserved = max(4, min(8, limit // 3))
        keep_urls = {r.get("url") for r in merged[: max(0, limit - reserved)]}
        for row in sorted(reserved_pool, key=lambda x: float(x.get("heat") or 0), reverse=True):
            if len(keep_urls) >= limit:
                break
            url = row.get("url")
            if url and url not in keep_urls:
                keep_urls.add(url)
                if row not in merged:
                    merged.append(row)
        merged = [r for r in merged if r.get("url") in keep_urls]
        merged.sort(key=lambda x: float(x.get("heat") or 0), reverse=True)

    if not merged:
        raise RuntimeError("免 Key 热点源全部失败: " + " | ".join(errors[:6]))

    return {
        "topic": topic,
        "industry_name": industry_name,
        "items": merged[:limit],
        "sources_used": sources_used,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "无需商业搜索 Key；GitHub/HN/36氪/创业邦/中国独立开发者公开源。"
            "分析仍需大模型 Key。"
        ),
    }
