# -*- coding: utf-8 -*-
"""
项目推荐库（本地缓存）。

作用: 预缓存多源项目，按行业/赛道/难易度索引；增量去重追加，加速推荐。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir
from app.theme_recommend import list_all_industries
from app.tools.project_meta import enrich_project_row

# 赛道枚举
TRACKS = ("saas", "local", "content", "ai_tool", "health", "fintech", "devtools", "other")
DIFFICULTIES = ("easy", "mid", "hard")

_TRACK_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("health", ("医疗", "康复", "健康", "牙科", "口腔", "医美", "诊所", "hospital", "wellness", "physio", "理疗")),
    ("fintech", ("金融", "支付", "分期", "信贷", "保险", "fintech", "stripe", "billing", "理财")),
    ("local", ("到店", "预约", "美业", "家政", "本地", "booking", "salon", "dental", "餐饮", "汽后", "宠物")),
    ("content", ("短视频", "小红书", "创作者", "内容", "配图", "creator", "newsletter")),
    ("ai_tool", ("ai", "大模型", "agent", "llm", "gpt", "智能体", "模型")),
    ("devtools", ("api", "sdk", "cli", "docker", "devtools", "boilerplate", "template", "开源", "mcp")),
    ("saas", ("saas", "订阅", "crm", "平台", "b2b", "subscription", "文档", "发票")),
]

# 关键词 → 与 theme_recommend 行业名对齐（提高识别准确度）
_INDUSTRY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("牙科·种植/矫正", ("种植", "矫正", "种植牙", "隐形矫正", "implant")),
    ("牙科·儿童齿科", ("儿童齿科", "涂氟", "窝沟", "儿童牙")),
    ("美业·皮肤管理", ("皮肤管理", "光子", "水光", "医美")),
    ("美业·美甲美睫", ("美甲", "美睫")),
    ("少儿·托管接送", ("托管", "晚托", "接送")),
    ("少儿·兴趣班试听", ("兴趣班", "编程课", "钢琴", "试听")),
    ("健身·私教体验", ("私教", "减脂", "体测", "健身")),
    ("家政·深度保洁", ("保洁", "开荒", "油烟机")),
    ("家政·收纳整理", ("收纳", "衣橱整理")),
    ("餐饮·宴会预订", ("婚宴", "宴会", "桌数")),
    ("宠物·洗护寄养", ("宠物", "洗护", "寄养", "美容")),
    ("宠物·到店医疗", ("疫苗", "绝育", "宠物医院")),
    ("汽后·贴膜保养", ("贴膜", "镀晶", "汽后")),
    ("本地·财税代理", ("代账", "财税", "开票")),
    ("康养·理疗体检", ("理疗", "中医", "体检套餐", "康复")),
    ("SaaS·文档智能化", ("pdf", "ocr", "发票", "报销", "文档", "markdown")),
    ("SaaS·创作者工具", ("小红书", "短视频", "创作者", "配图", "爆款")),
    ("SaaS·开发者工具", ("api", "agent", "mcp", "devtools", "检测", "sdk")),
    ("SaaS·本地隐私工具", ("离线", "端侧", "隐私", "不上传")),
    ("B2B·出海GEO", ("geo", "出海", "跨境", "询盘")),
    ("B2B·垂直线索", ("线索", "外联", "获客落地")),
    ("内容·小红书获客", ("小红书获客", "笔记爆款")),
    ("本地·到店预约核销", ("到店预约", "核销", "体验课")),
    ("金融·个人理财助手", ("理财", "记账", "投研")),
    ("教育·阅读学习工具", ("阅读", "伴读", "注释")),
]


def _library_path() -> Path:
    return data_dir() / "project_library.json"


def _url_key(url: str) -> str:
    return hashlib.sha1((url or "").strip().encode("utf-8")).hexdigest()[:20]


def _token_usable(token: str) -> bool:
    """
    判断匹配用的词是否足够有意义（避免 'c'/'ai' 误命中英文 blob）。

    Args:
        token: 待检词。

    Returns:
        是否可用于子串/词边界匹配。
    """

    text = (token or "").strip()
    if len(text) < 2:
        return False
    if text.isascii() and len(text) < 3:
        return False
    return True


def _token_in_blob(token: str, blob: str) -> bool:
    """中文用包含匹配；短英文用词边界，减少误报。"""

    if not _token_usable(token):
        return False
    text = token.lower()
    if text.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(text)}(?![a-z0-9])", blob) is not None
    return text in blob


def score_industry_match(name: str, hint: str, blob: str) -> int:
    """
    对行业目录条目打分。

    Args:
        name/hint: 行业名与提示。
        blob: 项目文本小写拼接。

    Returns:
        匹配分；越高越准。同分应优先更长行业名。
    """

    score = 0
    if _token_usable(name) and name.lower() in blob:
        score += 6 + min(len(name), 12)
    for token in re.split(r"[/·\s,，]+", name or ""):
        if _token_in_blob(token, blob):
            score += 3
    for token in re.split(r"[/·\s,，]+", hint or ""):
        if _token_in_blob(token, blob):
            score += 1
    return score


def classify_project(row: dict[str, Any]) -> dict[str, str]:
    """
    为项目打行业 / 赛道 / 难易度标签。

    Returns:
        {industry, industry_id, track, difficulty}
    """

    blob = " ".join(
        str(x or "")
        for x in (
            row.get("name"),
            row.get("summary"),
            " ".join(row.get("industry_niches") or []),
            " ".join(row.get("pain_tags") or []),
            row.get("kind"),
            row.get("source"),
            row.get("industry"),
        )
    ).lower()

    industry = ""
    industry_id = ""
    # 1) 关键词规则（与 Theme Pack 行业名对齐）
    for label, keys in _INDUSTRY_RULES:
        if any(k.lower() in blob for k in keys):
            industry = label
            break
    # 2) 对照全量行业目录（含自定义）做二次匹配；阈值提高，避免短词误伤
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
        score = score_industry_match(name, hint, blob)
        # 同分优先更长、更具体的行业名
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

    if not industry:
        niches = row.get("industry_niches") or []
        industry = str(niches[0]) if niches else "综合/未标注"

    track = "other"
    for label, keys in _TRACK_RULES:
        if any(k.lower() in blob for k in keys):
            track = label
            break

    stars = float(row.get("stars") or 0)
    one = bool(row.get("one_click_ready"))
    kind = str(row.get("kind") or "")
    if one or stars >= 800 or kind == "market_product":
        difficulty = "easy"
    elif kind == "github_repo" or stars >= 50:
        difficulty = "mid"
    else:
        difficulty = "hard"

    return {
        "industry": industry,
        "industry_id": industry_id,
        "track": track,
        "difficulty": difficulty,
    }


def load_library() -> dict[str, Any]:
    """加载项目库。"""

    path = _library_path()
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


def save_library(doc: dict[str, Any]) -> None:
    """持久化项目库。"""

    path = _library_path()
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


def upsert_projects(rows: list[dict[str, Any]]) -> dict[str, int]:
    """
    增量写入项目（按 URL 去重；已有则合并热度/标签）。

    Returns:
        {added, updated, total}
    """

    lib = load_library()
    items: dict[str, Any] = dict(lib.get("items") or {})
    added = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            continue
        key = _url_key(url)
        tags = classify_project(row)
        enriched = enrich_project_row({**row, **tags})
        entry = {
            **{k: enriched.get(k) for k in (
                "name", "url", "summary", "source", "source_label", "kind",
                "pain_tags", "audience_tags", "industry_niches",
                "one_click_ready", "one_click_signals", "stars", "heat",
                "how_to_use", "language",
                "region", "funding_stage", "funding_amount_raw",
                "funding_amount_wan", "funding_band", "company_nature",
                "industry", "industry_id", "track", "difficulty",
            ) if enriched.get(k) is not None},
            "updated_at": now,
        }
        if key in items:
            old = items[key]
            # 保留更高 heat/stars
            try:
                entry["heat"] = max(float(old.get("heat") or 0), float(entry.get("heat") or 0))
            except (TypeError, ValueError):
                pass
            try:
                if entry.get("stars") is None:
                    entry["stars"] = old.get("stars")
                elif old.get("stars") is not None:
                    entry["stars"] = max(float(old.get("stars") or 0), float(entry.get("stars") or 0))
            except (TypeError, ValueError):
                pass
            items[key] = {**old, **entry}
            updated += 1
        else:
            entry["created_at"] = now
            items[key] = entry
            added += 1
    # 控制体积
    if len(items) > 2000:
        ranked = sorted(
            items.items(),
            key=lambda kv: float(kv[1].get("heat") or 0),
            reverse=True,
        )[:2000]
        items = dict(ranked)
    lib["items"] = items
    save_library(lib)
    return {"added": added, "updated": updated, "total": len(items)}


def query_library(
    *,
    topic: str = "",
    industry: str = "",
    track: str = "",
    difficulty: str = "",
    source: str = "",
    region: str = "",
    funding_stage: str = "",
    funding_band: str = "",
    company_nature: str = "",
    limit: int = 24,
    soft_topic: bool = False,
) -> list[dict[str, Any]]:
    """
    从缓存库检索项目。

    Args:
        topic: 关键词（匹配 name/summary/tags）。
        soft_topic: True 时不因缺词硬过滤，交给后续相关性重排。
        industry/track/difficulty/source/region/funding_*: 过滤条件。
    """

    lib = load_library()
    items = list((lib.get("items") or {}).values())
    topic_l = (topic or "").strip().lower()
    industry_l = (industry or "").strip().lower()
    track_l = (track or "").strip().lower()
    diff_l = (difficulty or "").strip().lower()
    source_l = (source or "").strip().lower()
    region_l = (region or "").strip()
    stage_l = (funding_stage or "").strip()
    band_l = (funding_band or "").strip()
    nature_l = (company_nature or "").strip()

    def score(row: dict[str, Any]) -> float:
        heat = float(row.get("heat") or 0)
        blob = f"{row.get('name')} {row.get('summary')} {row.get('industry')} {' '.join(row.get('industry_niches') or [])}".lower()
        bonus = 0.0
        if topic_l:
            parts = [p for p in re.split(r"[\s,/|·\-:+]+", topic_l) if len(p) >= 2]
            # 丢掉过泛英文停用
            parts = [p for p in parts if p not in {"the", "and", "for", "with", "agent", "ai", "saas"}]
            hits = sum(1 for p in parts if p in blob)
            if parts:
                if hits == 0 and not soft_topic:
                    return -1.0
                if hits == 0 and soft_topic:
                    # 软模式：保留热度项，后续靠相关性过滤
                    bonus += 0
                else:
                    bonus += hits * 30
        if industry_l:
            ind = str(row.get("industry") or "").lower()
            niches = " ".join(row.get("industry_niches") or []).lower()
            if industry_l not in ind and industry_l not in niches and industry_l not in blob:
                if not soft_topic:
                    return -1.0
            else:
                bonus += 40
        if track_l and track_l != str(row.get("track") or "").lower():
            return -1.0
        if diff_l and diff_l != str(row.get("difficulty") or "").lower():
            return -1.0
        src = str(row.get("source") or "").lower()
        if source_l in {"github", "gh"} and not src.startswith("github"):
            return -1.0
        if source_l in {"36kr", "kr"} and "36kr" not in src and "cn_vc" not in src and "pitchhub" not in src:
            return -1.0
        if source_l in {"cyzone", "创业邦"} and "cyzone" not in src:
            return -1.0
        if source_l in {"cid", "cid_indie"} and "cid" not in src:
            return -1.0
        if region_l and region_l not in ("全部",) and region_l not in str(row.get("region") or ""):
            return -1.0
        if stage_l and stage_l not in ("全部", "unknown") and str(row.get("funding_stage") or "") != stage_l:
            return -1.0
        if band_l and band_l not in ("全部", "unknown") and str(row.get("funding_band") or "") != band_l:
            return -1.0
        if nature_l and nature_l not in ("全部", "unknown") and nature_l not in str(row.get("company_nature") or ""):
            return -1.0
        if row.get("one_click_ready"):
            bonus += 15
        return heat + bonus

    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in items:
        s = score(row)
        if s < 0:
            continue
        ranked.append((s, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in ranked[: max(1, min(limit, 120))]]


def library_stats() -> dict[str, Any]:
    """库统计：按行业/赛道/难易度分布。"""

    lib = load_library()
    items = list((lib.get("items") or {}).values())
    by_industry: dict[str, int] = {}
    by_track: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for row in items:
        by_industry[str(row.get("industry") or "未标注")] = by_industry.get(str(row.get("industry") or "未标注"), 0) + 1
        by_track[str(row.get("track") or "other")] = by_track.get(str(row.get("track") or "other"), 0) + 1
        by_diff[str(row.get("difficulty") or "mid")] = by_diff.get(str(row.get("difficulty") or "mid"), 0) + 1
    return {
        "count": len(items),
        "by_industry": dict(sorted(by_industry.items(), key=lambda x: -x[1])[:20]),
        "by_track": by_track,
        "by_difficulty": by_diff,
        "meta": lib.get("meta") or {},
    }
