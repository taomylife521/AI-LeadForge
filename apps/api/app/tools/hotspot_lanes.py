# -*- coding: utf-8 -*-
"""
多维热点通道（平台热搜 / SeekMoney 线索 / 创投 / 行业痛点 / AI 重构）。

作用: 为「看热点」提供一级页签数据；真实采集 + AI 提炼，禁止 mock 编造新闻。
参考: SeekMoney-ai 痛点发现、创投公开源、一人企业可落地约束。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.theme_recommend import list_all_industries, list_cn_vc_market_industries
from app.tools.hotspot_opportunity import group_hotspots_by_platform
from app.tools.hotspot_sources import (
    _item,
    build_github_queries,
    fetch_cn_vc_rss,
    fetch_github_search_repos,
    fetch_github_trending,
)
from app.tools.hotspot_warehouse import (
    fetch_hotspots_cached,
    query_hotspots,
    upsert_hotspots,
    warehouse_stats,
)
from app.tools.methodology_playbooks import methodology_context_block, seekmoney_opportunity_system_prompt
from app.tools.project_library import _INDUSTRY_RULES, query_library, score_industry_match
from app.tools.newsnow_hotspots import NEWSNOW_PLATFORMS
from app.tools.seekmoney_clues import build_seekmoney_clues


# 一级通道定义（UI 页签顺序）
LANE_DEFS: list[dict[str, str]] = [
    {"id": "platforms", "label": "平台热搜", "desc": "微博/知乎/百度等真实热榜"},
    {"id": "seekmoney", "label": "商机线索", "desc": "SeekMoney 痛点发现框架提炼"},
    {"id": "vc", "label": "创投热点", "desc": "36氪/创业邦等创业项目与融资动态"},
    {"id": "pain", "label": "行业痛点", "desc": "传统行业与平台侧可验证痛点场景"},
    {"id": "ai_rebuild", "label": "AI重构", "desc": "各行各业可用 AI 重做的作业场景"},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_hotspot(
    *,
    title: str,
    url: str,
    snippet: str,
    provider: str,
    heat: float,
    lane: str,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造带 lane 标记的热点条目。"""

    row = _item(
        title=title,
        url=url,
        snippet=snippet,
        provider=provider,
        heat=heat,
        meta={**(meta or {}), "lane": lane},
    )
    row["lane"] = lane
    return row


