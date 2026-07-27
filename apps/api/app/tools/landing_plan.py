# -*- coding: utf-8 -*-
"""
从推荐项目 / 项目组合一键生成落地方案。

作用: 依据商机主题 + 选定项目（真实链接与摘要），用 LLM 输出可执行 MVP 落地方案（禁止 mock 编造融资/用户量）。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.tools.methodology_playbooks import (
    merge_methodology_projects,
    methodology_context_block,
    opc_landing_system_prompt,
)
from app.tools.project_match import build_landing_portfolio, rank_projects_for_theme
from app.tools.project_recommend import recommend_landing_projects


def _project_lines(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for i, row in enumerate(items[:8], 1):
        lines.append(
            f"{i}. [{row.get('portfolio_role') or '组件'}] {row.get('name')} | "
            f"相关分={row.get('relevance_score')} | {row.get('url')}\n"
            f"   摘要: {(row.get('summary') or '')[:160]}\n"
            f"   命中: {', '.join(row.get('relevance_matched') or [])}"
        )
    return "\n".join(lines) or "(无项目)"


async def generate_landing_plan(
    *,
    topic: str,
    industry: str = "",
    opportunity_context: str = "",
    projects: Optional[list[dict[str, Any]]] = None,
    combo_title: str = "",
    use_ai: bool = True,
) -> dict[str, Any]:
    """
    生成落地方案。

    Args:
        topic: 商机主题。
        industry: 行业。
        opportunity_context: 决策台/狩猎摘要（可选）。
        projects: 已选项目；空则无法生成具体仓库步骤。
        combo_title: 组合名（可选）。

    Returns:
        含 plan 结构化字段与 markdown。
    """

    topic = (topic or "").strip()
    if not topic:
        raise ValueError("商机主题不能为空")
    projects = [p for p in (projects or []) if isinstance(p, dict) and p.get("url")]
    projects = merge_methodology_projects(projects)
    ranked = rank_projects_for_theme(projects, topic=topic, industry=industry, min_score=0.0, limit=10)
    if not ranked and projects:
        ranked = projects[:8]
    # 保证方法论项目至少有一条出现在方案引用中
    method_urls = {
        "https://github.com/liangdabiao/SeekMoney-ai",
        "https://github.com/easychen/opc-methodology",
    }
    have = {str(p.get("url") or "").rstrip("/") for p in ranked}
    for p in projects:
        u = str(p.get("url") or "").rstrip("/")
        if u in method_urls and u not in have:
            ranked.append(p)
            have.add(u)

    fallback = _heuristic_plan(topic=topic, industry=industry, projects=ranked, combo_title=combo_title)
    if not use_ai:
        return fallback

    system = opc_landing_system_prompt()
    user = (
        f"{methodology_context_block()}\n"
        f"商机主题: {topic}\n行业: {industry}\n组合: {combo_title or '自定义'}\n"
        f"商机上下文:\n{(opportunity_context or '')[:1200]}\n\n"
        f"选定项目（含方法论锚点）:\n{_project_lines(ranked)}\n"
    )
    try:
        llm = LLMClient()
        payload, model_name, used_mock = await llm.complete_json(
            route=ModelRoute.TIER_M,
            system=system,
            user=user,
            allow_mock=False,
            temperature=0.25,
        )
        if used_mock or not isinstance(payload, dict):
            fallback["method"] = "rules_fallback"
            fallback["ai_error"] = "mock_or_empty"
            return fallback
        md = str(payload.get("markdown") or "").strip()
        if not md:
            md = _format_markdown(payload, topic=topic, projects=ranked)
        return {
            "ok": True,
            "method": "ai",
            "model": model_name,
            "topic": topic,
            "industry": industry,
            "combo_title": combo_title,
            "projects": ranked,
            "plan": payload,
            "markdown": md,
        }
    except Exception as exc:  # noqa: BLE001
        fallback["method"] = "rules_fallback"
        fallback["ai_error"] = str(exc)[:200]
        return fallback


def _heuristic_plan(
    *,
    topic: str,
    industry: str,
    projects: list[dict[str, Any]],
    combo_title: str,
) -> dict[str, Any]:
    """无模型时的结构化兜底方案。"""

    reuse_map = []
    for p in projects[:5]:
        reuse_map.append(
            {
                "project": p.get("name"),
                "role": p.get("portfolio_role") or "组件",
                "reuse": f"直接参考/fork：{p.get('url')}",
                "modify": "替换领域文案与数据模型，对齐本商机付费方与合规要求",
            }
        )
    plan = {
        "title": f"{topic} · 一人公司落地方案",
        "one_liner": f"按 OPC 从副业起步，复用 {len(projects)} 个对标/方法论项目，两周验证（{industry or '垂直场景'}）。",
        "opc_canvas": {
            "who": "首批可用用户（副业可触达）",
            "offer": topic,
            "channel": "内容触达 + 私域/表单",
            "advantage": "一人可交付、模板化交付、不硬碰红海",
        },
        "reuse_map": reuse_map,
        "mvp_2w": [
            "day1-3: 用 SeekMoney 框架写清痛点/假设；选定主仓库 fork 跑通",
            "day4-7: 改领域模型，接最小表单与收款/预约；搭最小用户触达",
            "day8-14: 找 5 个真实用户试用，量转化与支付意愿，决定加投或止损",
        ],
        "infra_min": [
            "用户触达: 微信/社群/落地页表单",
            "内容: 一篇痛点验证内容",
            "产品: 两周 MVP 页面或脚本",
            "收款: 微信/支付宝收款码或简单订阅",
        ],
        "tech_stack": ["优先沿用主仓库技术栈", "合规文案人工抽检"],
        "validation_metrics": ["预约/线索完成数", "付费意向访谈数", "CAC 试算"],
        "stop_loss": "两周内无法约到 5 个目标用户深度反馈则降级或换切口",
        "next_actions": ["确认付费方", "选定主仓库", "写验证假设（SeekMoney）", "约首批用户"],
    }
    return {
        "ok": True,
        "method": "rules",
        "topic": topic,
        "industry": industry,
        "combo_title": combo_title,
        "projects": projects,
        "plan": plan,
        "markdown": _format_markdown(plan, topic=topic, projects=projects),
    }


def _format_markdown(plan: dict[str, Any], *, topic: str, projects: list[dict[str, Any]]) -> str:
    """把结构化 plan 拼成 Markdown。"""

    lines = [
        f"# {plan.get('title') or topic + ' 落地方案'}",
        "",
        plan.get("one_liner") or "",
        "",
        "## 项目复用映射",
    ]
    for row in plan.get("reuse_map") or []:
        lines.append(
            f"- **{row.get('project')}**（{row.get('role')}）\n"
            f"  - 复用：{row.get('reuse')}\n"
            f"  - 改造：{row.get('modify')}"
        )
    lines += ["", "## 两周 MVP"]
    for step in plan.get("mvp_2w") or []:
        lines.append(f"- {step}")
    lines += ["", "## 验证指标"]
    for m in plan.get("validation_metrics") or []:
        lines.append(f"- {m}")
    lines += ["", f"## 止损\n{plan.get('stop_loss') or '—'}", "", "## 下一步"]
    for a in plan.get("next_actions") or []:
        lines.append(f"- {a}")
    if projects:
        lines += ["", "## 选定项目链接"]
        for p in projects:
            lines.append(f"- [{p.get('name')}]({p.get('url')})")
    return "\n".join(lines)


async def portfolio_and_plan_for_opportunity(
    *,
    topic: str,
    industry: str = "",
    opportunity_context: str = "",
    candidate_projects: Optional[list[dict[str, Any]]] = None,
    generate_plan: bool = True,
) -> dict[str, Any]:
    """
    对候选项目做主题匹配 → 组合 →（可选）生成落地方案。
    """

    topic = (topic or "").strip()
    industry = (industry or "").strip()
    candidates = list(candidate_projects or [])
    if len(candidates) < 6:
        pack = await recommend_landing_projects(
            topic=topic,
            industry=industry,
            limit=28,
            mode="projects",
            use_cache=True,
            force_refresh=False,
        )
        # 合并去重
        seen = {str(c.get("url")) for c in candidates}
        for row in pack.get("items") or []:
            u = str(row.get("url") or "")
            if u and u not in seen:
                candidates.append(row)
                seen.add(u)

    portfolio = build_landing_portfolio(candidates, topic=topic, industry=industry)
    # 缓存全是宽行业误伤时：强制刷新一次全网检索
    if (portfolio.get("ranked_count") or 0) == 0 and topic:
        pack2 = await recommend_landing_projects(
            topic=topic,
            industry=industry,
            limit=28,
            mode="projects",
            use_cache=False,
            force_refresh=True,
        )
        seen = {str(c.get("url")) for c in candidates}
        for row in pack2.get("items") or []:
            u = str(row.get("url") or "")
            if u and u not in seen:
                candidates.append(row)
                seen.add(u)
        portfolio = build_landing_portfolio(candidates, topic=topic, industry=industry)

    plan = None
    # 默认用核心组合生成方案
    combo = (portfolio.get("combos") or [None])[0]
    if generate_plan and combo:
        plan = await generate_landing_plan(
            topic=topic,
            industry=industry,
            opportunity_context=opportunity_context,
            projects=combo.get("items") or portfolio.get("singles") or [],
            combo_title=str(combo.get("title") or ""),
            use_ai=True,
        )
    elif generate_plan and portfolio.get("singles"):
        plan = await generate_landing_plan(
            topic=topic,
            industry=industry,
            opportunity_context=opportunity_context,
            projects=portfolio.get("singles") or [],
            combo_title="高相关单项目",
            use_ai=True,
        )
    return {
        "topic": topic,
        "industry": industry,
        "portfolio": portfolio,
        "landing_plan": plan,
        "candidate_count": len(candidates),
    }
