# -*- coding: utf-8 -*-
"""
商机方法论融入：SeekMoney 痛点发现 + 一人企业（OPC）方法论。

作用: 将开源方法论提炼为 Agent 提示词与评审清单，驱动商机提炼与落地方案；
      不 fork 整仓，而是把可执行原则编入 LeadForge 推理链路。
参考:
  - https://github.com/liangdabiao/SeekMoney-ai
  - https://github.com/easychen/opc-methodology
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

from typing import Any

# 方法论锚点项目（可进项目库 / 落地方案引用）
METHODOLOGY_PROJECTS: list[dict[str, Any]] = [
    {
        "name": "SeekMoney-ai",
        "url": "https://github.com/liangdabiao/SeekMoney-ai",
        "summary": "从社交媒体找商机：多平台采集用户痛点，语义聚类 + AI 深度分析需求强度/市场规模/竞争度，输出 MVP 计划。",
        "source": "github_methodology",
        "source_label": "方法论",
        "kind": "github_repo",
        "pain_tags": ["痛点发现", "商机挖掘", "用户需求", "聚类分析"],
        "audience_tags": ["创业者", "独立开发者", "产品经理"],
        "industry_niches": ["SaaS·创作者工具", "本地·到店预约核销"],
        "one_click_ready": False,
        "heat": 90.0,
        "stars": 0,
        "portfolio_role": "商机发现方法",
        "how_to_use": "用其痛点分析框架审视热搜与评论：表面痛点→根因→场景→付费意愿→MVP。",
        "region": "中国",
        "company_nature": "开源项目",
        "methodology": "seekmoney",
    },
    {
        "name": "一人企业方法论 opc-methodology",
        "url": "https://github.com/easychen/opc-methodology",
        "summary": "一人企业完整方法论：赛道选择、不竞争策略、副产品优势、从副业起步、基础设施（用户池/内容池/产品池/支付）。",
        "source": "github_methodology",
        "source_label": "方法论",
        "kind": "github_repo",
        "pain_tags": ["一人公司", "独立开发", "副业", "被动收入"],
        "audience_tags": ["独立开发者", "一人公司", "副业创业"],
        "industry_niches": ["SaaS·创作者工具", "内容·小红书获客"],
        "one_click_ready": False,
        "heat": 95.0,
        "stars": 0,
        "portfolio_role": "一人公司落地方法",
        "how_to_use": "用 OPC 画布约束方案：小而美、可一人交付、先副业验证、结构化优势而非硬碰硬。",
        "region": "中国",
        "company_nature": "开源项目",
        "methodology": "opc",
    },
]


def seekmoney_opportunity_system_prompt() -> str:
    """
    SeekMoney 风格：从热搜提炼痛点商机的系统提示。

    Returns:
        系统提示词。
    """

    return (
        "你是「找商机」顾问，方法论对齐 SeekMoney-ai（痛点发现器）。\n"
        "从各平台真实热搜提炼可落地商机时，必须按 SeekMoney 框架思考：\n"
        "1) 表面痛点 → 根本原因 → 用户场景 → 情感强度\n"
        "2) 市场格局：现有方案 → 未满足需求 → 机会缺口\n"
        "3) 优先级：需求强度 + 市场规模 + 竞争度（综合排序）\n"
        "4) 数据质量：样本少则标注 exploratory；信号跨平台交叉则更可靠\n"
        "5) MVP：核心功能、验证假设、首批用户、成本可控\n"
        "同时对齐一人企业（OPC）约束：适合一人/小团队两周验证；优先副业可启动；"
        "避免重资本、重运营、强竞争红海；强调副产品优势与不竞争策略。\n"
        "拒绝：纯吃瓜娱乐无付费闭环、宏观口号、无法两周验证的方向。\n"
        "禁止编造热搜未出现的数据。\n"
        '输出 JSON: {"opportunities":[{"title":"...","industry":"...","why_reliable":"...",'
        '"surface_pain":"...","root_cause":"...","user_scenario":"...",'
        '"demand_score":0-5,"market_score":0-5,"competition_score":0-5,"priority_score":0-15,'
        '"who_pays":"...","mvp_2w":"...","validation_hypothesis":"...","first_users":"...",'
        '"opc_fit":"为何适合一人/小团队","risk":"...","confidence":0.0-1.0,'
        '"signal_platforms":["微博"],"source_titles":["..."],'
        '"data_quality":"exploratory|preliminary|reliable"}]}'
    )


def opc_landing_system_prompt() -> str:
    """
    一人企业方法论风格：落地方案系统提示。

    Returns:
        系统提示词。
    """

    return (
        "你是一人企业落地教练，方法论对齐 easychen/opc-methodology（《一人企业方法论》）。\n"
        "根据商机与真实开源/创投项目输出可执行落地方案，必须体现：\n"
        "1) 从副业/小范围起步，控制不确定性与止损\n"
        "2) 不竞争策略 + 结构化优势（时间/渠道/技能副产品），避免硬碰红海\n"
        "3) 一人可交付：两周 MVP 里程碑；能自动化/模板化的优先\n"
        "4) 基础设施四池：用户池与触达、内容池、产品池、支付能力——写清最小搭建\n"
        "5) 若引用 SeekMoney 框架：说明如何用痛点验证假设与首批用户\n"
        "只依据给定项目链接与摘要；禁止编造星标、融资、用户量。\n"
        "输出 JSON："
        '{"title":"...","one_liner":"...","opc_canvas":{"who":"...","offer":"...","channel":"...","advantage":"..."},'
        '"reuse_map":[{"project":"...","role":"...","reuse":"...","modify":"..."}],'
        '"mvp_2w":["day1-3:...","day4-7:...","day8-14:..."],'
        '"infra_min":["用户触达:...","内容:...","产品:...","收款:..."],'
        '"tech_stack":["..."],"validation_metrics":["..."],'
        '"stop_loss":"...","next_actions":["..."],"markdown":"完整Markdown方案"}'
    )


def methodology_context_block() -> str:
    """
    供用户消息附带的方法论短摘要。

    Returns:
        多行文本。
    """

    return (
        "方法论锚点:\n"
        f"- SeekMoney-ai: {METHODOLOGY_PROJECTS[0]['url']} — 痛点发现与优先级评分\n"
        f"- opc-methodology: {METHODOLOGY_PROJECTS[1]['url']} — 一人企业画布与基础设施\n"
    )


def enrich_opportunity_with_method_fields(item: dict[str, Any]) -> dict[str, Any]:
    """
    规范化 SeekMoney/OPC 扩展字段。

    Args:
        item: 模型返回的商机条目。

    Returns:
        补齐字段后的条目。
    """

    out = dict(item)
    for key in (
        "surface_pain",
        "root_cause",
        "user_scenario",
        "validation_hypothesis",
        "first_users",
        "opc_fit",
        "data_quality",
    ):
        if key in out and out[key] is not None:
            out[key] = str(out[key])[:240]
    for key in ("demand_score", "market_score", "competition_score", "priority_score"):
        try:
            out[key] = float(out.get(key) or 0)
        except (TypeError, ValueError):
            out[key] = 0.0
    dq = str(out.get("data_quality") or "exploratory").lower()
    if dq not in ("exploratory", "preliminary", "reliable"):
        out["data_quality"] = "exploratory"
    out["methodologies"] = ["seekmoney", "opc"]
    return out


def merge_methodology_projects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    将方法论项目并入候选列表（按 URL 去重，置顶方法论文档）。

    Args:
        rows: 原项目列表。

    Returns:
        合并后的列表。
    """

    seen = {str(r.get("url") or "").rstrip("/") for r in rows if isinstance(r, dict)}
    merged = list(METHODOLOGY_PROJECTS)
    for row in rows:
        if not isinstance(row, dict):
            continue
        u = str(row.get("url") or "").rstrip("/")
        if u and u in seen and any(u == str(m.get("url") or "").rstrip("/") for m in METHODOLOGY_PROJECTS):
            continue
        if u and any(u == str(m.get("url") or "").rstrip("/") for m in METHODOLOGY_PROJECTS):
            continue
        merged.append(row)
    return merged
