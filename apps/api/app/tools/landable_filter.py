# -*- coding: utf-8 -*-
"""
AI 可落地性筛选。

作用: 在主题相关性排序之后，用 LLM（禁止 mock）判定项目是否适合作为该商机的落地底座；
      仅保留 landable=true 的条目供 UI / 决策台展示。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.tools.landing_plan import generate_landing_plan, portfolio_and_plan_for_opportunity
from app.tools.methodology_playbooks import merge_methodology_projects
from app.tools.project_match import build_landing_portfolio, rank_projects_for_theme


async def filter_landable_projects(
    *,
    topic: str,
    industry: str = "",
    projects: list[dict[str, Any]],
    use_ai: bool = True,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    过滤出可落地项目。

    Args:
        topic: 商机主题。
        industry: 行业。
        projects: 候选项目（建议已含 relevance_*）。
        use_ai: 是否调用 LLM；False 时用规则启发式。
        limit: 返回上限。

    Returns:
        带 landable / landable_reason / landable_role 的项目列表（仅 landable=true）。
    """

    topic = (topic or "").strip()
    ranked = rank_projects_for_theme(
        [p for p in projects if isinstance(p, dict)],
        topic=topic,
        industry=industry,
        min_score=12.0,
        limit=max(limit, 16),
    )
    if not ranked:
        return []

    if not use_ai:
        return _heuristic_landable(ranked, topic=topic, industry=industry)[:limit]

    system = (
        "你是中国创业落地评审。根据商机主题判断每个开源/创投项目是否适合作为「可落地底座」"
        "（可 fork/改造出两周 MVP）。拒绝：教程/书/awesome 列表、跨领域误伤、纯资讯。"
        "只依据给定摘要，禁止编造星标与用户量。"
        '输出 JSON: {"items":[{"url":"...","landable":true,"reason":"...","role":"预约底座|CRM|合规|Agent|其他"}]}'
    )
    lines = []
    for i, p in enumerate(ranked[:14], 1):
        lines.append(
            f"{i}. url={p.get('url')} | name={p.get('name')} | "
            f"score={p.get('relevance_score')} | role={p.get('portfolio_role')} | "
            f"summary={(p.get('summary') or '')[:140]}"
        )
    user = f"商机: {topic}\n行业: {industry}\n候选:\n" + "\n".join(lines)
    try:
        llm = LLMClient()
        payload, _model, used_mock = await llm.complete_json(
            route=ModelRoute.TIER_M,
            system=system,
            user=user,
            allow_mock=False,
            temperature=0.15,
        )
        if used_mock or not isinstance(payload, dict):
            return _heuristic_landable(ranked, topic=topic, industry=industry)[:limit]
        by_url = {
            str(x.get("url") or ""): x
            for x in (payload.get("items") or [])
            if isinstance(x, dict)
        }
        out: list[dict[str, Any]] = []
        for p in ranked:
            u = str(p.get("url") or "")
            verdict = by_url.get(u) or {}
            landable = bool(verdict.get("landable")) if verdict else float(p.get("relevance_score") or 0) >= 28
            if not landable:
                continue
            out.append(
                {
                    **p,
                    "landable": True,
                    "landable_reason": str(verdict.get("reason") or "相关分达标且适合改造")[:200],
                    "landable_role": str(verdict.get("role") or p.get("portfolio_role") or "组件")[:40],
                }
            )
            if len(out) >= limit:
                break
        return out
    except Exception:  # noqa: BLE001
        return _heuristic_landable(ranked, topic=topic, industry=industry)[:limit]


def _heuristic_landable(
    ranked: list[dict[str, Any]],
    *,
    topic: str,
    industry: str,
) -> list[dict[str, Any]]:
    """无模型时：相关分≥22 且非噪音角色视为可落地。"""

    out: list[dict[str, Any]] = []
    for p in ranked:
        score = float(p.get("relevance_score") or 0)
        role = str(p.get("portfolio_role") or "")
        if score < 22:
            continue
        if role == "通用参考" and score < 35:
            continue
        out.append(
            {
                **p,
                "landable": True,
                "landable_reason": f"规则: 相关分 {score} · {role or '领域组件'}",
                "landable_role": role or "领域能力组件",
            }
        )
    return out


async def landable_portfolio_for_opportunity(
    *,
    topic: str,
    industry: str = "",
    opportunity_context: str = "",
    candidate_projects: Optional[list[dict[str, Any]]] = None,
    generate_plan: bool = True,
    use_ai: bool = True,
) -> dict[str, Any]:
    """
    组合匹配 + AI 可落地过滤；portfolio 的 singles/combos 仅含 landable 项目。
    """

    linked = await portfolio_and_plan_for_opportunity(
        topic=topic,
        industry=industry,
        opportunity_context=opportunity_context,
        candidate_projects=candidate_projects,
        generate_plan=False,
    )
    portfolio = linked.get("portfolio") or {}
    pool: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in list(portfolio.get("singles") or []):
        u = str(row.get("url") or "")
        if u and u not in seen:
            pool.append(row)
            seen.add(u)
    for combo in portfolio.get("combos") or []:
        for row in combo.get("items") or []:
            u = str(row.get("url") or "")
            if u and u not in seen:
                pool.append(row)
                seen.add(u)
    if candidate_projects:
        for row in candidate_projects:
            if isinstance(row, dict):
                u = str(row.get("url") or "")
                if u and u not in seen:
                    pool.append(row)
                    seen.add(u)

    pool = merge_methodology_projects(pool)

    landable = await filter_landable_projects(
        topic=topic,
        industry=industry,
        projects=pool,
        use_ai=use_ai,
        limit=12,
    )
    # 用可落地子集重建组合
    rebuilt = build_landing_portfolio(landable, topic=topic, industry=industry)
    rebuilt["singles"] = landable[:6]
    # combos 内再滤一遍
    new_combos = []
    landable_urls = {str(x.get("url")) for x in landable}
    for combo in rebuilt.get("combos") or []:
        items = [i for i in (combo.get("items") or []) if str(i.get("url")) in landable_urls]
        if items:
            new_combos.append({**combo, "items": items})
    rebuilt["combos"] = new_combos

    plan = None
    if generate_plan and (new_combos or landable):
        src = (new_combos[0]["items"] if new_combos else landable) or []
        plan = await generate_landing_plan(
            topic=topic,
            industry=industry,
            opportunity_context=opportunity_context,
            projects=src,
            combo_title=(new_combos[0]["title"] if new_combos else "可落地单项目"),
            use_ai=True,
        )

    return {
        "topic": topic,
        "industry": industry,
        "portfolio": rebuilt,
        "landing_plan": plan,
        "landable_items": landable,
        "landable_count": len(landable),
        "candidate_count": linked.get("candidate_count") or len(pool),
        "note": "仅展示与当前商机匹配、可落地的项目。",
    }
