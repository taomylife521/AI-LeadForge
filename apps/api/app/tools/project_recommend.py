# -*- coding: utf-8 -*-
"""
落地项目推荐（多源全网）：独立开发者 + GitHub + 36氪 + 创业邦。

作用: 按行业/痛点/主题推荐可参考或可一键试用的真实项目与创投线索（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.tools.cid_indie import collect_cid_indie_clues
from app.tools.cyzone import fetch_cyzone_projects
from app.tools.hotspot_sources import (
    fetch_cn_vc_rss,
    fetch_github_search_repos,
    fetch_github_trending,
    fetch_hn_search,
    github_search_authenticated,
)
from app.tools.project_library import classify_project, query_library, upsert_projects
from app.tools.project_match import rank_projects_for_theme
from app.tools.project_meta import enrich_project_row
from app.tools.pitchhub_36kr import fetch_pitchhub_projects


# 一键可用信号：README/描述里常出现的部署关键词
_ONE_CLICK_KEYS = (
    "docker",
    "dockerfile",
    "one-click",
    "一键",
    "deploy",
    "vercel",
    "railway",
    "render.com",
    "streamlit",
    "gradio",
    "colab",
    "huggingface spaces",
    "spaces.huggingface",
    "self-host",
    "docker-compose",
    "awesome",
    "template",
    "boilerplate",
    "starter",
)


def _blob(*parts: Any) -> str:
    return " ".join(str(p or "") for p in parts).lower()


def _one_click_score(text: str, topics: Optional[list[Any]] = None) -> tuple[bool, list[str]]:
    """判断是否具备一键部署/试用信号。"""

    blob = text.lower()
    if topics:
        blob += " " + " ".join(str(t).lower() for t in topics)
    hits = [k for k in _ONE_CLICK_KEYS if k in blob]
    return (len(hits) > 0, hits[:4])


# 中文主题 → 英文 GitHub 检索增强（提高命中质量）
_TOPIC_EN_ALIASES: list[tuple[tuple[str, ...], str]] = [
    (("康复", "颈", "腰", "职场健康", "理疗"), "workplace wellness physiotherapy"),
    (("牙科", "种植", "口腔"), "dental clinic booking"),
    (("美业", "医美", "皮肤"), "beauty salon booking"),
    (("宠物",), "pet care grooming"),
    (("家政", "保洁"), "home cleaning booking"),
    (("教培", "托管", "少儿"), "education tutoring"),
    (("发票", "报销"), "invoice expense receipt ocr"),
    (("小红书", "创作者"), "content creator tools"),
    (("获客", "线索"), "lead generation crm"),
    (("saas", "订阅"), "saas subscription"),
    (("agent", "智能体"), "ai agent workflow"),
]


def _english_boost(topic: str, industry: str) -> str:
    """根据中文主题追加英文检索词，提升 GitHub 命中质量。"""

    blob = f"{topic} {industry}".lower()
    hits: list[str] = []
    for keys, en in _TOPIC_EN_ALIASES:
        if any(k.lower() in blob for k in keys):
            hits.append(en)
    return " ".join(hits[:2])


def _github_queries(topic: str, industry: str) -> list[str]:
    """
    构造更广且更干净的 GitHub 全网检索词。

    覆盖：SaaS/工具、一键部署、模板仓库；避免「china」等易污染关键词。
    """

    topic = (topic or "").strip()
    industry = (industry or "").strip()
    noise = ("china", "中国", "独裁", "dictat")
    clean_parts = []
    for part in (topic, industry):
        low = part.lower()
        if part and not any(n in low for n in noise):
            clean_parts.append(part)
    boost = _english_boost(topic, industry)
    primary = boost or (topic if topic and not any(n in topic.lower() for n in noise) else "") or (
        industry if industry else "saas"
    )
    niche = boost or industry or topic or "tool"
    base = " ".join(x for x in [boost, *clean_parts] if x) or "saas ai tool"
    return [
        f"{base} (saas OR marketplace OR platform) stars:>30",
        f"{primary} (docker OR streamlit OR gradio OR fastapi) stars:>20",
        f"{niche} (boilerplate OR starter OR template) stars:>40",
        f"{primary} (agent OR automation OR workflow) stars:>25",
        f"{niche} (open-source OR opensource) language:Python stars:>40",
        f"{primary} (dashboard OR crm OR booking OR appointment) stars:>20",
    ]


_SPAM_NAME_HINTS = (
    "dictat",
    "dictatorship",
    "china-dict",
)


def _is_low_quality_repo(name: str, summary: str, stars: float) -> bool:
    """过滤明显无关/垃圾 GitHub 仓库。"""

    blob = f"{name} {summary}".lower()
    if any(h in blob for h in ("dictat", "dictatorship", "china-dict")):
        return True
    # 无描述且低星
    if stars < 15 and len((summary or "").strip()) < 8:
        return True
    # 仓库名过短或像随机串
    short = (name or "").split("/")[-1]
    if len(short) <= 2:
        return True
    if re.fullmatch(r"[a-z0-9]{16,}", short or ""):
        return True
    return False


def _diversify(items: list[dict[str, Any]], *, limit: int, mode: str = "projects") -> list[dict[str, Any]]:
    """
    按来源配额混排，避免被高 star GitHub 挤掉创投/独立开发者。

    mode=projects 时：少资讯、多项目（GitHub/CID/创业邦）。
    """

    buckets: dict[str, list[dict[str, Any]]] = {
        "github": [],
        "cid_indie": [],
        "cyzone": [],
        "36kr": [],
        "other": [],
    }
    for row in items:
        src = str(row.get("source") or "other")
        if src.startswith("github"):
            buckets["github"].append(row)
        elif src == "cid_indie":
            buckets["cid_indie"].append(row)
        elif src == "cyzone":
            buckets["cyzone"].append(row)
        elif src in {"36kr", "cn_vc_rss"}:
            buckets["36kr"].append(row)
        else:
            buckets["other"].append(row)

    for key in buckets:
        buckets[key].sort(
            key=lambda x: (1 if x.get("one_click_ready") else 0, float(x.get("heat") or 0)),
            reverse=True,
        )

    if mode == "projects":
        quotas = {
            "github": max(5, int(limit * 0.35)),
            "cid_indie": max(4, int(limit * 0.3)),
            "cyzone": max(4, int(limit * 0.3)),
            "36kr": max(0, int(limit * 0.05)),
            "other": max(1, int(limit * 0.05)),
        }
    else:
        quotas = {
            "github": max(4, int(limit * 0.4)),
            "cid_indie": max(2, int(limit * 0.2)),
            "cyzone": max(2, int(limit * 0.2)),
            "36kr": max(2, int(limit * 0.15)),
            "other": max(1, int(limit * 0.05)),
        }
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(bucket: str, n: int) -> None:
        for row in buckets[bucket]:
            if len(picked) >= limit:
                return
            if n <= 0:
                return
            url = str(row.get("url") or "")
            if url in seen:
                continue
            seen.add(url)
            picked.append(row)
            n -= 1

    for key, n in quotas.items():
        take(key, n)

    # 补足剩余名额（按综合排序）
    rest = sorted(
        items,
        key=lambda x: (1 if x.get("one_click_ready") else 0, float(x.get("heat") or 0)),
        reverse=True,
    )
    for row in rest:
        if len(picked) >= limit:
            break
        url = str(row.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            picked.append(row)
    return picked


_NEWS_NOISE = (
    "晚报",
    "早报",
    "快讯汇总",
    "一周",
    "日报",
    "速递",
    "要闻",
    "盘点",
    "热榜",
    "氪星晚报",
    "氪星早报",
)
_PROJECT_HINTS = (
    "融资",
    "获投",
    "种子轮",
    "天使轮",
    "Pre-A",
    "A轮",
    "B轮",
    "C轮",
    "发布",
    "上线",
    "开源",
    "产品",
    "平台",
    "SaaS",
    "公司",
)


def _looks_like_project_item(title: str, summary: str = "") -> bool:
    """判断是否更像「项目/公司」而非资讯盘点。"""

    blob = f"{title} {summary}"
    if any(n in blob for n in _NEWS_NOISE):
        return False
    return any(h in blob for h in _PROJECT_HINTS) or bool(re.search(r"[「“\"].+[」”\"]", blob))


async def recommend_landing_projects(
    *,
    topic: str = "",
    industry: str = "",
    limit: int = 24,
    mode: str = "projects",
    use_cache: bool = True,
    force_refresh: bool = False,
    track: str = "",
    difficulty: str = "",
    source: str = "",
    region: str = "",
    funding_stage: str = "",
    funding_band: str = "",
    company_nature: str = "",
) -> dict[str, Any]:
    """
    汇总推荐落地项目 / 创投线索。

    Args:
        mode: projects=只要项目/可落地仓库（默认，过滤晚报资讯）；all=含资讯讨论。
        use_cache: 优先读本地项目库（快）。
        force_refresh: 强制全网抓取并增量写入库。
        track/difficulty: 赛道/难易度过滤（easy|mid|hard）。
        source: github|36kr|cyzone|cid_indie|all（空=全部）。
        region/funding_stage/funding_band/company_nature: 元数据筛选。
    """

    topic = (topic or "").strip()
    industry = (industry or "").strip()
    track = (track or "").strip().lower()
    difficulty = (difficulty or "").strip().lower()
    source = (source or "").strip().lower()
    region = (region or "").strip()
    funding_stage = (funding_stage or "").strip()
    funding_band = (funding_band or "").strip()
    company_nature = (company_nature or "").strip()
    mode = (mode or "projects").strip().lower()
    if mode not in {"projects", "all"}:
        mode = "projects"
    kw = topic or industry or "创业 AI SaaS"
    hard_cap = max(8, min(limit, 40))

    # —— 缓存优先（加速）——
    if use_cache and not force_refresh:
        cached = query_library(
            topic=topic,
            industry=industry,
            track=track,
            difficulty=difficulty,
            source=source,
            region=region,
            funding_stage=funding_stage,
            funding_band=funding_band,
            company_nature=company_nature,
            limit=max(hard_cap * 3, 40),
            soft_topic=True,
        )
        ranked_cached = rank_projects_for_theme(
            cached, topic=topic, industry=industry, min_score=10.0, limit=hard_cap
        )
        if len(ranked_cached) >= min(4, max(3, hard_cap // 4)):
            by_source: dict[str, int] = {}
            for row in ranked_cached:
                key = str(row.get("source_label") or row.get("source") or "cache")
                by_source[key] = by_source.get(key, 0) + 1
            return {
                "topic": topic,
                "industry": industry,
                "mode": mode,
                "track": track,
                "difficulty": difficulty,
                "source": source,
                "region": region,
                "funding_stage": funding_stage,
                "funding_band": funding_band,
                "company_nature": company_nature,
                "count": len(ranked_cached),
                "total_found": len(cached),
                "items": ranked_cached,
                "by_source": by_source,
                "sources_used": ["project_library"],
                "from_cache": True,
                "errors": [],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": "本地库按商机主题相关性重排（过滤无关/噪音项）。",
            }

    errors: list[str] = []
    sources_used: list[str] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def push(row: dict[str, Any]) -> None:
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            return
        seen.add(url)
        tags = classify_project(row)
        row = enrich_project_row({**row, **tags})
        src = str(row.get("source") or "").lower()
        if source in {"github", "gh"} and not src.startswith("github"):
            return
        if source in {"36kr", "kr"} and "36kr" not in src and "cn_vc" not in src and "pitchhub" not in src:
            return
        if source in {"cyzone", "创业邦"} and "cyzone" not in src:
            return
        if source in {"cid", "cid_indie"} and "cid" not in src:
            return
        if track and str(row.get("track") or "") != track:
            return
        if difficulty and str(row.get("difficulty") or "") != difficulty:
            return
        if region and region not in ("", "全部") and region not in str(row.get("region") or ""):
            return
        if funding_stage and funding_stage not in ("", "全部", "unknown"):
            if str(row.get("funding_stage") or "") != funding_stage:
                return
        if funding_band and funding_band not in ("", "全部", "unknown"):
            if str(row.get("funding_band") or "") != funding_band:
                return
        if company_nature and company_nature not in ("", "全部", "unknown"):
            if company_nature not in str(row.get("company_nature") or ""):
                return
        items.append(row)

    async def collect_cid() -> None:
        try:
            cid = await collect_cid_indie_clues(
                keyword=topic or industry,
                industry=industry,
                limit=max(10, hard_cap // 2),
            )
            if cid.get("error"):
                errors.append(f"cid_indie: {cid['error']}")
                return
            sources_used.append("cid_indie")
            for row in cid.get("items") or []:
                blob = _blob(row.get("name"), row.get("description"))
                one, signals = _one_click_score(blob)
                push(
                    {
                        "name": row.get("name"),
                        "url": row.get("url"),
                        "summary": row.get("description"),
                        "source": "cid_indie",
                        "source_label": "中国独立开发者",
                        "kind": "market_product",
                        "pain_tags": row.get("pain_tags") or [],
                        "audience_tags": row.get("audience_tags") or [],
                        "industry_niches": row.get("industry_niches") or [],
                        "one_click_ready": one,
                        "one_click_signals": signals,
                        "stars": None,
                        "heat": 60.0,
                        "how_to_use": "对标已上线产品：拆痛点/人群/定价，找差异化切口。",
                    }
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cid_indie: {exc}")

    async def collect_github() -> None:
        # Trending 全网热仓（HTML，不占 Search API 配额）
        for lang in ("", "python", "typescript"):
            try:
                rows = await fetch_github_trending(language=lang, since="weekly", limit=8)
                if rows:
                    sources_used.append(f"github_trending:{lang or 'all'}")
                for repo in rows:
                    meta = repo.get("meta") or {}
                    name = str(repo.get("title") or "")
                    if name.startswith("sponsors/") or name.count("/") != 1:
                        continue
                    blob = _blob(name, repo.get("snippet"))
                    one, signals = _one_click_score(blob)
                    stars = float(repo.get("heat") or 0)
                    summary = str(repo.get("snippet") or "")
                    if _is_low_quality_repo(name, summary, stars):
                        continue
                    push(
                        {
                            "name": name,
                            "url": repo.get("url"),
                            "summary": summary,
                            "source": "github_trending",
                            "source_label": "GitHub Trending",
                            "kind": "github_repo",
                            "pain_tags": [],
                            "audience_tags": ["开发者"],
                            "industry_niches": [industry] if industry else [],
                            "one_click_ready": one or stars >= 200,
                            "one_click_signals": signals,
                            "stars": stars,
                            "language": meta.get("language") or lang or None,
                            "heat": stars + 30.0,
                            "how_to_use": "周榜热仓：优先看 README 与 Issues，评估是否可 fork 验证。",
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"github_trending:{lang or 'all'}: {exc}")

        # Search API：有 Token 才多路；无 Token 最多 1 路，遇限流立即停
        authed = github_search_authenticated()
        query_cap = 4 if authed else 1
        queries = _github_queries(topic, industry)[:query_cap]
        if not authed and queries:
            errors.append(
                "GitHub Search 未配置 GITHUB_TOKEN：匿名限额极低，已降为 1 路检索 + Trending；"
                "请在控制台 Key 维护或 .env 写入 GITHUB_TOKEN（github.com/settings/tokens，勾选 public_repo）。"
            )

        rate_limited = False
        for q in queries:
            if rate_limited:
                break
            try:
                repos = await fetch_github_search_repos(q, limit=10 if authed else 8)
                if repos:
                    sources_used.append("github_search")
                for repo in repos:
                    meta = repo.get("meta") or {}
                    blob = _blob(repo.get("title"), repo.get("snippet"), meta.get("language"))
                    one, signals = _one_click_score(blob, meta.get("topics") or [])
                    stars = float(meta.get("stars") or repo.get("heat") or 0)
                    name = str(repo.get("title") or "")
                    summary = str(repo.get("snippet") or "")
                    if _is_low_quality_repo(name, summary, stars):
                        continue
                    push(
                        {
                            "name": name,
                            "url": repo.get("url"),
                            "summary": summary,
                            "source": "github_search",
                            "source_label": "GitHub Search",
                            "kind": "github_repo",
                            "pain_tags": [],
                            "audience_tags": ["开发者"] if one else [],
                            "industry_niches": [industry] if industry else [],
                            "one_click_ready": one or stars >= 500,
                            "one_click_signals": signals or (["popular"] if stars >= 500 else []),
                            "stars": stars,
                            "language": meta.get("language"),
                            "heat": stars,
                            "query": q,
                            "how_to_use": (
                                "可 clone / fork；含 Docker/Spaces/模板信号时可快速搭 MVP。"
                                if one
                                else "阅读 README，评估二次开发成本与授权协议。"
                            ),
                        }
                    )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if "限流" in msg or "rate limit" in msg.lower() or "403" in msg:
                    rate_limited = True
                    errors.append(msg)
                    break
                errors.append(f"github_search:{q[:48]}: {exc}")
            if authed:
                await asyncio.sleep(0.35)
            else:
                await asyncio.sleep(1.0)

    async def collect_36kr() -> None:
        # 优先 PitchHub 企业项目库（真实项目卡片，非晚报资讯）
        try:
            pack = await fetch_pitchhub_projects(
                keyword=kw if len(kw) <= 40 else (topic or industry or ""),
                limit=max(12, hard_cap),
                sort="3",
            )
            rows = list(pack.get("items") or [])
            kept = 0
            for row in rows:
                push(row)
                kept += 1
            if kept:
                sources_used.append("36kr_pitchhub")
            if pack.get("errors"):
                errors.extend([f"pitchhub:{e}" for e in pack["errors"][:3]])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"pitchhub: {exc}")

        # RSS 作为补充（可能含资讯噪声，projects 模式会再滤）
        try:
            rows = await fetch_cn_vc_rss(keyword=kw, limit_per_feed=8)
            kept = 0
            for row in rows:
                title = str(row.get("title") or "")
                snippet = str(row.get("snippet") or "")
                if mode == "projects" and not _looks_like_project_item(title, snippet):
                    continue
                heat = float(row.get("heat") or 20) + 30.0
                push(
                    {
                        "name": title,
                        "url": row.get("url"),
                        "summary": snippet,
                        "source": "36kr",
                        "source_label": "36氪·RSS",
                        "kind": "startup_project" if _looks_like_project_item(title, snippet) else "startup_news",
                        "pain_tags": [],
                        "audience_tags": [],
                        "industry_niches": [industry] if industry else [],
                        "one_click_ready": False,
                        "one_click_signals": [],
                        "stars": None,
                        "heat": heat,
                        "how_to_use": "融资/产品发布线索：抽取公司与赛道，一键识别商机主题后跑工作流。",
                    }
                )
                kept += 1
            if kept:
                sources_used.append("36kr_rss")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"36kr_rss: {exc}")

    async def collect_cyzone() -> None:
        try:
            pack = await fetch_cyzone_projects(
                keyword=kw,
                limit=16,
                enrich=True,
                channels=["chuangtou", "company", "invest"],
            )
            if pack.get("errors"):
                errors.extend([f"cyzone:{e}" for e in pack["errors"][:4]])
            rows = list(pack.get("items") or [])
            kept = 0
            for row in rows:
                title = str(row.get("title") or row.get("name") or "")
                snippet = str(row.get("snippet") or row.get("summary") or "")
                if mode == "projects" and not _looks_like_project_item(title, snippet):
                    continue
                heat = float(row.get("heat") or 20) + 55.0
                push(
                    {
                        "name": title,
                        "url": row.get("url"),
                        "summary": snippet,
                        "source": "cyzone",
                        "source_label": "创业邦·项目",
                        "kind": "startup_project",
                        "pain_tags": [],
                        "audience_tags": [],
                        "industry_niches": [industry] if industry else [],
                        "one_click_ready": False,
                        "one_click_signals": [],
                        "stars": None,
                        "heat": heat + (10 if "融资" in title or "获投" in title else 0),
                        "channel": (row.get("meta") or {}).get("channel") if isinstance(row.get("meta"), dict) else None,
                        "how_to_use": "创业邦项目：点「识别商机主题」→「跑工作流」→「找项目组合」。",
                    }
                )
                kept += 1
            if kept:
                sources_used.append("cyzone")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cyzone: {exc}")

    async def collect_hn() -> None:
        try:
            rows = await fetch_hn_search(kw, limit=8)
            kept = 0
            for row in rows:
                url = str(row.get("url") or "")
                has_gh = "github.com" in url.lower()
                if mode == "projects" and not has_gh:
                    continue
                push(
                    {
                        "name": row.get("title"),
                        "url": url,
                        "summary": row.get("snippet"),
                        "source": "hackernews",
                        "source_label": "Hacker News",
                        "kind": "github_repo" if has_gh else "discussion",
                        "pain_tags": [],
                        "audience_tags": ["开发者"],
                        "industry_niches": [],
                        "one_click_ready": has_gh,
                        "one_click_signals": ["github_link"] if has_gh else [],
                        "stars": None,
                        "heat": float(row.get("heat") or 10),
                        "how_to_use": "开源/Show HN 项目：可直接 fork 验证，或识别主题后跑商机工作流。",
                    }
                )
                kept += 1
            if kept:
                sources_used.append("hackernews")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"hackernews: {exc}")

    await asyncio.gather(
        collect_cid(),
        collect_github(),
        collect_36kr(),
        collect_cyzone(),
        collect_hn(),
    )

    clipped = _diversify(items, limit=max(hard_cap * 2, 24), mode=mode)
    # 按商机主题相关性重排，丢掉无关噪音
    clipped = rank_projects_for_theme(
        clipped, topic=topic, industry=industry, min_score=8.0, limit=hard_cap
    )
    # 增量写入本地库（去重）——入库用 diversify 前的全集更划算
    try:
        upsert_stats = upsert_projects(items[:120])
    except Exception as exc:  # noqa: BLE001
        upsert_stats = {"error": str(exc)}
        errors.append(f"library_upsert: {exc}")

    by_source: dict[str, int] = {}
    for row in clipped:
        key = str(row.get("source_label") or row.get("source") or "other")
        by_source[key] = by_source.get(key, 0) + 1

    return {
        "topic": topic,
        "industry": industry,
        "mode": mode,
        "track": track,
        "difficulty": difficulty,
        "source": source,
        "region": region,
        "funding_stage": funding_stage,
        "funding_band": funding_band,
        "company_nature": company_nature,
        "count": len(clipped),
        "total_found": len(items),
        "items": clipped,
        "by_source": by_source,
        "sources_used": list(dict.fromkeys(sources_used)),
        "from_cache": False,
        "library_upsert": upsert_stats,
        "errors": errors[:12],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "全网抓取后按商机主题相关性重排；无关书单/awesome 等已降权过滤。"
        ),
    }
