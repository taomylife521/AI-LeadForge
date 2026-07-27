# -*- coding: utf-8 -*-
"""
商机主题 ↔ 落地项目相关性匹配与组合。

作用: 按主题/行业/痛点对项目打分，过滤无关项；组装可落地该商机的单项目或多项目组合。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import re
from typing import Any, Optional


# 领域同义词：扩展检索/匹配召回
_DOMAIN_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("dental", ("牙科", "口腔", "种植", "矫正", "齿科", "诊所", "dental", "dentist", "orthodont")),
    ("booking", ("预约", "排班", "挂号", "booking", "appointment", "schedule", "calendar")),
    ("clinic", ("诊所", "门诊", "到店", "clinic", "salon", "门店")),
    ("ltv", ("ltv", "复购", "生命周期", "会员", "留存", "lifetime")),
    ("compliance", ("合规", "医疗广告", "审校", "红队", "compliance", "hipaa", "资质")),
    ("crm", ("crm", "客户管理", "线索", "跟进", "私域")),
    ("saas", ("saas", "订阅", "b2b", "软件")),
    ("ai_agent", ("agent", "智能体", "大模型", "llm", "gpt", "ai")),
    ("health", ("医疗", "健康", "康复", "医美", "health", "medical")),
    ("tutor", ("家教", "辅导", "tutor", "教育", "学习", "课程")),
]

# 弱通用词：单独命中不算「可落地相关」
_WEAK_TOKENS = {
    "agent", "ai", "saas", "llm", "gpt", "平台", "系统", "方案", "服务",
    "智能体", "大模型", "software", "app", "tool", "tools",
}

# 噪音项目：通用书/课程/awesome 列表，不当「可落地」主仓
_NOISE_HINTS = (
    "awesome-",
    "ai-agent-book",
    "understanding-ai",
    "教程",
    "读书笔记",
    "interview",
    "leetcode",
    "cheat-sheet",
)

# 跨域冲突：主题命中某域时，项目若偏另一域则扣分
_CROSS_DOMAIN_PENALTIES: list[tuple[tuple[str, ...], tuple[str, ...], float]] = [
    (("dental", "牙科", "口腔", "诊所", "齿科"), ("tutor", "家教", "辅导", "教育", "课程", "learning", "student"), 35.0),
    (("dental", "牙科", "口腔", "医疗", "诊所"), ("game", "游戏", "nft", "crypto"), 30.0),
    (("clinic", "门诊", "到店"), ("tutor", "教育", "课程", "mooc"), 25.0),
]



def _topic_niche_groups(topic: str = "", industry: str = "") -> list[tuple[str, ...]]:
    """
    若主题命中强细分域，返回必须至少命中一组别名（否则视为行业误伤）。

    Returns:
        若干别名元组列表；空表示无强制细分门槛。
    """

    blob = f"{topic or ''} {industry or ''}"
    blob_l = blob.lower()
    groups: list[tuple[str, ...]] = []
    # 牙科/口腔：不能只靠「医疗健康」行业标签
    dental = ("牙科", "口腔", "种植", "矫正", "齿科", "dental", "dentist", "orthodont")
    if any(a.lower() in blob_l or a in blob for a in dental):
        groups.append(dental + ("诊所", "clinic"))
    return groups


def extract_theme_tokens(topic: str = "", industry: str = "", extra: Optional[list[str]] = None) -> list[str]:
    """
    从商机主题抽取匹配用关键词（去停用、保留领域词）。

    Returns:
        去重后的 token 列表（小写/原样中文）。
    """

    stop = {
        "的", "和", "与", "及", "或", "对", "基于", "通过", "一个", "一种", "进行", "实现",
        "the", "and", "for", "with", "from", "into", "that", "this", "your", "our",
        "agent", "ai", "saas", "平台", "系统", "方案", "服务", "驱动",
    }
    blob = f"{topic or ''} {industry or ''} {' '.join(extra or [])}"
    # 英文词 + 中文 2+ 字片段
    raw = re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}|[\u4e00-\u9fff]{2,}", blob)
    tokens: list[str] = []
    seen: set[str] = set()
    for t in raw:
        key = t.lower()
        if key in stop or len(t) < 2:
            continue
        if key not in seen:
            seen.add(key)
            tokens.append(t if re.search(r"[\u4e00-\u9fff]", t) else key)
    # 展开领域别名
    expanded: list[str] = list(tokens)
    blob_l = blob.lower()
    for _label, aliases in _DOMAIN_ALIASES:
        if any(a.lower() in blob_l or a in blob for a in aliases):
            for a in aliases:
                if a not in seen and len(a) >= 2:
                    seen.add(a.lower())
                    expanded.append(a)
    return expanded[:40]


def score_project_relevance(
    row: dict[str, Any],
    *,
    topic: str = "",
    industry: str = "",
    tokens: Optional[list[str]] = None,
) -> dict[str, Any]:
    """
    计算项目与商机主题的相关分。

    Returns:
        {score, matched_tokens, noise, role_hint}
    """

    tokens = tokens or extract_theme_tokens(topic, industry)
    blob = " ".join(
        str(x or "")
        for x in (
            row.get("name"),
            row.get("summary"),
            row.get("industry"),
            " ".join(row.get("industry_niches") or []),
            " ".join(row.get("pain_tags") or []),
            row.get("how_to_use"),
        )
    )
    blob_l = blob.lower()
    name_l = str(row.get("name") or "").lower()

    noise = any(n in name_l or n in blob_l for n in _NOISE_HINTS)
    matched: list[str] = []
    score = 0.0
    strong_hits = 0
    for tok in tokens:
        t = tok.lower()
        if len(t) < 2:
            continue
        if t in blob_l or tok in blob:
            matched.append(tok)
            weak = t in _WEAK_TOKENS
            # 名称命中加权；弱词降权
            if weak:
                weight = 3.0 if (t in name_l or tok in str(row.get("name") or "")) else 1.5
            else:
                weight = 18.0 if (t in name_l or tok in str(row.get("name") or "")) else 10.0
                strong_hits += 1
                if len(tok) >= 4:
                    weight += 4.0
            score += weight

    # 仅弱词命中：几乎无关（如教育 Tutor 撞上 Agent 商机）
    if matched and strong_hits == 0:
        score = min(score, 6.0)

    # 行业字段直接命中
    ind = str(row.get("industry") or "")
    if industry and (industry in ind or industry.lower() in blob_l):
        score += 25.0
        strong_hits += 1
        if industry not in matched:
            matched.append(industry)

    # 跨域惩罚（牙科主题不应推教育 Tutor）
    topic_blob = f"{topic} {industry}".lower()
    for topic_keys, proj_keys, penalty in _CROSS_DOMAIN_PENALTIES:
        if any(k.lower() in topic_blob or k in f"{topic}{industry}" for k in topic_keys):
            if any(k.lower() in blob_l or k in blob for k in proj_keys):
                score -= penalty

    # 细分赛道门槛：主题含牙科等强词时，项目必须命中细分，否则行业标签分无效
    niche_ok = True
    for group in _topic_niche_groups(topic, industry):
        if not any(a.lower() in blob_l or a in blob for a in group):
            niche_ok = False
            break
    if not niche_ok:
        # 丢掉宽行业/弱词抬分，只保留真实细分命中（此处为 0）
        score = min(score * 0.15, 8.0)
        strong_hits = 0

    # 一键可用加分；纯文档/书减分
    if row.get("one_click_ready") and niche_ok:
        score += 12.0
    if noise:
        score -= 40.0
    kind = str(row.get("kind") or "")
    if kind == "github_repo" and float(row.get("stars") or 0) < 5 and score < 20:
        score -= 8.0

    role = _infer_role(blob_l, matched) if strong_hits > 0 and niche_ok else "通用参考"
    return {
        "score": round(max(0.0, score), 2),
        "matched_tokens": matched[:12],
        "noise": noise,
        "role_hint": role,
        "strong_hits": strong_hits,
        "niche_ok": niche_ok,
    }


def _infer_role(blob_l: str, matched: list[str]) -> str:
    """推断项目在组合中的角色。"""

    if any(k in blob_l for k in ("booking", "appointment", "预约", "排班", "schedule")):
        return "预约/排班底座"
    if any(k in blob_l for k in ("crm", "线索", "lead", "客户管理")):
        return "客户/线索 CRM"
    if any(k in blob_l for k in ("compliance", "合规", "审校", "广告法")):
        return "合规/内容审校"
    if any(k in blob_l for k in ("ltv", "会员", "复购", "retention")):
        return "LTV/复购运营"
    if any(k in blob_l for k in ("agent", "llm", "chat", "客服", "rag")):
        return "AI Agent 能力层"
    if any(k in blob_l for k in ("billing", "支付", "订阅", "stripe", "invoice")):
        return "计费/订阅"
    if matched:
        return "领域能力组件"
    return "通用参考"


def rank_projects_for_theme(
    items: list[dict[str, Any]],
    *,
    topic: str = "",
    industry: str = "",
    min_score: float = 12.0,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """
    按主题相关性重排并过滤低分/噪音项目。

    Returns:
        带 relevance_* 字段的项目列表。
    """

    tokens = extract_theme_tokens(topic, industry)
    ranked: list[tuple[float, dict[str, Any]]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        rel = score_project_relevance(row, topic=topic, industry=industry, tokens=tokens)
        if rel["noise"] and rel["score"] < min_score + 10:
            continue
        if rel["score"] < min_score and tokens:
            # 无任何命中则丢弃
            if not rel["matched_tokens"]:
                continue
        # 仅弱词命中且无强命中：不进入推荐
        if tokens and int(rel.get("strong_hits") or 0) == 0 and rel["score"] < min_score + 6:
            continue
        if rel.get("niche_ok") is False and rel["score"] < min_score + 4:
            continue
        out = {
            **row,
            "relevance_score": rel["score"],
            "relevance_matched": rel["matched_tokens"],
            "portfolio_role": rel["role_hint"],
        }
        # 综合分：相关为主，热度为辅
        heat = float(row.get("heat") or row.get("stars") or 0)
        ranked.append((rel["score"] * 10 + min(heat, 200) * 0.05, out))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in ranked[: max(1, min(limit, 40))]]


def build_landing_portfolio(
    items: list[dict[str, Any]],
    *,
    topic: str = "",
    industry: str = "",
    max_combos: int = 3,
    max_per_combo: int = 4,
) -> dict[str, Any]:
    """
    组装可落地该商机的项目组合（角色互补，避免全是同类）。

    Returns:
        {singles, combos, topic, industry}
    """

    ranked = rank_projects_for_theme(items, topic=topic, industry=industry, min_score=8.0, limit=24)
    # 组合只收高相关，避免弱词撞车项目进落地包
    eligible = [r for r in ranked if float(r.get("relevance_score") or 0) >= 18]
    singles = eligible[:6]

    # 按角色分桶
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in eligible:
        role = str(row.get("portfolio_role") or "通用参考")
        if role == "通用参考":
            continue
        buckets.setdefault(role, []).append(row)

    preferred_order = [
        "预约/排班底座",
        "客户/线索 CRM",
        "AI Agent 能力层",
        "合规/内容审校",
        "LTV/复购运营",
        "计费/订阅",
        "领域能力组件",
    ]
    combos: list[dict[str, Any]] = []
    # Combo A：高相关单主仓 + 互补
    if eligible:
        primary = eligible[0]
        partners: list[dict[str, Any]] = [primary]
        used = {str(primary.get("url") or "")}
        for role in preferred_order:
            for cand in buckets.get(role, []):
                u = str(cand.get("url") or "")
                if u in used:
                    continue
                if role == primary.get("portfolio_role") and len(partners) == 1:
                    continue
                partners.append(cand)
                used.add(u)
                break
            if len(partners) >= max_per_combo:
                break
        combos.append(
            {
                "id": "combo-core",
                "title": "核心落地组合",
                "rationale": f"围绕「{topic or industry or '商机'}」：主能力 + 互补组件，减少从零造轮子。",
                "items": partners[:max_per_combo],
            }
        )

    # Combo B：一键可用优先
    easy = [r for r in eligible if r.get("one_click_ready")][:max_per_combo]
    if len(easy) >= 2:
        combos.append(
            {
                "id": "combo-fast",
                "title": "快速验证组合（一键/可试用）",
                "rationale": "优先可部署/可试用仓库，适合 1–2 周 MVP。",
                "items": easy,
            }
        )

    # Combo C：开源底座 + 创投对标
    gh = [r for r in eligible if "github" in str(r.get("source") or "").lower()][:2]
    vc = [r for r in eligible if r.get("kind") == "startup_project"][:2]
    mix = gh + [x for x in vc if str(x.get("url")) not in {str(g.get("url")) for g in gh}]
    if len(mix) >= 2:
        combos.append(
            {
                "id": "combo-benchmark",
                "title": "开源底座 + 创投对标",
                "rationale": "开源可 fork，创投项目看商业化路径与定价。",
                "items": mix[:max_per_combo],
            }
        )

    return {
        "topic": topic,
        "industry": industry,
        "singles": singles,
        "combos": combos[:max_combos],
        "ranked_count": len(ranked),
        "tokens_used": extract_theme_tokens(topic, industry)[:16],
    }
