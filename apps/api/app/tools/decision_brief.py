# -*- coding: utf-8 -*-
"""
HITL 决策台深度分析：单位经济、五维验证、专家质询、合规与退出。

作用: 在商业模式确认前产出可交互测算与 VC 级风险穿透字段（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.tools.expert_framework import (
    expert_brief_json_schema,
    heuristic_expert_pack,
    normalize_risk_with_expert,
)
from app.tools.landable_filter import landable_portfolio_for_opportunity
from app.tools.project_recommend import recommend_landing_projects


def _friendly_model_error(exc: BaseException) -> str:
    """把模型解析失败转成决策台可读说明（避免把原始 JSON 错堆进非共识）。"""

    msg = str(exc)
    if "无法解析 JSON" in msg or "Unterminated string" in msg or "Expecting" in msg:
        return (
            "深度模型输出过长被截断或 JSON 损坏，已降级为启发式简报。"
            "请点「刷新穿透简报」或换更大上下文模型后重试。"
        )
    return msg[:220]


def _slim_for_brief(opportunity: dict[str, Any], business_model: dict[str, Any], redteam: dict[str, Any]) -> dict[str, Any]:
    """压缩送入模型的载荷，降低输出截断概率。"""

    opps = opportunity.get("opportunities") if isinstance(opportunity.get("opportunities"), list) else []
    slim_opps = []
    for row in opps[:2]:
        if not isinstance(row, dict):
            continue
        slim_opps.append(
            {
                k: row.get(k)
                for k in (
                    "name",
                    "pain",
                    "pain_core",
                    "pain_level",
                    "audience_segment",
                    "industry_niche",
                    "who_pays",
                    "validate_ease",
                    "willingness_to_pay",
                    "competition_gap",
                    "stop_loss_hint",
                    "current_alternative",
                )
                if row.get(k) is not None
            }
        )
    hunt = opportunity.get("hunt_report") if isinstance(opportunity.get("hunt_report"), dict) else {}
    hunt_slim = {
        "recommended_topic": hunt.get("recommended_topic"),
        "recommended_opportunity": hunt.get("recommended_opportunity"),
        "executive_summary": str(hunt.get("executive_summary") or "")[:400],
        "phase4_verdict": hunt.get("phase4_verdict"),
        "phase3_preferred": (hunt.get("phase3_rebuild") or {}).get("preferred_model")
        if isinstance(hunt.get("phase3_rebuild"), dict)
        else None,
    }
    return {
        "opportunity": {
            "recommended": opportunity.get("recommended"),
            "recommendation": opportunity.get("recommendation"),
            "selection_rationale": str(opportunity.get("selection_rationale") or "")[:400],
            "opportunities": slim_opps,
            "competitors": (opportunity.get("competitors") or [])[:5],
            "hunt_report": hunt_slim,
        },
        "business_model": {
            "positioning": business_model.get("positioning"),
            "pricing": business_model.get("pricing"),
            "unit_economics": business_model.get("unit_economics"),
            "budget_cap_test_cny": business_model.get("budget_cap_test_cny"),
        },
        "redteam": {
            "passed": redteam.get("passed"),
            "gate": redteam.get("gate"),
            "issues": (redteam.get("issues") or [])[:6],
            "soft_issues": (redteam.get("soft_issues") or redteam.get("warnings") or [])[:6],
        },
    }


def calc_unit_economics(inputs: dict[str, Any]) -> dict[str, Any]:
    """
    动态测算单位经济与回本（纯计算，无 LLM）。

    Args:
        inputs: 可含 price_cny, ad_spend, sales_commission, channel_rebate,
            demo_labor_cost, delivery_labor_hours, labor_hourly_cny,
            bad_case_rate, monthly_fixed_cost, rd_amortization_cny,
            monthly_orders, funding_cost_per_order, bad_debt_reserve_rate,
            ltv_months, gross_margin, test_budget_cny 等。

    Returns:
        含 CAC、交付边际成本、贡献利润、回本月数、客户数门槛、现金流警示。
    """

    def f(key: str, default: float = 0.0) -> float:
        try:
            val = inputs.get(key, default)
            if val is None or val == "":
                return float(default)
            return float(val)
        except (TypeError, ValueError):
            return float(default)

    price = max(0.0, f("price_cny", 199))
    ad = max(0.0, f("ad_spend", 50))
    commission = max(0.0, f("sales_commission", 0))
    rebate = max(0.0, f("channel_rebate", 0))
    demo_labor = max(0.0, f("demo_labor_cost", 0))
    hours = max(0.0, f("delivery_labor_hours", 0.5))
    hourly = max(0.0, f("labor_hourly_cny", 80))
    bad_case_rate = min(1.0, max(0.0, f("bad_case_rate", 0.15)))
    monthly_fixed = max(0.0, f("monthly_fixed_cost", 3000))
    rd_amort = max(0.0, f("rd_amortization_cny", 5000))
    monthly_orders = max(0.0, f("monthly_orders", 20))
    funding_cost = max(0.0, f("funding_cost_per_order", 0))
    bad_debt_rate = min(1.0, max(0.0, f("bad_debt_reserve_rate", 0.03)))
    ltv_months = max(1.0, f("ltv_months", 6))
    margin = min(0.95, max(0.05, f("gross_margin", 0.55)))
    test_budget = max(0.0, f("test_budget_cny", 200))

    cac = ad + commission + rebate + demo_labor
    delivery_base = hours * hourly
    delivery_bad_case = delivery_base * bad_case_rate * 2.0
    delivery_cost = delivery_base + delivery_bad_case
    variable_cost = delivery_cost + funding_cost + price * bad_debt_rate
    contribution_ex_cac = price * margin - variable_cost
    contribution_incl_cac = contribution_ex_cac - cac
    ltv = price * margin * ltv_months
    ltv_cac = (ltv / cac) if cac > 0 else None

    customers_for_test = None
    if contribution_ex_cac > 0:
        customers_for_test = test_budget / contribution_ex_cac
    customers_for_test_rd = None
    if contribution_ex_cac > 0:
        customers_for_test_rd = (test_budget + rd_amort) / contribution_ex_cac

    monthly_net = monthly_orders * contribution_ex_cac - (monthly_orders * cac)
    payback_months = None
    if monthly_net > 0:
        payback_months = (monthly_fixed + rd_amort) / monthly_net

    is_outsourcing_risk = delivery_cost > price * 0.35 or hours >= 2.0

    alerts: list[str] = []
    if payback_months is not None and payback_months > 12:
        alerts.append(f"回本约 {payback_months:.1f} 个月，超过 12 个月警戒线，建议提价或找预付客户。")
    if ltv_cac is not None and ltv_cac < 3:
        alerts.append(f"LTV/CAC={ltv_cac:.2f} < 3，单位经济偏弱（铁律未达标）。")
    if is_outsourcing_risk:
        alerts.append("人工兜底成本过高，模式接近外包而非可规模化产品。")
    if contribution_incl_cac <= 0:
        alerts.append("含获客后单笔净贡献≤0，当前定价/成本结构不可持续。")
    if monthly_net <= 0:
        alerts.append("月净贡献≤0，无法自然回本，需降 CAC 或提客单。")

    status = "ok"
    if any("不可持续" in a or "≤0" in a for a in alerts) or is_outsourcing_risk:
        status = "block"
    elif alerts:
        status = "warn"

    return {
        "inputs_used": {
            "price_cny": price,
            "ad_spend": ad,
            "sales_commission": commission,
            "channel_rebate": rebate,
            "demo_labor_cost": demo_labor,
            "delivery_labor_hours": hours,
            "labor_hourly_cny": hourly,
            "bad_case_rate": bad_case_rate,
            "monthly_fixed_cost": monthly_fixed,
            "rd_amortization_cny": rd_amort,
            "monthly_orders": monthly_orders,
            "funding_cost_per_order": funding_cost,
            "bad_debt_reserve_rate": bad_debt_rate,
            "ltv_months": ltv_months,
            "gross_margin": margin,
            "test_budget_cny": test_budget,
        },
        "cac_full": round(cac, 2),
        "delivery_marginal_cost": round(delivery_cost, 2),
        "variable_cost_per_order": round(variable_cost, 2),
        "contribution_ex_cac": round(contribution_ex_cac, 2),
        "contribution_incl_cac": round(contribution_incl_cac, 2),
        "ltv": round(ltv, 2),
        "ltv_cac": round(ltv_cac, 2) if ltv_cac is not None else None,
        "ltv_cac_rule": (
            "pass" if (ltv_cac is not None and ltv_cac >= 3) else ("fail" if ltv_cac is not None else "unknown")
        ),
        "customers_to_cover_test_budget": round(customers_for_test, 1) if customers_for_test else None,
        "customers_to_cover_test_and_rd": round(customers_for_test_rd, 1) if customers_for_test_rd else None,
        "monthly_net_contribution": round(monthly_net, 2),
        "payback_months": round(payback_months, 1) if payback_months is not None else None,
        "is_outsourcing_risk": is_outsourcing_risk,
        "status": status,
        "alerts": alerts,
        "formula_note": (
            "CAC=广告+提成+返点+Demo人力；交付边际=工时×时薪×(1+BadCase加成)；"
            "回本=(固定成本+研发摊销)/月净贡献；铁律 LTV≥3×CAC。"
        ),
    }


def _seed_inputs_from_payloads(
    opportunity: dict[str, Any],
    business_model: dict[str, Any],
) -> dict[str, Any]:
    """从商机/模式载荷抽取计算器初始值。"""

    pricing = business_model.get("pricing") if isinstance(business_model.get("pricing"), dict) else {}
    plans = pricing.get("plans") if isinstance(pricing.get("plans"), list) else []
    price = 199.0
    if plans and isinstance(plans[0], dict):
        try:
            price = float(plans[0].get("price_cny") or price)
        except (TypeError, ValueError):
            pass
    ue = business_model.get("unit_economics") if isinstance(business_model.get("unit_economics"), dict) else {}
    cpl = 35.0
    try:
        cpl = float(pricing.get("cpl_target_cny") or cpl)
    except (TypeError, ValueError):
        pass
    budget = 200.0
    try:
        budget = float(business_model.get("budget_cap_test_cny") or budget)
    except (TypeError, ValueError):
        pass
    margin = 0.55
    try:
        margin = float(ue.get("gross_margin_est") or margin)
    except (TypeError, ValueError):
        pass

    blob = f"{opportunity.get('recommended') or ''} {business_model.get('positioning') or ''}".lower()
    fintechish = any(k in blob for k in ("分期", "消金", "放贷", "保险", "信贷", "种植"))
    return {
        "price_cny": price,
        "ad_spend": cpl,
        "sales_commission": 20 if fintechish else 10,
        "channel_rebate": 30 if fintechish else 0,
        "demo_labor_cost": 80 if fintechish else 40,
        "delivery_labor_hours": 1.5 if fintechish else 0.4,
        "labor_hourly_cny": 100 if fintechish else 80,
        "bad_case_rate": 0.25 if fintechish else 0.12,
        "monthly_fixed_cost": 5000 if fintechish else 3000,
        "rd_amortization_cny": 8000 if fintechish else 5000,
        "monthly_orders": 15 if fintechish else 25,
        "funding_cost_per_order": 400 if fintechish else 0,
        "bad_debt_reserve_rate": 0.04 if fintechish else 0.01,
        "ltv_months": 12 if fintechish else 6,
        "gross_margin": margin,
        "test_budget_cny": budget,
    }


async def build_decision_brief(
    *,
    opportunity: dict[str, Any],
    business_model: dict[str, Any],
    redteam: dict[str, Any],
    llm: Optional[LLMClient] = None,
) -> dict[str, Any]:
    """
    生成决策台深度简报：单位经济 + 五维验证 + 专家质询。

    Raises:
        RuntimeError: 模型失败时仍返回计算器结果，风险字段降级为启发式（不编造融资数据）。
    """

    opportunity = opportunity if isinstance(opportunity, dict) else {}
    business_model = business_model if isinstance(business_model, dict) else {}
    redteam = redteam if isinstance(redteam, dict) else {}

    seed = _seed_inputs_from_payloads(opportunity, business_model)
    unit = calc_unit_economics(seed)
    compact = _slim_for_brief(opportunity, business_model, redteam)

    client = llm or LLMClient()
    schema = expert_brief_json_schema()
    system = (
        "你是顶级孵化器合伙人 + 中国市场操盘手，对医疗/金融交叉极度谨慎。"
        "禁止直接给建议：必须先完成战场定义、五维验证、压力测试、保守算账、MVP找路，再裁决。"
        "必须只依据给定商机/模式/红队证据；禁止编造牌照、融资额、精确市场规模；未知标 unknown。"
        "单位经济必须保守，并对照铁律 LTV>=3*CAC；专家质询中「靠体验好活下去」一律不合格。"
        "合规 status 只能是 pass|warn|block；裁决 recommendation 只能是 "
        "strongly_recommend|cautious_try|abandon。"
        "JSON 必须完整可解析；字符串勿过长（单字段≤120字）；数组最多3项。"
        f"只输出 JSON：{schema}"
    )
    user = (
        f"payload={compact}\n"
        f"unit_economics_calc={unit}\n"
        "请输出完整五维(demand/market/unit_economics/moat/execution)、"
        "专家质询清单、资本退出、第二曲线、反脆弱，以及合规/渠道/退出字段。"
        "若有狩猎报告，裁决须与 phase4_verdict 对齐或给出更严降级；reasons[] 必填。"
        "并在 JSON 顶层增加 hunt_digest:"
        '{"tracks":["..."],"preferred_model":"...","non_consensus":"...","mvp_budget":0}。'
    )

    risk: dict[str, Any]
    model_name = ""
    try:
        payload, model_name, used_mock = await client.complete_json(
            route=ModelRoute.TIER_S,
            system=system,
            user=user,
            mock_payload=None,
            allow_mock=False,
            temperature=0.25,
        )
        if used_mock or not isinstance(payload, dict):
            raise RuntimeError("决策简报禁止 mock 或非对象返回")
        risk = payload
    except Exception as exc:  # noqa: BLE001
        friendly = _friendly_model_error(exc)
        blob = f"{opportunity.get('recommended')} {business_model.get('positioning')}".lower()
        fin = any(k in blob for k in ("分期", "消金", "放贷", "保险", "信贷", "代偿"))
        med = any(k in blob for k in ("牙科", "种植", "医美", "医疗", "病历"))
        items: list[dict[str, Any]] = []
        if med:
            items.append(
                {
                    "title": "医疗隐私数据",
                    "status": "warn",
                    "law_ref": "《数据安全法》《个人信息保护法》",
                    "detail": "病历属敏感个人信息，公有云明文存储有爆雷风险。",
                    "action": "确认私有化/加密与最小必要采集后再放量。",
                }
            )
        if fin:
            items.append(
                {
                    "title": "金融牌照与资金流隔离",
                    "status": "block",
                    "law_ref": "放贷相关监管 / 禁止砍头息",
                    "detail": "涉及分期代偿可能触及放贷资质；技术服务须严格隔离资金流。",
                    "action": "明确仅做技术服务、合同与资金流隔离，或对接持牌机构。",
                }
            )
        if not items:
            items.append(
                {
                    "title": "通用合规复核",
                    "status": "warn",
                    "law_ref": "广告法/个人信息保护",
                    "detail": friendly,
                    "action": "人工复核合同与数据存储方案；可刷新穿透简报重试。",
                }
            )
        overall = "block" if any(i["status"] == "block" for i in items) else "warn"
        expert = heuristic_expert_pack(
            opportunity=opportunity,
            business_model=business_model,
            unit=unit,
            seed=seed,
            compliance_items=items,
            overall_compliance=overall,
            error=friendly,
        )
        risk = {
            **expert,
            "market": {
                "tam": "证据不足未估",
                "sam": "证据不足未估",
                "som": "证据不足未估",
                "sources": [],
                "notes": friendly,
                "trend": "unknown",
            },
            "monetization_paths": [],
            "compliance": {"overall": overall, "items": items},
            "channel_dependency": {
                "single_dependency": True,
                "risk": "证据不足，默认按高依赖假设。",
                "plan_b": "补齐备选资方/渠道后再扩大测试预算。",
                "score_penalty": 0.15,
            },
            "exit_and_pivot": {
                "stop_loss": f"测试预算 {seed.get('test_budget_cny')} 元内设定转化阈值，未达标即停。",
                "observe_metrics": [{"name": "转化率", "threshold": "低于预期阈值", "window": "3天"}],
                "asset_reuse": "评估技术栈与工作流是否可迁移邻近场景。",
                "pivot_paths": [],
            },
            "asymmetric_competition": {
                "giants": "需评估平台型巨头免费赠送降维打击。",
                "offline_alternatives": "Excel/兼职会计等土办法迁移成本。",
                "migration_cost": "mid",
                "defense": "未知，需补齐数据飞轮或工作流锁定。",
            },
            "policy_and_dispute": {
                "collection_policy": "unknown",
                "clinic_cashflow_need": "若涉及诊所，确认资方是否支持对诊所即时结清。",
                "dispute_risk": "医疗纠纷可能导致拒还，需合同隔离或保险。",
                "contract_must_have": ["争议期还款义务", "服务与借贷关系隔离说明"],
            },
            "degraded": True,
            "error": friendly,
            "error_raw": str(exc)[:300],
        }

    risk = normalize_risk_with_expert(risk, unit)

    # 透传狩猎报告摘要，供决策台图表
    hunt = opportunity.get("hunt_report") if isinstance(opportunity.get("hunt_report"), dict) else {}
    if hunt and not risk.get("hunt_digest"):
        p3 = hunt.get("phase3_rebuild") if isinstance(hunt.get("phase3_rebuild"), dict) else {}
        p4 = hunt.get("phase4_verdict") if isinstance(hunt.get("phase4_verdict"), dict) else {}
        p1 = hunt.get("phase1_macro") if isinstance(hunt.get("phase1_macro"), dict) else {}
        risk["hunt_digest"] = {
            "tracks": [t.get("name") for t in (p1.get("cross_tracks") or []) if isinstance(t, dict)][:3],
            "preferred_model": p3.get("preferred_model"),
            "non_consensus": (p4.get("non_consensus") or {}).get("opportunity")
            if isinstance(p4.get("non_consensus"), dict)
            else "",
            "mvp_budget": (p4.get("mvp") or {}).get("budget_cny") if isinstance(p4.get("mvp"), dict) else None,
            "recommendation": p4.get("recommendation"),
            "executive_summary": hunt.get("executive_summary"),
        }

    # 市场已有 / GitHub 可一键项目识别（必须挂钩当前商机主题）
    topic_hint = str(opportunity.get("recommended") or opportunity.get("topic") or "").strip()
    top_opp: dict[str, Any] = {}
    opps = opportunity.get("opportunities") if isinstance(opportunity.get("opportunities"), list) else []
    if opps and isinstance(opps[0], dict):
        top_opp = opps[0]
        if not topic_hint:
            topic_hint = str(top_opp.get("name") or "").strip()
    industry_hint = ""
    ind = opportunity.get("industry")
    if isinstance(ind, dict):
        industry_hint = str(ind.get("name") or "")
    elif isinstance(ind, str):
        industry_hint = ind
    # 用痛点/人群/细分拼检索词，避免只拿产品英文名导致跑偏
    extra_bits = [
        str(top_opp.get("pain") or ""),
        str(top_opp.get("pain_core") or ""),
        str(top_opp.get("audience_segment") or ""),
        str(top_opp.get("industry_niche") or ""),
        industry_hint,
    ]
    search_topic = " ".join(x for x in [topic_hint, *extra_bits] if x).strip()[:120] or topic_hint

    project_pack: dict[str, Any] = {}
    portfolio_pack: dict[str, Any] = {}
    try:
        linked = await landable_portfolio_for_opportunity(
            topic=search_topic or topic_hint,
            industry=industry_hint,
            opportunity_context=str(opportunity.get("selection_rationale") or top_opp.get("pain") or "")[:800],
            generate_plan=True,
            use_ai=True,
        )
        portfolio_pack = linked.get("portfolio") or {}
        # 仅展示可落地项目
        show_items = list(linked.get("landable_items") or portfolio_pack.get("singles") or [])
        if not show_items and portfolio_pack.get("combos"):
            show_items = list((portfolio_pack["combos"][0] or {}).get("items") or [])
        project_pack = {
            "topic": search_topic,
            "industry": industry_hint,
            "count": len(show_items),
            "items": show_items[:12],
            "portfolio": portfolio_pack,
            "landing_plan": linked.get("landing_plan"),
            "landable_only": True,
            "from_cache": True,
            "note": linked.get("note")
            or "仅展示经 AI/规则判定可落地的项目；可一键生成方案并交接 Paperclip。",
        }
    except Exception as exc:  # noqa: BLE001
        try:
            project_pack = await recommend_landing_projects(
                topic=search_topic or topic_hint,
                industry=industry_hint,
                limit=16,
            )
        except Exception as exc2:  # noqa: BLE001
            project_pack = {"items": [], "errors": [str(exc), str(exc2)], "count": 0}

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model_name,
        "unit_economics": unit,
        "calculator_defaults": seed,
        "risk": risk,
        "hunt_report": hunt or None,
        "recommended_projects": project_pack,
        "source": "decision_brief_v2",
        "framework": "five_dimensions_plus_expert_interrogation",
    }
