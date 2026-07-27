# -*- coding: utf-8 -*-
"""
创业邦（cyzone.cn）真实项目/创投资讯采集。

作用: 抓取公开频道文章（创投/融资等），抽取标题与摘要，供商机线索分析；禁止 mock。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional

import httpx

from app.envelope import ModelRoute
from app.llm import LLMClient


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 LeadForgeCyzone/1.0"
)

# 公开可访问频道（无需登录）
CYZONE_CHANNELS: list[dict[str, str]] = [
    {"id": "chuangtou", "name": "创投", "path": "/channel/chuangtou"},
    {"id": "news", "name": "最新资讯", "path": "/channel/news"},
    {"id": "invest", "name": "投资", "path": "/channel/invest"},
    {"id": "company", "name": "公司", "path": "/channel/company"},
    {"id": "finance", "name": "财经", "path": "/channel/finance"},
]

_FUNDING_HINTS = (
    "融资",
    "获投",
    "种子轮",
    "天使轮",
    "Pre-A",
    "A轮",
    "B轮",
    "C轮",
    "IPO",
    "上市",
    "独家",
    "发布",
    "创业",
    "独角兽",
    "AI",
    "大模型",
    "获客",
    "本地生活",
)


def _heat_from_title(title: str) -> float:
    """根据标题关键词估算热点分。"""

    heat = 1.0
    for w in _FUNDING_HINTS:
        if w.lower() in title.lower() or w in title:
            heat += 2.0 if w in {"融资", "A轮", "B轮", "C轮", "获投"} else 1.0
    return heat


def _extract_articles_from_html(html: str, *, channel: str) -> list[dict[str, Any]]:
    """从频道页 HTML 提取文章 ID/标题。"""

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r"/article/(\d+)\.html", html):
        aid = match.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        pos = match.start()
        chunk = html[max(0, pos - 500) : pos + 500]
        titles = re.findall(r">([\u4e00-\u9fff][^<]{6,90})<", chunk)
        title = ""
        for candidate in titles:
            cand = unescape(candidate).strip()
            if cand.startswith("创业邦"):
                continue
            if "ICP" in cand or "许可证" in cand:
                continue
            title = cand
            break
        url = f"https://www.cyzone.cn/article/{aid}.html"
        items.append(
            {
                "id": aid,
                "title": title or f"创业邦文章 {aid}",
                "url": url,
                "snippet": "",
                "provider": "cyzone",
                "heat": _heat_from_title(title),
                "meta": {"channel": channel, "article_id": aid},
            }
        )
    return items


async def _enrich_article(client: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
    """抓取文章页补全 title/description。"""

    try:
        resp = await client.get(item["url"])
        if resp.status_code >= 400:
            item["meta"] = {**(item.get("meta") or {}), "fetch_error": f"HTTP {resp.status_code}"}
            return item
        html = resp.text
        title_m = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
        if title_m:
            title = unescape(re.sub(r"\s+", " ", title_m.group(1))).strip()
            title = re.sub(r"\s*-\s*创业邦\s*$", "", title).strip()
            if title:
                item["title"] = title
                item["heat"] = _heat_from_title(title)
        desc = ""
        for pat in (
            r'property="og:description"\s+content="(.*?)"',
            r'name="description"\s+content="(.*?)"',
        ):
            m = re.search(pat, html, flags=re.I | re.S)
            if m:
                desc = unescape(m.group(1)).strip()
                break
        if not desc:
            # 正文粗抽
            text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
            text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
            text = re.sub(r"(?is)<[^>]+>", " ", text)
            text = unescape(re.sub(r"\s+", " ", text)).strip()
            desc = text[:280]
        item["snippet"] = desc[:500]
        item["meta"] = {**(item.get("meta") or {}), "enriched": True}
    except Exception as exc:  # noqa: BLE001
        item["meta"] = {**(item.get("meta") or {}), "fetch_error": str(exc)}
    return item


def _keyword_match(text: str, keyword: str) -> bool:
    """宽松中文/英文关键词匹配。"""

    keyword = (keyword or "").strip()
    if not keyword:
        return True
    blob = (text or "").lower()
    kw = keyword.lower()
    if kw in blob:
        return True
    # 拆字/拆词
    parts = re.split(r"[\s,/|，、]+", keyword)
    hits = 0
    for part in parts:
        part = part.strip().lower()
        if len(part) >= 2 and part in blob:
            hits += 1
    if hits:
        return True
    chars = [c for c in keyword if "\u4e00" <= c <= "\u9fff"]
    return bool(chars) and sum(1 for c in chars if c in blob) >= max(1, len(chars) // 3)


async def fetch_cyzone_projects(
    *,
    keyword: str = "",
    channels: Optional[list[str]] = None,
    limit: int = 20,
    enrich: bool = True,
) -> dict[str, Any]:
    """
    采集创业邦公开频道中的项目/融资相关文章。

    Args:
        keyword: 主题/行业关键词过滤（空则不过滤）。
        channels: 频道 id 列表；默认创投+资讯+投资+公司。
        limit: 返回上限。
        enrich: 是否抓详情页补摘要。

    Returns:
        {items, channels_used, errors, generated_at, source}
    """

    wanted = set(channels or ["chuangtou", "news", "invest", "company"])
    channel_defs = [c for c in CYZONE_CHANNELS if c["id"] in wanted] or CYZONE_CHANNELS[:3]
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html", "Accept-Language": "zh-CN,zh;q=0.9"}
    errors: list[str] = []
    channels_used: list[str] = []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=35.0, follow_redirects=True, headers=headers) as client:
        for ch in channel_defs:
            url = "https://www.cyzone.cn" + ch["path"]
            try:
                resp = await client.get(url)
                if resp.status_code >= 400:
                    errors.append(f"{ch['id']}: HTTP {resp.status_code}")
                    continue
                rows = _extract_articles_from_html(resp.text, channel=ch["id"])
                channels_used.append(ch["id"])
                for row in rows:
                    if row["url"] in seen:
                        continue
                    seen.add(row["url"])
                    merged.append(row)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{ch['id']}: {exc}")

        # 关键词过滤（先按列表页标题）
        if keyword:
            filtered = [
                r
                for r in merged
                if _keyword_match(f"{r.get('title')} {r.get('snippet')}", keyword)
                or any(h in (r.get("title") or "") for h in _FUNDING_HINTS)
            ]
            # 若过滤过严，保留融资向文章
            if filtered:
                merged = filtered

        # 优先融资/项目向
        merged.sort(key=lambda x: float(x.get("heat") or 0), reverse=True)
        merged = merged[: max(1, min(limit, 40))]

        if enrich and merged:
            enriched: list[dict[str, Any]] = []
            for row in merged:
                enriched.append(await _enrich_article(client, row))
            # 二次关键词（含摘要）
            if keyword:
                enriched2 = [
                    r
                    for r in enriched
                    if _keyword_match(f"{r.get('title')} {r.get('snippet')}", keyword)
                    or any(h in (r.get("title") or "") for h in ("融资", "获投", "轮"))
                ]
                merged = enriched2 or enriched
            else:
                merged = enriched

    if not merged:
        raise RuntimeError("创业邦公开频道未解析到文章: " + " | ".join(errors[:5] or ["empty"]))

    merged.sort(key=lambda x: float(x.get("heat") or 0), reverse=True)
    return {
        "source": "cyzone",
        "keyword": keyword,
        "channels_used": channels_used,
        "items": merged[:limit],
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "来自 cyzone.cn 公开频道真实页面，非 mock。",
    }


async def analyze_cyzone_lead_clues(
    *,
    topic: str,
    industry_name: str = "",
    cyzone_pack: Optional[dict[str, Any]] = None,
    llm: Optional[LLMClient] = None,
    limit: int = 12,
) -> dict[str, Any]:
    """
    基于创业邦真实文章，用大模型抽取商机线索（allow_mock=False）。

    Returns:
        含 lead_clues[] / projects[] / recommended_leads 的结构化结果。
    """

    topic = (topic or "").strip() or "中国创业项目"
    pack = cyzone_pack or await fetch_cyzone_projects(keyword=topic or industry_name, limit=limit)
    items = pack.get("items") or []
    # 关键词过窄时回退到未过滤公开频道，避免无证据
    if len(items) < 3:
        broad = await fetch_cyzone_projects(keyword="", limit=max(limit, 10), enrich=True)
        items = broad.get("items") or items
        pack = {**pack, **broad, "items": items, "note": "关键词过窄，已回退公开频道全量近期文章"}

    if not items:
        raise RuntimeError("无创业邦文章可用于线索分析")

    evidence = []
    for idx, row in enumerate(items[:limit], start=1):
        evidence.append(
            f"[{idx}] title={row.get('title')}\nurl={row.get('url')}\n"
            f"channel={(row.get('meta') or {}).get('channel')}\n"
            f"heat={row.get('heat')}\nsnippet={(row.get('snippet') or '')[:350]}"
        )

    client = llm or LLMClient()
    system = (
        "你是中国创投「商机线索」分析师，目标是发现可靠、可落地的商业机会信号。"
        "必须只依据创业邦文章证据，把融资/公司/产品新闻转成商机线索。"
        "评估重点：真实需求、谁付费、竞争缺口、为何现在、最小验证步骤；"
        "获客渠道只可作为后续落地备注，不要写成主结论。"
        "禁止编造融资金额、投资方、未出现的公司名；金额只能引用证据原文。"
        "只输出 JSON："
        '{"lead_clues":[{"company":"...","event":"...","round_or_signal":"...",'
        '"why_hot":"...","opportunity_angle":"...","success_signal":"...",'
        '"who_pays":"...","validation_step":"...","channels_later":["后续可选渠道"],'
        '"confidence":0.0,"source_url":"...","source_title":"..."}],'
        '"projects":[{"name":"...","summary":"...","url":"...","heat_note":"..."}],'
        '"recommended_leads":["高潜力商机一句话", "..."],'
        '"market":"中国","locale":"zh-CN"}'
    )
    user = (
        f"topic={topic}; industry={industry_name}; source=创业邦cyzone。\n"
        f"阶段=商机发现（非获客执行）。要求: lead_clues 至少覆盖证据中的项目，每条必须带 source_url。\n"
        f"证据：\n" + "\n\n".join(evidence)
    )
    payload, model, used_mock = await client.complete_json(
        route=ModelRoute.TIER_S,
        system=system,
        user=user,
        mock_payload=None,
        allow_mock=False,
        temperature=0.25,
    )
    if used_mock:
        raise RuntimeError("创业邦线索分析禁止 mock")
    if not isinstance(payload, dict):
        raise RuntimeError("线索分析未返回 JSON 对象")
    clues = payload.get("lead_clues") if isinstance(payload.get("lead_clues"), list) else []
    projects = payload.get("projects") if isinstance(payload.get("projects"), list) else []

    # 模型偶发只填 projects：用真实 projects/文章回填线索（不编造）
    if not clues and projects:
        for proj in projects:
            if not isinstance(proj, dict):
                continue
            url = str(proj.get("url") or "").strip()
            name = str(proj.get("name") or "").strip()
            if not url or not name:
                continue
            clues.append(
                {
                    "company": name,
                    "event": str(proj.get("summary") or "")[:200],
                    "round_or_signal": "见证据摘要",
                    "why_hot": str(proj.get("heat_note") or ""),
                    "opportunity_angle": f"围绕「{topic or industry_name or '中国市场'}」评估可复制的需求/付费/供给缺口",
                    "success_signal": str(proj.get("heat_note") or "见证据"),
                    "channels_later": [],
                    "confidence": 0.55,
                    "source_url": url,
                    "source_title": name,
                }
            )
    if not clues:
        for row in items[:limit]:
            title = str(row.get("title") or "").strip()
            url = str(row.get("url") or "").strip()
            if not title or not url:
                continue
            company = title.split("|")[-1].strip() if "|" in title else title[:40]
            clues.append(
                {
                    "company": company,
                    "event": title,
                    "round_or_signal": "创业邦公开资讯",
                    "why_hot": f"heat={row.get('heat')}",
                    "opportunity_angle": f"评估「{topic or '创投'}」是否构成可验证、可付费的商业机会",
                    "success_signal": f"heat={row.get('heat')}",
                    "channels_later": [],
                    "confidence": 0.45,
                    "source_url": url,
                    "source_title": title,
                }
            )

    if not clues:
        raise RuntimeError("未产出 lead_clues[]")

    recommended = payload.get("recommended_leads") if isinstance(payload.get("recommended_leads"), list) else []
    if not recommended:
        recommended = [f"{c.get('company')}: {c.get('event')}" for c in clues[:5] if isinstance(c, dict)]

    return {
        "topic": topic,
        "industry_name": industry_name,
        "market": "中国",
        "locale": "zh-CN",
        "lead_clues": clues,
        "projects": projects
        or [
            {
                "name": c.get("company"),
                "summary": c.get("event"),
                "url": c.get("source_url"),
                "heat_note": c.get("why_hot"),
            }
            for c in clues
            if isinstance(c, dict)
        ],
        "recommended_leads": recommended,
        "cyzone": {
            "channels_used": pack.get("channels_used"),
            "item_count": len(items),
            "items": [
                {
                    "title": i.get("title"),
                    "url": i.get("url"),
                    "heat": i.get("heat"),
                    "channel": (i.get("meta") or {}).get("channel"),
                }
                for i in items
            ],
            "errors": pack.get("errors") or [],
        },
        "model": model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "cyzone_real",
    }