async def collect_vc_hotspot_items(*, limit: int = 24, industry: str = "") -> list[dict[str, Any]]:
    """
    采集创投圈热点项目/资讯，归一为热点条目。

    Args:
        limit: 条数上限。
        industry: 行业关键词（空=全网创业/AI）。

    Returns:
        热点列表（lane=vc）。
    """

    from app.tools.cyzone import fetch_cyzone_projects
    from app.tools.cid_indie import collect_cid_indie_clues
    from app.tools.pitchhub_36kr import fetch_pitchhub_projects

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    keys = _industry_keywords(industry)
    kw = " ".join(keys[:4]) if keys else "创业 融资 AI"
    cz_kw = f"{keys[0]} AI" if keys else "AI 创业"

    try:
        rss = await fetch_cn_vc_rss(keyword=kw, limit_per_feed=6)
        for r in rss:
            rows.append(
                _as_hotspot(
                    title=str(r.get("title") or ""),
                    url=str(r.get("url") or ""),
                    snippet=str(r.get("snippet") or "36氪创投资讯"),
                    provider=str(r.get("provider") or "rss:36kr"),
                    heat=float(r.get("heat") or 60) + 20,
                    lane="vc",
                    meta={"lane": "vc", "platform_label": "36氪", "platform": "36kr"},
                )
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"rss:{exc}")

    try:
        cz = await fetch_cyzone_projects(keyword=cz_kw, limit=10, enrich=False)
        for r in cz.get("items") or []:
            rows.append(
                _as_hotspot(
                    title=str(r.get("name") or r.get("title") or ""),
                    url=str(r.get("url") or ""),
                    snippet=str(r.get("summary") or "创业邦项目"),
                    provider="cyzone",
                    heat=float(r.get("heat") or 40) + 50,
                    lane="vc",
                    meta={"lane": "vc", "platform_label": "创业邦", "platform": "cyzone"},
                )
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cyzone:{exc}")

    try:
        ph = await fetch_pitchhub_projects(keyword="AI", limit=8)
        for r in ph.get("items") or []:
            rows.append(
                _as_hotspot(
                    title=str(r.get("name") or r.get("title") or ""),
                    url=str(r.get("url") or ""),
                    snippet=str(r.get("summary") or "36氪 PitchHub"),
                    provider="pitchhub_36kr",
                    heat=float(r.get("heat") or 30) + 40,
                    lane="vc",
                    meta={"lane": "vc", "platform_label": "PitchHub", "platform": "pitchhub"},
                )
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"pitchhub:{exc}")

    try:
        cid = await collect_cid_indie_clues(keyword="AI", industry="", limit=8)
        for r in cid.get("hotspot_items") or []:
            rows.append(
                _as_hotspot(
                    title=str(r.get("title") or r.get("name") or ""),
                    url=str(r.get("url") or ""),
                    snippet=str(r.get("snippet") or "独立开发者项目"),
                    provider="cid_indie",
                    heat=float(r.get("heat") or 25) + 30,
                    lane="vc",
                    meta={"lane": "vc", "platform_label": "独立开发", "platform": "cid_indie"},
                )
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cid:{exc}")

    # 去重
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: float(x.get("heat") or 0), reverse=True):
        key = (r.get("url") or r.get("title") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    if errors:
        # 仅挂在首条 meta，便于排查；不阻断
        if out:
            out[0].setdefault("meta", {})["collect_errors"] = errors[:4]
    return out


async def _ai_lane_cards(
    *,
    lane: str,
    signal_lines: list[str],
    industry: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    基于真实信号用 AI 生成某通道卡片（SeekMoney/痛点/AI重构）。

    Args:
        lane: seekmoney | pain | ai_rebuild
        signal_lines: 真实热搜/创投标题行。
        industry: 行业偏好。
        limit: 条数。

    Returns:
        热点形态卡片列表。
    """

    if not signal_lines:
        return []

    industry_hint = ""
    try:
        names = [str(x.get("name") or "") for x in list_all_industries()[:18]]
        industry_hint = "、".join([n for n in names if n][:12])
    except Exception:  # noqa: BLE001
        industry_hint = "牙科、美业、教培、家政、餐饮、宠物、SaaS"

    if lane == "seekmoney":
        system = seekmoney_opportunity_system_prompt() + (
            "\n本次输出通道=商机线索：每条必须是「可验证的付费痛点商机」，禁止新闻标题复述。"
            "禁止写成「围绕「某某热搜」的可产品化痛点」；title 用「商机：行业 · 具体痛点」。"
            "热搜仅可作弱证据写进 source_titles；若信号是吃瓜/体育/明星则跳过。"
            '字段用 opportunities 数组。'
        )
        focus = "SeekMoney 商机线索（痛点→根因→付费方→MVP）"
    elif lane == "pain":
        system = (
            "你是行业痛点分析师。根据真实热搜与创投信号，提炼「传统行业/平台侧」可验证痛点场景。\n"
            f"可参考行业目录：{industry_hint}\n"
            "每条痛点要具体到作业场景（谁在什么环节多花时间/钱/投诉），禁止空泛。\n"
            "对齐 SeekMoney：表面痛点→根因→场景；对齐一人公司：两周可验证。\n"
            '输出 JSON: {"opportunities":[{"title":"痛点：...","industry":"...","why_reliable":"...",'
            '"surface_pain":"...","root_cause":"...","user_scenario":"...","who_pays":"...",'
            '"mvp_2w":"...","confidence":0-1,"signal_platforms":[],"source_titles":[]}]}'
        )
        focus = "传统行业与平台痛点场景"
    else:
        system = (
            "你是 AI 落地顾问。根据真实信号，列出「各行各业可用 AI 重构」的作业场景。\n"
            f"行业参考：{industry_hint}\n"
            "每条写清：原流程痛点、AI 如何替代/增强、谁付费、两周最小验证。\n"
            "拒绝纯概念（如「赋能数字化」）；要可交付动作。\n"
            '输出 JSON: {"opportunities":[{"title":"AI重构：...","industry":"...","why_reliable":"...",'
            '"surface_pain":"...","root_cause":"...","user_scenario":"...","who_pays":"...",'
            '"mvp_2w":"...","confidence":0-1,"signal_platforms":[],"source_titles":[]}]}'
        )
        focus = "AI 可重构作业场景"

    user = (
        f"{methodology_context_block()}\n"
        f"通道: {focus}\n行业偏好: {industry or '不限'}\n请输出 {max(3, min(limit, 10))} 条：\n"
        + "\n".join(signal_lines[:40])[:4200]
    )
    try:
        llm = LLMClient()
        payload, _model, used_mock = await llm.complete_json(
            route=ModelRoute.TIER_M,
            system=system,
            user=user,
            allow_mock=False,
            temperature=0.25,
        )
        if used_mock or not isinstance(payload, dict):
            return []
        cards: list[dict[str, Any]] = []
        for i, item in enumerate(payload.get("opportunities") or []):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            try:
                conf = float(item.get("confidence") or 0.5)
            except (TypeError, ValueError):
                conf = 0.5
            heat = 50 + conf * 50 + max(0, 10 - i)
            snippet_parts = [
                str(item.get("surface_pain") or item.get("why_reliable") or "")[:120],
                f"付费方:{item.get('who_pays') or '—'}",
                f"两周:{item.get('mvp_2w') or '—'}",
            ]
            cards.append(
                _as_hotspot(
                    title=title[:140],
                    url=f"leadforge://lane/{lane}/{i}",
                    snippet=" · ".join([p for p in snippet_parts if p]),
                    provider=f"seekmoney:{lane}" if lane == "seekmoney" else f"insight:{lane}",
                    heat=heat,
                    lane=lane,
                    meta={
                        "lane": lane,
                        "platform": lane,
                        "platform_label": {
                            "seekmoney": "SeekMoney线索",
                            "pain": "行业痛点",
                            "ai_rebuild": "AI重构",
                        }.get(lane, lane),
                        "industry": item.get("industry"),
                        "who_pays": item.get("who_pays"),
                        "mvp_2w": item.get("mvp_2w"),
                        "surface_pain": item.get("surface_pain"),
                        "root_cause": item.get("root_cause"),
                        "user_scenario": item.get("user_scenario"),
                        "confidence": conf,
                        "source_titles": item.get("source_titles") or [],
                        "methodology": "seekmoney+opc",
                    },
                )
            )
            if len(cards) >= limit:
                break
        return cards
    except Exception:  # noqa: BLE001
        return []


def _signal_lines_from_rows(rows: list[dict[str, Any]], *, limit: int = 36) -> list[str]:
    """把热点/创投行格式化为模型输入行。"""

    lines: list[str] = []
    for r in rows[:limit]:
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        label = str(meta.get("platform_label") or r.get("channel_label") or r.get("provider") or "")
        title = str(r.get("title") or r.get("name") or "").strip()
        if not title:
            continue
        lines.append(f"- [{label}] {title[:80]}")
    return lines


async def collect_github_hotspot_items(*, topic: str = "", industry: str = "", limit: int = 20) -> list[dict[str, Any]]:
    """
    采集 GitHub 全网相关项目，归一为热点条目（中国落地语境）。

    Args:
        topic: 主题。
        industry: 行业。
        limit: 条数。

    Returns:
        热点列表（供线索/AI重构引用）。
    """

    def _looks_junk(title: str, snippet: str) -> bool:
        """过滤无描述、随机名仓库等低质量条目。"""

        t = (title or "").strip()
        s = (snippet or "").strip()
        if not t or "/" not in t:
            # 允许本土库非 owner/repo 名，但需有实质描述
            if len(s) < 12 and len(t) < 4:
                return True
            return False
        owner, _, repo = t.partition("/")
        if not owner or not repo:
            return True
        # 过短/纯随机字母数字且无描述
        if len(repo) <= 3 and len(s) < 20:
            return True
        if re.fullmatch(r"[A-Za-z0-9_-]{10,}", repo) and len(s) < 16 and not any(
            k in f"{t} {s}".lower() for k in ("ai", "llm", "agent", "saas", "bot", "crm", "chat")
        ):
            # 无意义长串仓库名且无 AI 相关信号
            vowels = sum(1 for c in repo.lower() if c in "aeiou")
            if vowels <= 1:
                return True
        return False

    rows: list[dict[str, Any]] = []
    try:
        for lang, since in (("", "daily"), ("python", "weekly"), ("typescript", "weekly")):
            try:
                part = await fetch_github_trending(language=lang, since=since, limit=8)
            except Exception:  # noqa: BLE001
                continue
            for r in part:
                title = str(r.get("title") or "")
                snip = str(r.get("snippet") or "GitHub Trending")
                if _looks_junk(title, snip):
                    continue
                rows.append(
                    _as_hotspot(
                        title=title,
                        url=str(r.get("url") or ""),
                        snippet=snip,
                        provider="github_trending",
                        heat=float(r.get("heat") or 40),
                        lane="github",
                        meta={
                            "lane": "github",
                            "platform": "github",
                            "platform_label": "GitHub",
                            "region": "global",
                        },
                    )
                )
    except Exception:  # noqa: BLE001
        pass

    queries = build_github_queries(topic or "AI agent SaaS China", industry or "本地生活")
    # 固定补几条高相关搜索，避免行业词过窄导致空结果
    queries = list(dict.fromkeys(list(queries) + [
        "AI agent stars:>100",
        "chatbot CRM language:TypeScript",
        "appointment booking SaaS",
    ]))
    for q in queries[:4]:
        try:
            part = await fetch_github_search_repos(q, limit=8)
            for r in part:
                title = str(r.get("title") or "")
                snip = str(r.get("snippet") or f"GitHub Search · {q}")[:200]
                if _looks_junk(title, snip):
                    continue
                rows.append(
                    _as_hotspot(
                        title=title,
                        url=str(r.get("url") or ""),
                        snippet=snip,
                        provider="github_search",
                        heat=float(r.get("heat") or 30),
                        lane="github",
                        meta={
                            "lane": "github",
                            "platform": "github",
                            "platform_label": "GitHub",
                            "query": q,
                        },
                    )
                )
        except Exception:  # noqa: BLE001
            continue

    # 本土项目库补充
    try:
        lib = query_library(topic=topic or "AI 预约", industry=industry, region="中国", limit=12)
        for r in lib:
            title = str(r.get("name") or r.get("title") or "")
            snip = str(r.get("summary") or "本土项目库")[:200]
            if not title:
                continue
            rows.append(
                _as_hotspot(
                    title=title,
                    url=str(r.get("url") or ""),
                    snippet=snip,
                    provider=str(r.get("source") or "project_library"),
                    heat=float(r.get("heat") or 35) + 15,
                    lane="github",
                    meta={
                        "lane": "github",
                        "platform": "cn_library",
                        "platform_label": "本土项目库",
                        "industry": r.get("industry"),
                    },
                )
            )
    except Exception:  # noqa: BLE001
        pass

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=lambda x: float(x.get("heat") or 0), reverse=True):
        key = (r.get("url") or r.get("title") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= limit:
            break
    return out


def _is_insight_or_derived_row(row: dict[str, Any]) -> bool:
    """判断是否为已生成的洞察卡（避免再当热搜喂给线索通道）。"""

    provider = str(row.get("provider") or "").lower()
    channel = str(row.get("channel") or "").lower()
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    lane = str(meta.get("lane") or row.get("lane") or "").lower()
    title = str(row.get("title") or "")
    if channel in ("seekmoney", "pain", "ai_rebuild", "github"):
        return True
    if lane in ("seekmoney", "pain", "ai_rebuild", "github"):
        return True
    if provider.startswith(("seekmoney", "insight:", "github")):
        return True
    if title.startswith(("线索：", "痛点：", "AI重构：")):
        return True
    return False


def _clean_signal_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤掉洞察/派生条目，只保留原始热搜/创投信号。"""

    return [r for r in rows if isinstance(r, dict) and not _is_insight_or_derived_row(r)]


def _industry_keywords(industry: str) -> list[str]:
    """
    展开行业筛选关键词（含自定义行业同义词）。

    Args:
        industry: 用户选择的行业名，空表示全网。

    Returns:
        小写关键词列表（已去重）；空行业返回空列表。
    """

    name = (industry or "").strip()
    if not name:
        return []

    # 创投标准所属行业 → 检索同义词
    synonyms: dict[str, tuple[str, ...]] = {
        "文化娱乐": ("文化娱乐", "文娱", "影视", "综艺", "娱乐", "短剧", "内容", "粉丝", "明星", "直播"),
        "消费电商": ("消费电商", "电商", "零售", "新消费", "品牌", "直播带货", "购物", "跨境店"),
        "汽车出行": ("汽车出行", "汽车", "出行", "新能源车", "智能驾驶", "网约车", "汽后", "充电"),
        "教育": ("教育", "教培", "培训", "课程", "试听", "托管", "学习", "学校", "在线教育"),
        "金融": ("金融", "理财", "支付", "信贷", "保险", "银行", "证券", "基金", "fintech"),
        "企业服务": ("企业服务", "saas", "toB", "办公", "降本", "crm", "erp", "低代码", "协同"),
        "产业升级": ("产业升级", "数字化转型", "工业互联网", "智能工厂"),
        "前沿技术": ("前沿技术", "人工智能", "大模型", "机器人", "agi", "llm", "agent"),
        "医疗健康": (
            "医疗健康", "大健康", "健康", "医疗", "医药", "康养", "医院", "诊所", "体检",
            "药店", "康复", "医美", "牙科", "疫苗", "医保", "数字医疗", "互联网医疗",
        ),
        "大健康": (
            "大健康", "医疗健康", "健康", "医疗", "医药", "康养", "医院", "诊所", "体检",
        ),
        "先进制造": ("先进制造", "智能制造", "高端装备", "工业机器人", "精密制造"),
        "通信/半导体": ("通信", "半导体", "芯片", "集成电路", "5g", "光模块", "eda"),
        "物联网/硬件": ("物联网", "iot", "硬件", "传感", "嵌入式", "联网设备"),
        "工具软件": ("工具软件", "效率工具", "生产力", "插件", "devtools", "编辑器"),
        "社交网络": ("社交", "社区", "兴趣社交", "即时通讯", "交友"),
        "农林牧渔": ("农业", "农林", "牧渔", "养殖", "种植", "农产品"),
        "能源环保": ("能源", "环保", "新能源", "光伏", "储能", "碳中和", "节能"),
        "本地生活": ("本地生活", "到店", "外卖", "团购", "门店", "核销", "预约", "家政", "餐饮"),
        "体育游戏": ("体育", "游戏", "电竞", "赛事", "健身"),
        "跨境出海": ("跨境", "出海", "独立站", "跨境电商", "国际化", "geo"),
        "房产地产": ("房产", "地产", "物业", "租房", "装修", "中介"),
        "旅游": ("旅游", "文旅", "酒店", "景区", "机票", "度假"),
        "广告营销": ("广告", "营销", "投放", "增长", "获客", "martech"),
        "智能硬件": ("智能硬件", "消费电子", "智能家居", "可穿戴", "耳机"),
        "物流": ("物流", "仓储", "快递", "供应链", "运力", "配送"),
        "区块链": ("区块链", "web3", "加密", "链上", "defi"),
        "传统制造": ("传统制造", "工厂", "代工", "制造", "车间"),
        "元宇宙": ("元宇宙", "xr", "vr", "ar", "数字人", "虚拟空间"),
        "其他": ("综合", "其他"),
        "餐饮": ("餐饮", "饭店", "外卖", "宴会", "茶饮"),
        "宠物": ("宠物", "猫", "狗", "洗护", "寄养"),
        "家政": ("家政", "保洁", "收纳", "月嫂"),
        "汽车": ("汽车", "汽后", "贴膜", "保养", "新能源车"),
        "saas": ("saas", "软件", "api", "agent", "自动化", "低代码"),
    }

    tokens: list[str] = []
    name_l = name.lower()
    for key, words in synonyms.items():
        if key in name_l or name_l in key:
            tokens.extend(words)
    # 拆分目录名：牙科·种植/矫正 → 牙科, 种植, 矫正
    for part in re.split(r"[/·\s,，、|+]+", name):
        part = part.strip()
        if len(part) >= 2:
            tokens.append(part)
    # 目录规则关键词
    for label, keys in _INDUSTRY_RULES:
        if label == name or name in label or label in name:
            tokens.extend(keys)
            tokens.append(label)

    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        tl = str(t).strip().lower()
        if len(tl) < 2 or tl in seen:
            continue
        # 过短英文噪声
        if tl.isascii() and len(tl) < 3:
            continue
        seen.add(tl)
        out.append(tl)
    return out


def _row_content_blob(row: dict[str, Any]) -> str:
    """仅标题+摘要（过滤时不信任已污染的 industry 字段）。"""

    return f"{row.get('title') or ''} {row.get('snippet') or ''}".lower()


def _row_blob(row: dict[str, Any]) -> str:
    """拼接条目可检索文本（含行业字段，供打分展示）。"""

    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return " ".join(
        str(x or "")
        for x in (
            row.get("title"),
            row.get("snippet"),
            row.get("industry"),
            meta.get("industry"),
            meta.get("platform_label"),
            meta.get("query"),
            " ".join(str(x) for x in (meta.get("topics") or [])),
        )
    ).lower()


def _row_matches_industry(row: dict[str, Any], industry: str) -> bool:
    """
    判断条目是否与行业相关（只看标题/摘要关键词，避免库内错误行业标签误放行）。

    Args:
        row: 热点/项目。
        industry: 行业名；空=全网（恒为 True）。

    Returns:
        是否匹配。
    """

    keys = _industry_keywords(industry)
    if not keys:
        return True
    blob = _row_content_blob(row)
    if not blob.strip():
        return False
    return any(k in blob for k in keys)


def _filter_by_industry(rows: list[dict[str, Any]], industry: str) -> list[dict[str, Any]]:
    """按行业过滤；空行业原样返回（全网）。"""

    if not (industry or "").strip():
        return list(rows)
    return [r for r in rows if isinstance(r, dict) and _row_matches_industry(r, industry)]


def _tool_industry_from_text(text: str) -> str:
    """
    将 GitHub/工具类项目映射到 SaaS 行业（不落到牙科/医美）。

    Args:
        text: 标题+描述。

    Returns:
        行业名；无法判断时返回空串。
    """

    blob = (text or "").lower()
    rules = [
        ("SaaS·开发者工具", ("agent", "mcp", "sdk", "devtools", "langchain", "langflow", "dify", "hermes", "superpowers", "framework", "workflow")),
        ("SaaS·文档智能化", ("ocr", "pdf", "invoice", "document", "markdown", "rag", "knowledge", "prompt")),
        ("SaaS·创作者工具", ("creator", "short video", "xiaohongshu", "content", "marketing", "seo", "copywriting")),
        ("SaaS·本地隐私工具", ("offline", "privacy", "local-first", "on-device", "端侧")),
        ("B2B·垂直线索", ("crm", "lead", "outbound", "sales", "线索")),
        ("本地·到店预约核销", ("booking", "appointment", "reservation", "预约", "核销")),
        ("教育·阅读学习工具", ("guide", "tutorial", "interview", "learning", "course", "javaguide", "教材")),
    ]
    for label, keys in rules:
        if any(k in blob for k in keys):
            return label
    if "/" in (text or "") and any(k in blob for k in ("github", "llm", "ai", "python", "typescript")):
        return "SaaS·开发者工具"
    return ""


# 工具能力 → 匹配的作业场景（按优先级匹配，避免 JavaGuide 去做预约）
_TOOL_JOB_RULES: list[tuple[tuple[str, ...], tuple[str, str, str]]] = [
    (("javaguide", "guide", "tutorial", "interview", "学习", "教材"), ("知识问答与培训", "培训/技术负责人", "用知识库沉淀面试/岗位要点并做问答")),
    (("ocr", "invoice", "pdf", "document", "报销", "单据"), ("单据与报销审核", "财务", "OCR+规则引擎替代人工录入")),
    (("booking", "appointment", "reservation", "预约", "排班", "calendar"), ("预约接待与回访", "客服/前台", "用对话/表单 Agent 接管初筛与预约")),
    (("crm", "lead", "sales", "outbound", "线索"), ("线索评分与跟进", "销售", "用模型给线索打分并自动催办")),
    (("content", "creator", "marketing", "seo", "copy", "配图", "短视频"), ("内容生产与获客", "运营", "用生成+审核流批量产出投放素材")),
    (("rag", "knowledge", "dify", "langchain", "langflow", "prompt", "qa", "chat"), ("知识问答与培训", "店长/负责人", "用知识库问答替代反复口传培训")),
    (("agent", "hermes", "superpowers", "workflow", "mcp", "自动化"), ("研发协作与质检", "技术负责人", "用 Agent/工作流减少重复手工")),
    (("inventory", "stock", "schedule", "排班", "库存"), ("库存/排班辅助", "运营", "用预测/规则减少空档与积压")),
]


def _best_job_for_tool(text: str) -> tuple[str, str, str]:
    """
    按工具语义选择最匹配的作业场景。

    Args:
        text: 仓库标题+描述。

    Returns:
        (场景名, 付费方, 两周做法)
    """

    blob = (text or "").lower()
    for keys, job in _TOOL_JOB_RULES:
        if any(k in blob for k in keys):
            return job
    # 默认：通用 Agent 协作，而不是硬套预约/获客
    return ("研发协作与质检", "技术负责人", "用 Agent/工作流减少重复手工")


def _match_industry(text: str) -> str:
    """
    从文本匹配行业目录名（正文关键词 / 目录打分）。

    Args:
        text: 标题或摘要。

    Returns:
        命中的行业名；未命中返回空串（禁止默认牙科）。
    """

    blob = (text or "").lower()
    if not blob.strip():
        return ""

    best_label = ""
    for label, keys in _INDUSTRY_RULES:
        if any(k.lower() in blob for k in keys if len(k) >= 2):
            best_label = label
            break

    best_score = 0
    best_name = ""
    try:
        catalog_rows = list_all_industries()
    except Exception:  # noqa: BLE001
        catalog_rows = []
    for item in catalog_rows:
        name = str(item.get("name") or "")
        hint = str(item.get("hint") or "")
        score = score_industry_match(name, hint, blob)
        if score > best_score or (score == best_score and score > 0 and len(name) > len(best_name)):
            best_score = score
            best_name = name
    if best_score >= 6:
        return best_name
    return best_label


def _resolve_item_industry(
    text: str,
    *,
    preferred: str = "",
    force_preferred: bool = False,
    allow_tool_map: bool = False,
    fallback: str = "综合/未标注",
) -> str:
    """
    解析单条内容的行业标签。

    Args:
        text: 正文。
        preferred: 用户在「看热点」选择的行业（筛选/聚焦，不是全局强贴）。
        force_preferred: True 时直接用 preferred（痛点/AI重构在用户选定行业时）。
        allow_tool_map: 是否按工具语义映射到 SaaS 行业。
        fallback: 最终兜底（不得用牙科目录首项）。

    Returns:
        行业名。
    """

    preferred = (preferred or "").strip()
    if force_preferred and preferred:
        return preferred

    matched = _match_industry(text)
    if matched:
        # 用户筛选行业时：仅保留相关条目由上层过滤；此处仍返回真实匹配
        return matched

    if allow_tool_map:
        tool_ind = _tool_industry_from_text(text)
        if tool_ind:
            return tool_ind

    if preferred:
        return preferred
    return fallback


def _diversified_industries(preferred: str = "", *, limit: int = 10) -> list[str]:
    """
    生成痛点通道用的行业列表：默认用创投标准所属行业交错取样。

    Args:
        preferred: 用户选定行业（置顶并缩小范围）。
        limit: 数量。

    Returns:
        行业名列表。
    """

    preferred = (preferred or "").strip()
    if preferred:
        return [preferred]

    market = [str(x.get("name") or "") for x in list_cn_vc_market_industries() if x.get("name")]
    if not market:
        market = [x[0] for x in _INDUSTRY_RULES[:16]]
    return market[:limit]


def build_rule_insight_lanes(
    *,
    platform_rows: list[dict[str, Any]],
    vc_rows: list[dict[str, Any]],
    github_rows: list[dict[str, Any]],
    industry: str = "",
    limit_each: int = 10,
) -> dict[str, list[dict[str, Any]]]:
    """
    不依赖 LLM：用中国本土热搜/创投 + GitHub 全网项目，按 SeekMoney 框架生成三通道内容。

    行业规则：
    - industry 为空：全网，不做过滤
    - industry 有值：仅保留与该行业关键词相关的热搜/创投/线索；痛点与 AI 重构聚焦该行业；
      AI 重构场景必须与工具能力匹配（禁止 JavaGuide→预约获客这类错配）

    Args:
        platform_rows: 平台热搜（调用方宜已按行业过滤）。
        vc_rows: 创投条目（调用方宜已按行业过滤）。
        github_rows: GitHub/本土项目。
        industry: 用户行业偏好（空=全网）。
        limit_each: 每通道上限。

    Returns:
        {seekmoney, pain, ai_rebuild}
    """

    preferred = (industry or "").strip()
    platform_rows = _clean_signal_rows(platform_rows)
    vc_rows = _clean_signal_rows(vc_rows)
    github_rows = [g for g in github_rows if not str(g.get("title") or "").startswith(("商机：", "线索：", "痛点：", "AI重构："))]

    if preferred:
        platform_rows = _filter_by_industry(platform_rows, preferred)
        vc_rows = _filter_by_industry(vc_rows, preferred)

    # SeekMoney：结构化可验证商机（禁止「围绕新闻标题」套模板）
    seekmoney = build_seekmoney_clues(
        platform_rows=platform_rows,
        vc_rows=vc_rows,
        github_rows=github_rows,
        industry=preferred,
        limit=limit_each,
    )
    pain: list[dict[str, Any]] = []
    ai_rebuild: list[dict[str, Any]] = []

    industries = _diversified_industries(preferred, limit=10 if not preferred else 1)
    pain_templates = [
        ("获客成本高、到店转化低", "门店老板/增长负责人", "预约落地页+核销+回访脚本"),
        ("人工跟进线索慢、漏单多", "销售/顾问", "线索池+自动提醒+话术模板"),
        ("内容获客不稳定、难复用", "运营", "爆款结构模板+批量改写+投放试金石"),
        ("合规/发票/单据耗时长", "店长/财务", "单据 OCR+审核清单两周试点"),
        ("知识沉淀弱、培训靠口传", "店长/培训", "知识库问答+入职清单"),
        ("排班/库存靠经验、波动大", "运营", "简单预测规则+异常提醒"),
    ]
    pain_pairs: list[tuple[str, tuple[str, str, str]]] = []
    if preferred:
        for tpl in pain_templates[:6]:
            pain_pairs.append((preferred, tpl))
    else:
        for j, ind_name in enumerate(industries[:8]):
            pain_pairs.append((ind_name, pain_templates[j % len(pain_templates)]))

    anchor_pool = [g for g in (github_rows + vc_rows) if str(g.get("title") or "").strip()]
    used_anchors: set[str] = set()

    for j, (ind_name, tpl) in enumerate(pain_pairs):
        anchor = None
        pain_keys = tpl[0]
        for g in anchor_pool:
            key = str(g.get("url") or g.get("title") or "")
            if key in used_anchors:
                continue
            blob = f"{g.get('title')} {g.get('snippet')}"
            job = _best_job_for_tool(blob)
            related = (
                any(k in job[0] for k in ("预约", "线索", "内容", "单据", "知识", "排班") if k in pain_keys)
                or _row_matches_industry(g, ind_name)
            )
            if related:
                anchor = g
                used_anchors.add(key)
                break
        if not anchor and anchor_pool:
            for offset in range(len(anchor_pool)):
                g = anchor_pool[(j + offset) % len(anchor_pool)]
                key = str(g.get("url") or g.get("title") or "")
                if key in used_anchors:
                    continue
                anchor = g
                used_anchors.add(key)
                break
        if not anchor and anchor_pool:
            anchor = anchor_pool[j % len(anchor_pool)]

        url = str((anchor or {}).get("url") or f"leadforge://pain/{j}")
        ref_name = str((anchor or {}).get("title") or (anchor or {}).get("name") or "开源对标")[:40]
        pain.append(
            _as_hotspot(
                title=f"痛点：{ind_name} · {tpl[0]}",
                url=url,
                snippet=(
                    f"付费方:{tpl[1]} · 两周:{tpl[2]} · 对标:{ref_name} · "
                    f"{'行业筛选聚焦' if preferred else '全网多样行业'}"
                )[:220],
                provider="insight:pain",
                heat=70 - j,
                lane="pain",
                meta={
                    "lane": "pain",
                    "platform_label": "行业痛点",
                    "industry": ind_name,
                    "surface_pain": tpl[0],
                    "who_pays": tpl[1],
                    "mvp_2w": tpl[2],
                    "anchor_project": ref_name,
                    "industry_basis": "user_filter" if preferred else "catalog_diversified",
                    "confidence": 0.6,
                    "methodology": "seekmoney+opc",
                },
            )
        )

    scene_industries = _diversified_industries("", limit=12)
    for j, g in enumerate(github_rows[:12]):
        title = str(g.get("title") or "").strip()
        if not title:
            continue
        raw = f"{title} {g.get('snippet') or ''}"
        job = _best_job_for_tool(raw)
        tool_ind = _resolve_item_industry(raw, allow_tool_map=True, fallback="SaaS·开发者工具")
        if preferred:
            scene = preferred
        else:
            scene = tool_ind if tool_ind else scene_industries[j % max(1, len(scene_industries))]
            if tool_ind.startswith("教育") or "guide" in raw.lower() or "javaguide" in raw.lower():
                scene = "教育·阅读学习工具"
        ai_rebuild.append(
            _as_hotspot(
                title=f"AI重构：用「{title[:22]}」重做{scene}的{job[0]}",
                url=str(g.get("url") or f"leadforge://ai/{j}"),
                snippet=(
                    f"工具能力→{job[0]} · 落地场景:{scene} · 付费方:{job[1]} · "
                    f"{job[2]} · 两周：fork 跑通→换领域数据→5 人试用"
                )[:220],
                provider="insight:ai_rebuild",
                heat=float(g.get("heat") or 40) + 12,
                lane="ai_rebuild",
                meta={
                    "lane": "ai_rebuild",
                    "platform_label": "AI重构",
                    "industry": scene,
                    "tool_industry": tool_ind,
                    "surface_pain": job[0],
                    "who_pays": job[1],
                    "mvp_2w": job[2],
                    "github": title,
                    "industry_basis": "user_filter+tool_job" if preferred else "tool_job",
                    "confidence": 0.62,
                    "methodology": "opc+github",
                },
            )
        )

    if len(ai_rebuild) < 4:
        for j, v in enumerate(vc_rows[:6]):
            title = str(v.get("title") or "").strip()
            if not title:
                continue
            raw = f"{title} {v.get('snippet') or ''}"
            job = _best_job_for_tool(raw)
            ind = preferred or _resolve_item_industry(raw, allow_tool_map=True, fallback="本地服务")
            ai_rebuild.append(
                _as_hotspot(
                    title=f"AI重构：借鉴「{title[:22]}」改造{ind}的{job[0]}",
                    url=str(v.get("url") or f"leadforge://ai/vc/{j}"),
                    snippet=f"创投信号 · 工具能力→{job[0]} · 付费方:{job[1]} · {job[2]}"[:200],
                    provider="insight:ai_rebuild",
                    heat=45 + j,
                    lane="ai_rebuild",
                    meta={
                        "lane": "ai_rebuild",
                        "platform_label": "AI重构",
                        "industry": ind,
                        "who_pays": job[1],
                        "mvp_2w": job[2],
                        "industry_basis": "user_filter+tool_job" if preferred else "tool_job",
                        "confidence": 0.5,
                    },
                )
            )

    def _cap(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            t = str(r.get("title") or "")
            if t in seen:
                continue
            seen.add(t)
            out.append(r)
            if len(out) >= limit_each:
                break
        return out

    return {
        "seekmoney": _cap(seekmoney),
        "pain": _cap(pain),
        "ai_rebuild": _cap(ai_rebuild),
    }


async def build_hotspot_lanes(
    *,
    industry: str = "",
    per_platform: int = 30,
    refresh: bool = False,
    include_ai_lanes: bool = False,
) -> dict[str, Any]:
    """
    构建多维热点页签数据包。

    industry 为空：全网热搜/创投/线索，不做行业过滤。
    industry 有值：平台热搜、创投、商机线索、痛点、AI重构均按行业关键词关联筛选。
    """

    preferred = (industry or "").strip()
    newsnow_ids = {p[0] for p in NEWSNOW_PLATFORMS} | {
        "weibo", "zhihu", "baidu", "toutiao", "douyin", "bilibili", "cls", "wallstreetcn", "thepaper", "tieba", "ifeng",
    }

    def _is_platform_hot_row(row: dict[str, Any]) -> bool:
        """是否为平台热搜（排除创投/洞察/GitHub）。"""

        provider = str(row.get("provider") or "").lower()
        channel = str(row.get("channel") or "").lower()
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        plat = str(meta.get("platform") or "")
        if channel in ("cn_vc", "cyzone", "cid_indie", "github", "seekmoney", "pain", "ai_rebuild", "hackernews"):
            return False
        if provider.startswith(("cyzone", "pitchhub", "rss:", "cid", "seekmoney", "insight:", "github")):
            return False
        if provider.startswith(("trendradar", "newsnow")):
            return True
        if plat in newsnow_ids:
            return True
        if channel == "trendradar":
            return True
        return False

    # 1) 平台热搜：库内不足时主动拉 newsnow（全网默认也拉，避免平台热搜为 0）
    warm_topic = preferred or "创业"
    need_live = bool(refresh)
    cached = [
        r
        for r in _clean_signal_rows(query_hotspots(channel="trendradar", region="cn", limit=400))
        if _is_platform_hot_row(r)
    ]
    if preferred:
        cached = _filter_by_industry(cached, preferred)
    # 按平台估算：不足约 8 平台 × 10 条则强制刷新
    if len(cached) < 80:
        need_live = True

    per_plat = max(10, min(int(per_platform or 30), 50))
    if need_live:
        try:
            await fetch_hotspots_cached(
                topic=warm_topic,
                channel="trendradar",
                region="cn",
                limit=max(240, per_plat * 8),
                use_cache=False,
                refresh=True,
            )
        except Exception:  # noqa: BLE001
            try:
                from app.tools.newsnow_hotspots import collect_newsnow_hotspots

                nw = await collect_newsnow_hotspots(
                    limit=max(240, per_plat * 8),
                    per_platform=per_plat,
                )
                upsert_hotspots(nw.get("items") or [], batch_source="newsnow")
            except Exception:  # noqa: BLE001
                pass

    platform_rows = [
        r
        for r in _clean_signal_rows(
            query_hotspots(channel="trendradar", region="cn", topic=preferred, limit=400)
        )
        if _is_platform_hot_row(r)
    ]
    if len(platform_rows) < per_plat:
        extra = [
            r
            for r in _clean_signal_rows(query_hotspots(limit=400))
            if _is_platform_hot_row(r)
        ]
        seen = {(r.get("url") or r.get("title") or "") for r in platform_rows}
        for r in extra:
            key = r.get("url") or r.get("title") or ""
            if key and key not in seen:
                platform_rows.append(r)
                seen.add(key)

    if preferred:
        platform_rows = _filter_by_industry(platform_rows, preferred)
        if len(platform_rows) < 3:
            keys = _industry_keywords(preferred)
            broadened = [
                r
                for r in _clean_signal_rows(query_hotspots(limit=400))
                if _is_platform_hot_row(r) and any(k in _row_content_blob(r) for k in keys)
            ]
            if broadened:
                platform_rows = broadened

    # 组内按名次排序，每平台取前 per_plat 名
    platforms_grouped = group_hotspots_by_platform(platform_rows, per_platform=per_plat)

    # 2) 创投 + GitHub（并行；创投按行业关键词采集）
    gh_topic = f"{preferred} AI" if preferred else "AI SaaS 本地生活"
    vc_rows, github_rows = await asyncio.gather(
        collect_vc_hotspot_items(limit=24, industry=preferred),
        collect_github_hotspot_items(topic=gh_topic, industry=preferred, limit=24),
    )
    if preferred:
        vc_rows = _filter_by_industry(vc_rows, preferred)
        # 创投过少时保留采集结果中带行业词的，不再回退全网以免「筛选失效」
    if vc_rows:
        upsert_hotspots(vc_rows, batch_source="vc")
    if github_rows:
        upsert_hotspots(github_rows, batch_source="github")

    # 3) 规则通道
    ruled = build_rule_insight_lanes(
        platform_rows=platform_rows,
        vc_rows=vc_rows,
        github_rows=github_rows,
        industry=preferred,
        limit_each=12,
    )
    seekmoney_rows = list(ruled.get("seekmoney") or [])
    pain_rows = list(ruled.get("pain") or [])
    ai_rows = list(ruled.get("ai_rebuild") or [])

    # 4) 可选 LLM 加深（信号也按行业）
    if include_ai_lanes:
        signals = _signal_lines_from_rows(
            list(platform_rows[:20]) + list(vc_rows[:10]) + list(github_rows[:10])
        )
        if signals:
            try:
                ai_seek, ai_pain, ai_ai = await asyncio.gather(
                    _ai_lane_cards(lane="seekmoney", signal_lines=signals, industry=preferred, limit=6),
                    _ai_lane_cards(lane="pain", signal_lines=signals, industry=preferred, limit=6),
                    _ai_lane_cards(lane="ai_rebuild", signal_lines=signals, industry=preferred, limit=6),
                )
                if ai_seek:
                    seekmoney_rows = (ai_seek if not preferred else _filter_by_industry(ai_seek, preferred)) + seekmoney_rows
                if ai_pain:
                    pain_rows = (ai_pain if not preferred else _filter_by_industry(ai_pain, preferred)) + pain_rows
                if ai_ai:
                    ai_rows = (ai_ai if not preferred else _filter_by_industry(ai_ai, preferred)) + ai_rows
            except Exception:  # noqa: BLE001
                pass

    # 去重截断
    def _uniq(rows: list[dict[str, Any]], n: int = 12) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            t = str(r.get("title") or "")
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(r)
            if len(out) >= n:
                break
        return out

    seekmoney_rows = _uniq(seekmoney_rows)
    pain_rows = _uniq(pain_rows)
    ai_rows = _uniq(ai_rows)

    for batch, src in (
        (seekmoney_rows, "seekmoney"),
        (pain_rows, "pain"),
        (ai_rows, "ai_rebuild"),
    ):
        if batch:
            upsert_hotspots(batch, batch_source=src)

    scope_desc = f"聚焦「{preferred}」" if preferred else "全网（未选行业）"
    lanes = [
        {
            "id": "platforms",
            "label": "平台热搜",
            "desc": f"{scope_desc} · 微博/知乎/百度等真实热榜",
            "kind": "platforms",
            "count": int(platforms_grouped.get("total") or 0),
            "platforms": platforms_grouped.get("platforms") or [],
            "items": [],
        },
        {
            "id": "seekmoney",
            "label": "商机线索",
            "desc": (
                f"{scope_desc} · SeekMoney 框架：表面痛点→根因→付费方→两周MVP"
                "（热搜仅作弱证据，拒绝吃瓜新闻套壳）"
            ),
            "kind": "list",
            "count": len(seekmoney_rows),
            "items": seekmoney_rows,
            "ref": "https://github.com/liangdabiao/SeekMoney-ai",
        },
        {
            "id": "vc",
            "label": "创投热点",
            "desc": f"{scope_desc} · 36氪 / 创业邦 / PitchHub",
            "kind": "list",
            "count": len(vc_rows),
            "items": vc_rows,
        },
        {
            "id": "pain",
            "label": "行业痛点",
            "desc": f"{scope_desc} · 可验证作业痛点",
            "kind": "list",
            "count": len(pain_rows),
            "items": pain_rows,
        },
        {
            "id": "ai_rebuild",
            "label": "AI重构",
            "desc": f"{scope_desc} · 工具能力匹配作业场景",
            "kind": "list",
            "count": len(ai_rows),
            "items": ai_rows,
        },
    ]

    total = sum(int(x.get("count") or 0) for x in lanes)
    return {
        "ok": True,
        "lanes": lanes,
        "lane_count": len(lanes),
        "total": total,
        "industry": preferred,
        "scope": "filtered" if preferred else "all",
        "from_cache": not refresh,
        "generated_at": _now(),
        "stats": warehouse_stats(),
        "methodologies": ["seekmoney", "opc"],
        "sources": {
            "platforms": len(platform_rows),
            "vc": len(vc_rows),
            "github": len(github_rows),
        },
    }


def flatten_lane_items(lanes_payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    将多维通道结果展平为热点条目列表（供商机提炼等下游复用）。

    Args:
        lanes_payload: build_hotspot_lanes 返回值，或 lanes 列表本身。

    Returns:
        展平后的热点条目（平台热搜按平台展开）。
    """

    if isinstance(lanes_payload, dict):
        lanes = list(lanes_payload.get("lanes") or [])
    else:
        lanes = list(lanes_payload or [])

    out: list[dict[str, Any]] = []
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        kind = str(lane.get("kind") or "")
        lane_id = str(lane.get("id") or "")
        if kind == "platforms" or lane_id == "platforms":
            for plat in lane.get("platforms") or []:
                if not isinstance(plat, dict):
                    continue
                for item in plat.get("items") or []:
                    if isinstance(item, dict):
                        out.append(item)
            continue
        for item in lane.get("items") or []:
            if isinstance(item, dict):
                out.append(item)
    return out
