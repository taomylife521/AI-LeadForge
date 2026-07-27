# -*- coding: utf-8 -*-
"""
热点 → 商机主题 → 可落地项目 一键流水线。

作用: 汇总多平台真实热搜，用 AI 提炼可靠、可落地实操的商机，并匹配可落地项目。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.theme_recommend import recommend_topics
from app.tools.landable_filter import landable_portfolio_for_opportunity
from app.tools.methodology_playbooks import (
    enrich_opportunity_with_method_fields,
    methodology_context_block,
    seekmoney_opportunity_system_prompt,
)
from app.tools.newsnow_hotspots import NEWSNOW_PLATFORMS


def _platform_of(row: dict[str, Any]) -> tuple[str, str]:
    """
    解析热点所属平台 id 与展示名。

    Args:
        row: 热点条目。

    Returns:
        (platform_id, platform_label)
    """

    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    pid = str(meta.get("platform") or "").strip()
    label = str(meta.get("platform_label") or "").strip()
    if not pid:
        provider = str(row.get("provider") or "")
        if ":" in provider:
            pid = provider.split(":", 1)[1].strip()
        elif provider:
            pid = provider
    name_map = {p: lab for p, lab in NEWSNOW_PLATFORMS}
    if not label:
        label = name_map.get(pid, "") or str(row.get("channel_label") or pid or "其他")
    return pid or "other", label or "其他"


def group_hotspots_by_platform(
    items: list[dict[str, Any]],
    *,
    per_platform: int = 30,
) -> dict[str, Any]:
    """
    将热点按平台分组（供页签展示），每平台按名次升序、热度降序排列。

    Args:
        items: 热点列表。
        per_platform: 每平台条数上限（默认前 30 名）。

    Returns:
        {platforms:[{id,label,count,items}], total}
    """

    cap = max(1, min(int(per_platform or 30), 50))
    buckets: dict[str, dict[str, Any]] = {}
    order = [p for p, _ in NEWSNOW_PLATFORMS]

    def _sort_key(row: dict[str, Any]) -> tuple[int, float]:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        try:
            rank = int(meta.get("rank") or 0)
        except (TypeError, ValueError):
            rank = 0
        if rank <= 0:
            rank = 9999
        try:
            heat = float(row.get("heat") or 0)
        except (TypeError, ValueError):
            heat = 0.0
        # 名次越小越靠前；同名次按热度降序
        return (rank, -heat)

    for row in items:
        if not isinstance(row, dict):
            continue
        if not (row.get("title") or row.get("name")):
            continue
        pid, label = _platform_of(row)
        if pid not in buckets:
            buckets[pid] = {"id": pid, "label": label, "items": []}
        buckets[pid]["items"].append(row)

    for b in buckets.values():
        b["items"].sort(key=_sort_key)
        # 同平台去重：优先保留名次更靠前的标题
        seen_titles: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for row in b["items"]:
            title = str(row.get("title") or row.get("name") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            uniq.append(row)
            if len(uniq) >= cap:
                break
        b["items"] = uniq

    platforms: list[dict[str, Any]] = []
    for pid in order:
        if pid in buckets:
            b = buckets.pop(pid)
            platforms.append(
                {
                    "id": b["id"],
                    "label": b["label"],
                    "count": len(b["items"]),
                    "items": b["items"],
                }
            )
    for pid, b in sorted(buckets.items(), key=lambda x: -len(x[1]["items"])):
        platforms.append(
            {
                "id": b["id"],
                "label": b["label"],
                "count": len(b["items"]),
                "items": b["items"],
            }
        )
    total = sum(int(p["count"]) for p in platforms)
    return {"platforms": platforms, "total": total, "platform_count": len(platforms)}


async def extract_opportunities_from_hotspots(
    *,
    hotspots: list[dict[str, Any]],
    industry: str = "",
    theme_pack: str = "",
    limit: int = 5,
) -> dict[str, Any]:
    """
    用 AI 从多平台热点提炼可靠、可落地实操的商机列表。

    Args:
        hotspots: 热点条目。
        industry: 行业偏好（可选）。
        theme_pack: Theme Pack。
        limit: 商机条数上限。

    Returns:
        {opportunities, hotspots_used, platforms_covered, note}
    """

    rows = [h for h in (hotspots or []) if isinstance(h, dict) and (h.get("title") or h.get("name"))]
    if not rows:
        raise ValueError("请至少提供一条热点")

    # 按平台整理给模型
    grouped = group_hotspots_by_platform(rows, per_platform=12)
    lines: list[str] = []
    for plat in grouped.get("platforms") or []:
        lines.append(f"【{plat.get('label')}】")
        for i, h in enumerate(plat.get("items") or [], 1):
            heat = h.get("heat")
            heat_s = f" heat={int(float(heat))}" if heat is not None else ""
            lines.append(f"  {i}. {str(h.get('title') or '')[:80]}{heat_s}")

    system = seekmoney_opportunity_system_prompt()
    user = (
        f"{methodology_context_block()}\n"
        f"行业偏好: {industry or '不限（从热点自行判断）'}\n"
        f"场景包: {theme_pack or 'local-service-leadgen'}\n"
        f"请提炼 {max(2, min(limit, 6))} 条最可靠可落地商机（SeekMoney 评分 + OPC 一人可交付）：\n"
        + "\n".join(lines)[:4500]
    )

    opportunities: list[dict[str, Any]] = []
    used_mock = False
    try:
        llm = LLMClient()
        payload, _model, used_mock = await llm.complete_json(
            route=ModelRoute.TIER_M,
            system=system,
            user=user,
            allow_mock=False,
            temperature=0.2,
        )
        if used_mock:
            raise RuntimeError("拒绝使用 mock 模型输出")
        raw_items = payload.get("opportunities") if isinstance(payload, dict) else []
        for item in raw_items or []:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            try:
                conf = float(item.get("confidence") or 0.5)
            except (TypeError, ValueError):
                conf = 0.5
            enriched = enrich_opportunity_with_method_fields(
                {
                    "title": title[:120],
                    "industry": str(item.get("industry") or industry or "")[:60],
                    "why_reliable": str(item.get("why_reliable") or "")[:300],
                    "who_pays": str(item.get("who_pays") or "")[:120],
                    "mvp_2w": str(item.get("mvp_2w") or "")[:240],
                    "risk": str(item.get("risk") or "")[:200],
                    "confidence": max(0.0, min(1.0, conf)),
                    "signal_platforms": [
                        str(x) for x in (item.get("signal_platforms") or []) if x
                    ][:6],
                    "source_titles": [
                        str(x) for x in (item.get("source_titles") or []) if x
                    ][:6],
                    "surface_pain": item.get("surface_pain"),
                    "root_cause": item.get("root_cause"),
                    "user_scenario": item.get("user_scenario"),
                    "demand_score": item.get("demand_score"),
                    "market_score": item.get("market_score"),
                    "competition_score": item.get("competition_score"),
                    "priority_score": item.get("priority_score"),
                    "validation_hypothesis": item.get("validation_hypothesis"),
                    "first_users": item.get("first_users"),
                    "opc_fit": item.get("opc_fit"),
                    "data_quality": item.get("data_quality"),
                }
            )
            opportunities.append(enriched)
    except Exception as exc:  # noqa: BLE001
        # 规则兜底：取各平台 Top1 拼主题建议（仍非 mock LLM 编造）
        seed_titles = [str(h.get("title") or "")[:40] for h in rows[:5] if h.get("title")]
        if seed_titles:
            opportunities.append(
                enrich_opportunity_with_method_fields(
                    {
                        "title": f"围绕「{seed_titles[0][:24]}」的本地服务获客验证",
                        "industry": industry or "本地服务",
                        "why_reliable": f"AI 提炼暂不可用（{str(exc)[:80]}），基于热搜标题的保守方向",
                        "who_pays": "本地到店商家",
                        "mvp_2w": "落地页+预约表单+投放小预算验证咨询量",
                        "risk": "需人工确认热搜与行业相关性",
                        "confidence": 0.35,
                        "signal_platforms": [p.get("label") for p in (grouped.get("platforms") or [])[:3]],
                        "source_titles": seed_titles[:3],
                        "surface_pain": seed_titles[0],
                        "root_cause": "待验证",
                        "user_scenario": "本地到店获客",
                        "opc_fit": "适合一人用开源预约页+表单两周验证",
                        "data_quality": "exploratory",
                        "fallback": True,
                    }
                )
            )

    opportunities.sort(
        key=lambda x: (
            float(x.get("priority_score") or 0),
            float(x.get("confidence") or 0),
        ),
        reverse=True,
    )
    opportunities = opportunities[: max(1, min(limit, 8))]

    return {
        "ok": True,
        "opportunities": opportunities,
        "count": len(opportunities),
        "hotspots_used": rows[:40],
        "platforms_covered": [
            {"id": p.get("id"), "label": p.get("label"), "count": p.get("count")}
            for p in (grouped.get("platforms") or [])
        ],
        "used_mock": used_mock,
        "methodologies": ["seekmoney", "opc"],
        "note": "已按 SeekMoney 痛点框架 + 一人企业方法论提炼可落地商机。",
    }


async def hotspot_to_opportunity_pack(
    *,
    hotspots: list[dict[str, Any]],
    industry: str = "",
    theme_pack: str = "",
    generate_plan: bool = True,
    use_ai: bool = True,
    opportunity_title: str = "",
) -> dict[str, Any]:
    """
    从热点列表生成商机 + 可落地项目。

    Args:
        hotspots: 热点条目（title/url/snippet/provider）。
        industry: 行业提示。
        theme_pack: Theme Pack id（可选）。
        generate_plan: 是否生成落地方案。
        use_ai: 可落地筛选是否用 AI。
        opportunity_title: 若已选定商机标题则直接使用。

    Returns:
        {topic, industry, hotspots_used, themes, portfolio, landable_items, landing_plan, note}
    """

    rows = [h for h in (hotspots or []) if isinstance(h, dict) and (h.get("title") or h.get("name"))]
    if not rows:
        raise ValueError("请至少选择一条热点")

    titles = [str(h.get("title") or h.get("name") or "").strip() for h in rows[:8]]
    titles = [t for t in titles if t]
    seed = " / ".join(titles[:3])
    direction = (opportunity_title or titles[0])[:80]

    themes: dict[str, Any] = {}
    topic = direction
    industry_out = industry

    if opportunity_title.strip():
        topic = opportunity_title.strip()[:120]
    else:
        try:
            themes = await recommend_topics(
                theme_pack=theme_pack or "local-service-leadgen",
                hint=seed[:120],
                industry=industry,
                use_llm=True,
                limit=5,
            )
        except Exception as exc:  # noqa: BLE001
            themes = {"items": [], "error": str(exc)[:200]}

        rec_items = themes.get("items") or themes.get("recommendations") or []
        if rec_items and isinstance(rec_items[0], dict):
            topic = str(
                rec_items[0].get("topic")
                or rec_items[0].get("name")
                or rec_items[0].get("title")
                or direction
            ).strip()
        ind = themes.get("industry")
        if isinstance(ind, dict):
            industry_out = str(ind.get("name") or industry)
        elif themes.get("industry_name"):
            industry_out = str(themes["industry_name"])

    ctx = "热点来源:\n" + "\n".join(
        f"- {h.get('title')} ({_platform_of(h)[1]}) {h.get('url')}" for h in rows[:8]
    )
    linked = await landable_portfolio_for_opportunity(
        topic=topic,
        industry=industry_out,
        opportunity_context=ctx[:1200],
        generate_plan=generate_plan,
        use_ai=use_ai,
    )
    return {
        "ok": True,
        "topic": topic,
        "industry": industry_out,
        "direction_seed": seed,
        "hotspots_used": rows[:8],
        "themes": themes,
        "portfolio": linked.get("portfolio"),
        "landable_items": linked.get("landable_items") or [],
        "landable_count": linked.get("landable_count") or 0,
        "landing_plan": linked.get("landing_plan"),
        "note": linked.get("note") or "仅推荐可落地项目",
    }
