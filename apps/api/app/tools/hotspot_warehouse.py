# -*- coding: utf-8 -*-
"""
热点仓库（本地预热缓存）。

作用: 预采集多源热点并按行业/渠道/来源/地区/热度聚合入库，供控制台秒级刷新。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir
from app.tools.project_library import _INDUSTRY_RULES, score_industry_match
from app.theme_recommend import list_all_industries
from app.tools.trendradar_client import fetch_trendradar_hotspots

# 渠道（用户可见）
CHANNEL_LABELS: dict[str, str] = {
    "github": "开源社区",
    "hackernews": "海外科技",
    "cn_vc": "国内创投",
    "cyzone": "创业邦",
    "cid_indie": "独立开发",
    "trendradar": "全网热榜",
    "newsnow": "全网热榜",
    "seekmoney": "商机线索",
    "pain": "行业痛点",
    "ai_rebuild": "AI重构",
    "other": "其他",
}

# 预热种子：仅用于中文创投/资讯检索，不再把行业强加给 GitHub
_WARM_SEEDS: list[tuple[str, str]] = [
    ("创业融资", ""),
    ("本地生活", ""),
    ("SaaS 订阅", ""),
    ("医疗健康", ""),
    ("宠物经济", ""),
]

_MAX_ITEMS = 3000
_STALE_SECONDS = 30 * 60


def _warehouse_path() -> Path:
    """热点仓库文件路径。"""

    return data_dir() / "hotspot_warehouse.json"


def _item_key(row: dict[str, Any]) -> str:
    """
    生成热点去重键。

    Args:
        row: 热点条目。

    Returns:
        URL 或 title 的短哈希。
    """

    url = str(row.get("url") or "").strip()
    if url and not url.startswith("trendradar://"):
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    title = str(row.get("title") or "").strip().lower()
    provider = str(row.get("provider") or "").strip().lower()
    blob = f"{provider}|{title}"
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def _heat_bucket(heat: float) -> str:
    """
    热度分桶。

    Args:
        heat: 热度分。

    Returns:
        low | mid | high | hot
    """

    if heat >= 200:
        return "hot"
    if heat >= 80:
        return "high"
    if heat >= 20:
        return "mid"
    return "low"


def classify_hotspot(row: dict[str, Any], *, fallback_industry: str = "") -> dict[str, str]:
    """
    为热点打行业 / 渠道 / 来源 / 地区标签。

    Args:
        row: 原始热点。
        fallback_industry: 仅当正文确有相关词时才可采用的行业提示。

    Returns:
        {industry, industry_id, channel, channel_label, source, region, heat_bucket}
    """

    provider = str(row.get("provider") or row.get("source") or "").lower()
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    # 正文 blob：禁止把 fallback 行业名塞进去，否则会把无关 GitHub 项目误标成牙科等
    content_blob = " ".join(
        str(x or "")
        for x in (
            row.get("title"),
            row.get("snippet"),
            " ".join(str(x) for x in (meta.get("industry_niches") or [])),
            " ".join(str(x) for x in (meta.get("pain_tags") or [])),
            " ".join(str(x) for x in (meta.get("topics") or [])),
            meta.get("platform_label") or "",
        )
    ).lower()

    if provider.startswith("github"):
        channel = "github"
        source = "github"
        region = "global"
    elif "hackernews" in provider or provider == "hn":
        channel = "hackernews"
        source = "hackernews"
        region = "global"
    elif "cyzone" in provider or "pitchhub" in provider or "36kr" in provider or provider.startswith("rss:"):
        channel = "cn_vc" if "cyzone" not in provider else "cyzone"
        if "pitchhub" in provider:
            channel = "cyzone"
        source = "36kr" if ("36kr" in provider or "rss" in provider or "pitchhub" in provider) else "cyzone"
        region = "cn"
    elif provider.startswith("seekmoney") or provider.startswith("insight:"):
        channel = "trendradar"
        source = provider.split(":")[0]
        region = "cn"
        # 洞察卡片：用 meta.lane 区分
        lane = str(meta.get("lane") or "")
        if lane in ("seekmoney", "pain", "ai_rebuild"):
            channel = lane
            source = lane
    elif "cid" in provider:
        channel = "cid_indie"
        source = "cid_indie"
        region = "cn"
    elif provider.startswith("trendradar") or provider.startswith("newsnow"):
        channel = "trendradar"
        source = "trendradar" if provider.startswith("trendradar") else "newsnow"
        region = "cn"
    else:
        channel = "other"
        source = provider.split(":")[0] if provider else "other"
        region = "cn" if any(k in content_blob for k in ("中国", "国内", "创业邦", "36氪", "小红书", "微博", "知乎")) else "unknown"

    industry = ""
    industry_id = ""
    for label, keys in _INDUSTRY_RULES:
        if any(k.lower() in content_blob for k in keys):
            industry = label
            break

    best_score = 0
    best_name = ""
    best_id = ""
    try:
        catalog_rows = list_all_industries()
    except Exception:  # noqa: BLE001
        catalog_rows = []
    for item in catalog_rows:
        name = str(item.get("name") or "")
        hint = str(item.get("hint") or "")
        iid = str(item.get("id") or "")
        score = score_industry_match(name, hint, content_blob)
        if score > best_score or (score == best_score and score > 0 and len(name) > len(best_name)):
            best_score = score
            best_name = name
            best_id = iid
    if best_score >= 6:
        industry = best_name or industry
        industry_id = best_id
    elif industry:
        for item in catalog_rows:
            if str(item.get("name") or "") == industry:
                industry_id = str(item.get("id") or "")
                break

    # fallback 仅在正文确实出现该行业关键词时采用
    fb = (fallback_industry or "").strip()
    if not industry and fb:
        fb_l = fb.lower()
        tokens = [t for t in re.split(r"[/·\s,，]+", fb_l) if len(t) >= 2]
        if any(t in content_blob for t in tokens):
            industry = fb
            for item in catalog_rows:
                if str(item.get("name") or "") == fb:
                    industry_id = str(item.get("id") or "")
                    break

    if not industry:
        niches = meta.get("industry_niches") or []
        industry = str(niches[0]) if niches else "综合/未标注"

    try:
        heat = float(row.get("heat") or 0)
    except (TypeError, ValueError):
        heat = 0.0

    return {
        "industry": industry,
        "industry_id": industry_id,
        "channel": channel,
        "channel_label": CHANNEL_LABELS.get(channel, channel),
        "source": source,
        "region": region,
        "heat_bucket": _heat_bucket(heat),
    }


def load_warehouse() -> dict[str, Any]:
    """加载热点仓库。"""

    path = _warehouse_path()
    if not path.exists():
        return {"items": {}, "meta": {"count": 0}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"items": {}, "meta": {"count": 0, "error": "corrupt"}}
    if not isinstance(doc, dict):
        return {"items": {}, "meta": {"count": 0}}
    items = doc.get("items")
    if not isinstance(items, dict):
        items = {}
    return {"items": items, "meta": doc.get("meta") if isinstance(doc.get("meta"), dict) else {}}


def save_warehouse(doc: dict[str, Any]) -> None:
    """持久化热点仓库。"""

    path = _warehouse_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    items = doc.get("items") if isinstance(doc.get("items"), dict) else {}
    doc = {
        "items": items,
        "meta": {
            **(doc.get("meta") if isinstance(doc.get("meta"), dict) else {}),
            "count": len(items),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_hotspots(
    rows: list[dict[str, Any]],
    *,
    batch_source: str = "",
    fallback_industry: str = "",
) -> dict[str, int]:
    """
    增量写入热点（按 URL/标题去重；保留更高热度）。

    Args:
        rows: 热点列表。
        batch_source: 本批次来源标记。
        fallback_industry: 缺省行业。

    Returns:
        {added, updated, total}
    """

    wh = load_warehouse()
    items: dict[str, Any] = dict(wh.get("items") or {})
    added = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        key = _item_key(row)
        tags = classify_hotspot(row, fallback_industry=fallback_industry)
        try:
            heat = float(row.get("heat") or 0)
        except (TypeError, ValueError):
            heat = 0.0
        entry = {
            "title": title[:240],
            "url": str(row.get("url") or "").strip(),
            "snippet": str(row.get("snippet") or "").strip()[:800],
            "provider": str(row.get("provider") or tags["source"]),
            "heat": heat,
            "meta": row.get("meta") if isinstance(row.get("meta"), dict) else {},
            "industry": tags["industry"],
            "industry_id": tags["industry_id"],
            "channel": tags["channel"],
            "channel_label": tags["channel_label"],
            "source": tags["source"],
            "region": tags["region"],
            "heat_bucket": tags["heat_bucket"],
            "batch_source": batch_source or tags["source"],
            "last_seen_at": now,
            "updated_at": now,
        }
        if key in items:
            old = items[key]
            try:
                entry["heat"] = max(float(old.get("heat") or 0), heat)
            except (TypeError, ValueError):
                entry["heat"] = heat
            entry["heat_bucket"] = _heat_bucket(float(entry["heat"]))
            # 行业未标注时保留旧标签
            if entry["industry"] in ("", "综合/未标注") and old.get("industry"):
                entry["industry"] = old.get("industry")
                entry["industry_id"] = old.get("industry_id") or entry["industry_id"]
            items[key] = {**old, **entry}
            updated += 1
        else:
            entry["created_at"] = now
            items[key] = entry
            added += 1

    if len(items) > _MAX_ITEMS:
        ranked = sorted(
            items.items(),
            key=lambda kv: float(kv[1].get("heat") or 0),
            reverse=True,
        )[:_MAX_ITEMS]
        items = dict(ranked)

    wh["items"] = items
    meta = dict(wh.get("meta") or {})
    if batch_source:
        meta["last_batch_source"] = batch_source
    wh["meta"] = meta
    save_warehouse(wh)
    return {"added": added, "updated": updated, "total": len(items)}


def query_hotspots(
    *,
    topic: str = "",
    industry: str = "",
    channel: str = "",
    source: str = "",
    region: str = "",
    heat_bucket: str = "",
    heat_min: float = 0.0,
    limit: int = 24,
) -> list[dict[str, Any]]:
    """
    从热点仓库检索。

    Args:
        topic: 关键词。
        industry/channel/source/region/heat_bucket: 过滤维度。
        heat_min: 最低热度。
        limit: 返回条数上限。

    Returns:
        按热度与匹配分排序的热点列表。
    """

    wh = load_warehouse()
    items = list((wh.get("items") or {}).values())
    topic_l = (topic or "").strip().lower()
    industry_l = (industry or "").strip().lower()
    channel_l = (channel or "").strip().lower()
    source_l = (source or "").strip().lower()
    region_l = (region or "").strip().lower()
    bucket_l = (heat_bucket or "").strip().lower()
    try:
        heat_floor = float(heat_min or 0)
    except (TypeError, ValueError):
        heat_floor = 0.0

    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in items:
        try:
            heat = float(row.get("heat") or 0)
        except (TypeError, ValueError):
            heat = 0.0
        if heat < heat_floor:
            continue
        if industry_l:
            ind = str(row.get("industry") or "").lower()
            if industry_l not in ind and industry_l not in str(row.get("title") or "").lower():
                continue
        if channel_l and channel_l not in ("全部", "all"):
            if channel_l != str(row.get("channel") or "").lower():
                continue
        if source_l and source_l not in ("全部", "all"):
            src = str(row.get("source") or row.get("provider") or "").lower()
            if source_l not in src:
                continue
        if region_l and region_l not in ("全部", "all", "unknown"):
            if region_l != str(row.get("region") or "").lower():
                continue
        if bucket_l and bucket_l not in ("全部", "all"):
            if bucket_l != str(row.get("heat_bucket") or "").lower():
                continue

        bonus = 0.0
        blob = f"{row.get('title')} {row.get('snippet')} {row.get('industry')}".lower()
        if topic_l:
            parts = [p for p in re.split(r"[\s,/|·\-:+]+", topic_l) if len(p) >= 2]
            parts = [p for p in parts if p not in {"the", "and", "for", "with", "ai", "saas"}]
            hits = sum(1 for p in parts if p in blob)
            if parts and hits == 0:
                # 软匹配：主题不命中仍保留，但降权
                bonus -= 5
            else:
                bonus += hits * 25
        ranked.append((heat + bonus, row))

    ranked.sort(key=lambda x: x[0], reverse=True)
    # 多平台 × 每平台 30 名时总量可达 200+，上限放宽到 500
    return [r for _, r in ranked[: max(1, min(int(limit or 24), 500))]]


def warehouse_stats() -> dict[str, Any]:
    """
    热点库聚合统计。

    Returns:
        count 与各维度分布、热度分桶。
    """

    wh = load_warehouse()
    items = list((wh.get("items") or {}).values())
    by_industry: dict[str, int] = {}
    by_channel: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_region: dict[str, int] = {}
    heat_buckets: dict[str, int] = {"low": 0, "mid": 0, "high": 0, "hot": 0}
    for row in items:
        ind = str(row.get("industry") or "未标注")
        by_industry[ind] = by_industry.get(ind, 0) + 1
        ch = str(row.get("channel_label") or row.get("channel") or "其他")
        by_channel[ch] = by_channel.get(ch, 0) + 1
        src = str(row.get("source") or row.get("provider") or "其他")
        by_source[src] = by_source.get(src, 0) + 1
        reg = str(row.get("region") or "unknown")
        by_region[reg] = by_region.get(reg, 0) + 1
        bucket = str(row.get("heat_bucket") or _heat_bucket(float(row.get("heat") or 0)))
        heat_buckets[bucket] = heat_buckets.get(bucket, 0) + 1
    return {
        "count": len(items),
        "by_industry": dict(sorted(by_industry.items(), key=lambda x: -x[1])[:24]),
        "by_channel": dict(sorted(by_channel.items(), key=lambda x: -x[1])),
        "by_source": dict(sorted(by_source.items(), key=lambda x: -x[1])[:20]),
        "by_region": by_region,
        "heat_buckets": heat_buckets,
        "meta": wh.get("meta") or {},
    }


def warehouse_ready(*, min_count: int = 12) -> bool:
    """判断仓库是否已有足够预热数据。"""

    return int(warehouse_stats().get("count") or 0) >= min_count


def scrub_warehouse(*, reclassify: bool = True) -> dict[str, int]:
    """
    清理假数据并可选重分类。

    删除: example.com、热点样例、空标题等测试污染条目。

    Args:
        reclassify: 是否对保留条目重新打标签。

    Returns:
        {removed, kept, reclassified}
    """

    wh = load_warehouse()
    items: dict[str, Any] = dict(wh.get("items") or {})
    removed = 0
    reclassified = 0
    keep: dict[str, Any] = {}
    for key, row in items.items():
        if not isinstance(row, dict):
            removed += 1
            continue
        title = str(row.get("title") or "")
        url = str(row.get("url") or "")
        batch = str(row.get("batch_source") or "")
        if (
            "example.com" in url
            or "热点样例" in title
            or title.startswith("样例")
            or batch == "seed"
            or batch == "test"
            or url.startswith("https://example.com")
        ):
            removed += 1
            continue
        if reclassify:
            tags = classify_hotspot(row, fallback_industry="")
            row = {**row, **tags}
            row["heat_bucket"] = _heat_bucket(float(row.get("heat") or 0))
            reclassified += 1
        keep[key] = row
    wh["items"] = keep
    meta = dict(wh.get("meta") or {})
    meta["last_scrub_at"] = datetime.now(timezone.utc).isoformat()
    wh["meta"] = meta
    save_warehouse(wh)
    return {"removed": removed, "kept": len(keep), "reclassified": reclassified}


def is_warehouse_stale(*, max_age_seconds: int = _STALE_SECONDS) -> bool:
    """
    判断仓库是否过期。

    Args:
        max_age_seconds: 最大新鲜度（秒）。

    Returns:
        True 表示需要重新预热。
    """

    meta = load_warehouse().get("meta") or {}
    last = str(meta.get("last_warm_at") or meta.get("updated_at") or "")
    if not last:
        return True
    try:
        ts = datetime.fromisoformat(last.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        return age > max_age_seconds
    except Exception:  # noqa: BLE001
        return True


async def warm_hotspot_warehouse(
    *,
    limit_per_source: int = 16,
    include_trendradar: bool = True,
) -> dict[str, Any]:
    """
    预热真实热点：优先 TrendRadar MCP，失败则用同源 newsnow 全网热榜，再补国内创投。

    Args:
        limit_per_source: 每个源采集上限。
        include_trendradar: 是否尝试 TrendRadar MCP。

    Returns:
        预热结果摘要。
    """

    from app.tools.newsnow_hotspots import collect_newsnow_hotspots

    # 先清掉历史假数据 / 误标
    scrub = scrub_warehouse(reclassify=True)

    errors: list[str] = []
    upsert_total = {"added": 0, "updated": 0, "total": 0}
    sources_used: list[str] = []

    def _acc(stats: dict[str, Any], source: str) -> None:
        upsert_total["added"] += int(stats.get("added") or 0)
        upsert_total["updated"] += int(stats.get("updated") or 0)
        upsert_total["total"] = int(stats.get("total") or upsert_total["total"])
        if source not in sources_used:
            sources_used.append(source)

    got_realtime = False
    if include_trendradar:
        try:
            pack = await fetch_trendradar_hotspots(limit=max(20, min(limit_per_source * 2, 50)))
            rows = pack.get("items") or []
            if rows:
                _acc(upsert_hotspots(rows, batch_source="trendradar"), "trendradar")
                got_realtime = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"trendradar: {str(exc)[:160]}")

    # MCP 不可用时：直连 newsnow（TrendRadar README 标明的同源 API）
    if not got_realtime:
        try:
            pack = await collect_newsnow_hotspots(limit=max(240, limit_per_source * 8), per_platform=30)
            rows = pack.get("items") or []
            if rows:
                _acc(upsert_hotspots(rows, batch_source="newsnow"), "newsnow")
                got_realtime = True
                for s in pack.get("sources_used") or []:
                    if s not in sources_used:
                        sources_used.append(str(s))
            for e in pack.get("errors") or []:
                errors.append(str(e)[:160])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"newsnow: {str(exc)[:160]}")

    # 国内创投补源（不含 GitHub trending，避免冲掉热搜观感）
    async def _cn_seed(topic: str) -> dict[str, Any]:
        try:
            from app.tools.cyzone import fetch_cyzone_projects
            from app.tools.hotspot_sources import fetch_cn_vc_rss

            rows: list[dict[str, Any]] = []
            try:
                rows.extend(await fetch_cn_vc_rss(keyword=topic, limit_per_feed=5))
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "topic": topic, "error": f"rss:{exc}"}
            try:
                cz = await fetch_cyzone_projects(keyword=topic, limit=8, enrich=False)
                for row in cz.get("items") or []:
                    row["heat"] = float(row.get("heat") or 0) + 40.0
                    rows.append(row)
            except Exception:  # noqa: BLE001
                pass
            stats = upsert_hotspots(rows, batch_source="cn_vc")
            return {"ok": True, "topic": topic, "stats": stats}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "topic": topic, "error": str(exc)[:200]}

    seed_results = await asyncio.gather(*[_cn_seed(t) for t, _ in _WARM_SEEDS[:4]])
    for res in seed_results:
        if not res.get("ok"):
            errors.append(f"{res.get('topic')}: {res.get('error')}")
            continue
        _acc(res.get("stats") or {}, "cn_vc")

    wh = load_warehouse()
    meta = dict(wh.get("meta") or {})
    meta["last_warm_at"] = datetime.now(timezone.utc).isoformat()
    meta["last_warm_sources"] = sources_used
    meta["last_scrub"] = scrub
    wh["meta"] = meta
    save_warehouse(wh)

    return {
        "ok": True,
        "upsert": upsert_total,
        "sources_used": sources_used,
        "errors": errors[:12],
        "scrub": scrub,
        "stats": warehouse_stats(),
        "note": "已优先入库全网热搜（TrendRadar MCP 或同源 newsnow）",
    }


async def fetch_hotspots_cached(
    *,
    topic: str = "",
    industry: str = "",
    channel: str = "",
    source: str = "",
    region: str = "",
    heat_bucket: str = "",
    heat_min: float = 0.0,
    limit: int = 20,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    优先读仓库；必要时实时采集真实热搜并回写。

    默认不展示 GitHub 仓库冒充「热点」；channel=github 时才看开源社区。
    """

    from app.tools.newsnow_hotspots import collect_newsnow_hotspots

    limit_n = max(1, min(int(limit or 20), 40))
    errors: list[str] = []
    # 未指定渠道时，默认看全网热榜 + 国内创投（排除 github/hn）
    default_channel = (channel or "").strip()
    prefer_realtime_cn = default_channel in ("", "trendradar", "newsnow", "cn_vc", "cyzone")

    if use_cache and not refresh and warehouse_ready(min_count=8):
        q_channel = default_channel
        cached = query_hotspots(
            topic=topic,
            industry=industry,
            channel=q_channel,
            source=source,
            region=region or ("cn" if prefer_realtime_cn and not default_channel else ""),
            heat_bucket=heat_bucket,
            heat_min=heat_min,
            limit=limit_n * 2 if not default_channel else limit_n,
        )
        if not default_channel:
            # 过滤掉开源/HN，优先全网热榜
            cn_first = [
                r for r in cached
                if str(r.get("channel") or "") not in ("github", "hackernews")
            ]
            if len(cn_first) >= min(6, limit_n):
                cached = cn_first[:limit_n]
            else:
                cached = cached[:limit_n]
        if len(cached) >= min(4, limit_n):
            # 缓存里若几乎没有全网热榜，强制刷新一次
            realtime_n = sum(
                1 for r in cached
                if str(r.get("channel") or "") == "trendradar" or str(r.get("source") or "") in ("newsnow", "trendradar")
            )
            if realtime_n >= 2 or not prefer_realtime_cn:
                return {
                    "topic": topic or "",
                    "industry_name": industry or "",
                    "items": cached[:limit_n],
                    "count": min(len(cached), limit_n),
                    "from_cache": True,
                    "source_mode": "warehouse",
                    "sources_used": ["hotspot_warehouse"],
                    "errors": [],
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "note": "来自预热热点库",
                    "stats": warehouse_stats(),
                }

    live_items: list[dict[str, Any]] = []
    source_mode = "newsnow"
    try:
        tr = await fetch_trendradar_hotspots(limit=limit_n)
        live_items.extend(tr.get("items") or [])
        source_mode = "trendradar"
        upsert_hotspots(tr.get("items") or [], batch_source="trendradar")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc)[:200])

    if not live_items:
        try:
            nw = await collect_newsnow_hotspots(limit=max(limit_n, 240), per_platform=30)
            live_items.extend(nw.get("items") or [])
            source_mode = "newsnow"
            upsert_hotspots(nw.get("items") or [], batch_source="newsnow")
            for e in nw.get("errors") or []:
                errors.append(str(e)[:160])
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:200])

    # 可选补国内创投（不补 GitHub）
    try:
        from app.tools.hotspot_sources import fetch_cn_vc_rss

        rss = await fetch_cn_vc_rss(keyword=topic or industry or "创业", limit_per_feed=4)
        upsert_hotspots(rss, batch_source="cn_vc", fallback_industry=industry)
        seen = {_item_key(x) for x in live_items}
        for it in rss:
            k = _item_key(it)
            if k not in seen:
                seen.add(k)
                live_items.append(it)
        if live_items and source_mode == "newsnow":
            source_mode = "mixed"
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc)[:160])

    if not live_items:
        cached = query_hotspots(
            topic=topic,
            industry=industry,
            channel=default_channel,
            source=source,
            region=region,
            heat_bucket=heat_bucket,
            heat_min=heat_min,
            limit=limit_n,
        )
        if cached:
            return {
                "topic": topic or "",
                "industry_name": industry or "",
                "items": cached,
                "count": len(cached),
                "from_cache": True,
                "source_mode": "warehouse_fallback",
                "sources_used": ["hotspot_warehouse"],
                "errors": errors,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": "外网采集失败，已回退热点库",
                "stats": warehouse_stats(),
            }
        raise RuntimeError("热点采集失败: " + " | ".join(errors[:4]))

    filtered = query_hotspots(
        topic=topic,
        industry=industry,
        channel=default_channel if default_channel else "trendradar",
        source=source,
        region=region,
        heat_bucket=heat_bucket,
        heat_min=heat_min,
        limit=limit_n,
    )
    if not filtered and not default_channel:
        filtered = query_hotspots(
            topic=topic,
            industry=industry,
            channel="",
            source=source,
            region="cn",
            heat_bucket=heat_bucket,
            heat_min=heat_min,
            limit=limit_n,
        )
        filtered = [r for r in filtered if str(r.get("channel") or "") not in ("github", "hackernews")]
    items = filtered or live_items[:limit_n]
    return {
        "topic": topic or "",
        "industry_name": industry or "",
        "items": items[:limit_n],
        "count": len(items[:limit_n]),
        "from_cache": False,
        "source_mode": source_mode,
        "sources_used": [source_mode],
        "errors": errors[:8],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "已写入真实全网热搜" if source_mode in ("newsnow", "trendradar", "mixed") else "已写入热点库",
        "stats": warehouse_stats(),
    }
