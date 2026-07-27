# -*- coding: utf-8 -*-
"""
专家级商机验证与质询框架。

作用: 为商机/模式 Agent 与决策简报提供统一的五维验证 + VC 级质询思维链（禁止 mock 编造数据）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

from typing import Any


# 裁决枚举
RECOMMEND_STRONG = "strongly_recommend"
RECOMMEND_CAUTIOUS = "cautious_try"
RECOMMEND_ABANDON = "abandon"

EXPERT_CHAIN_FOR_OPPORTUNITY = (
    "禁止直接给建议。必须先按思维链思考再输出："
    "1) 定义战场：ToB/ToC、线上/线下、强监管/弱监管；"
    "2) 需求真伪：止痛药还是维生素？现有替代是什么（忍受/Excel/成熟竞品）？"
    "付费意愿看非金钱成本（时间/隐私/学习），不要只问「想不想要」；"
    "3) 压力测试：挑剔投资人会打哪三点？巨头（腾讯/阿里等）如何抄袭击垮？"
    "政策收紧会否立刻停摆？"
    "4) 算账：用保守行业假设，LTV 是否 > 3×CAC；边际成本是否递减；"
    "5) 找路：预算内最低成本 MVP（可不写代码/先人工跑通）；"
    "6) 裁决：强烈推荐|谨慎尝试|建议放弃，并给理由。"
    "痛点等级只能是 high|mid|low；未知标 unknown，禁止编造市场规模精确数字。"
)

EXPERT_CHAIN_FOR_BUSINESS_MODEL = (
    "商业模式必须可算账、可止损、可退出。"
    "单位经济用保守假设：全口径 CAC（广告+提成+返点+Demo）、交付人工兜底、坏账/退款；"
    "铁律 LTV>=3×CAC，否则降级裁决。"
    "必须给出：现金流结构(prepaid|postpaid|mixed)、盈亏平衡客户数、止损线、"
    "MVP 最低路径、资产复用说明、隐性组织成本（稀缺人才/长销售周期）。"
    "禁止乐观灌水；未知写 unknown。"
)

EXPERT_INTERROGATION_KEYS = (
    "moat_vs_giant",
    "kill_factors",
    "unit_econ_truth",
    "endgame",
    "non_consensus",
)


def expert_brief_json_schema() -> str:
    """
    返回决策穿透 JSON schema 说明（嵌入 LLM system）。

    Returns:
        紧凑 schema 字符串。
    """

    return (
        "{"
        '"battlefield":{"tob_toc":"ToB|ToC|Both","online_offline":"online|offline|hybrid",'
        '"regulation":"strong|weak|mixed","notes":"..."},'
        '"five_dimensions":{'
        '"demand":{"pain_level":"high|mid|low","painkiller_or_vitamin":"painkiller|vitamin|unclear",'
        '"current_alternatives":"...","alternative_gaps":"...","non_money_willingness":"...",'
        '"mvp_core_metric":"...","score":0.0},'
        '"market":{"tam":"...","sam":"...","som":"...","sources":["..."],'
        '"niche_entry":"...","trend":"rising|flat|declining|unknown","score":0.0},'
        '"unit_economics":{"gross_margin_est":0.0,"ltv_cac_rule":"pass|fail|unknown",'
        '"payback_months":"...","breakeven_customers":"...","cashflow_health":"good|weak|poor",'
        '"cashflow_structure":"prepaid|postpaid|mixed","marginal_cost_declining":true,"score":0.0},'
        '"moat":{"risk_level":"pass|warn|block","data_flywheel":"...",'
        '"switching_cost":"low|mid|high","network_effects":"...","platform_dependency":"...",'
        '"defense_vs_giant":"...","main_competitors":["..."],"score":0.0},'
        '"execution":{"mvp_path":"...","milestones":["..."],"stop_loss":"...",'
        '"asset_residual":"...","org_friction":"...","sales_cycle_note":"...","score":0.0}},'
        '"expert_interrogation":{'
        '"moat_vs_giant":{"q":"若巨头明日上线同功能，靠什么活？","a":"...","must_not_be_ux_only":true},'
        '"kill_factors":[{"risk":"...","plan_b":"..."}],'
        '"unit_econ_truth":{"q":"扣隐性成本后毛利是否仍正？","a":"...","still_positive":true},'
        '"endgame":{"q":"3年后更值钱还是更不值钱？","a":"...","trajectory":"more_valuable|less_valuable|unclear"},'
        '"non_consensus":{"opportunity":"...","hidden_risk":"..."}},'
        '"capital_exit":{"strategic_buyers":["..."],"ipo_plausible":false,"cash_cow_ok":true,"data_fit_for_mna":"..."},'
        '"second_curve":{"stages":["工具","平台","生态"],"reuse_domains":["..."],"next_bet":"..."},'
        '"antifragility":{"single_points_of_failure":["..."],"gray_compliance":"...","pr_crisis_plan":"..."},'
        '"market":{"tam":"...","sam":"...","som":"...","sources":["..."],"notes":"...","trend":"..."},'
        '"monetization_paths":[{"name":"...","how":"...","compliance_note":"...","score":0.0}],'
        '"compliance":{"overall":"pass|warn|block","items":[{"title":"...","status":"pass|warn|block",'
        '"law_ref":"...","detail":"...","action":"..."}]},'
        '"channel_dependency":{"single_dependency":true,"risk":"...","plan_b":"...","score_penalty":0.0},'
        '"exit_and_pivot":{"stop_loss":"...","observe_metrics":[{"name":"...","threshold":"...","window":"..."}],'
        '"asset_reuse":"...","pivot_paths":["..."]},'
        '"asymmetric_competition":{"giants":"...","offline_alternatives":"...","migration_cost":"low|mid|high",'
        '"defense":"..."},'
        '"policy_and_dispute":{"collection_policy":"...","clinic_cashflow_need":"...","dispute_risk":"...",'
        '"contract_must_have":["..."]},'
        '"verdict":{"recommendation":"strongly_recommend|cautious_try|abandon","go":true,'
        '"summary":"...","reasons":["..."],"must_fix_before_go":["..."]}}'
    )


def heuristic_expert_pack(
    *,
    opportunity: dict[str, Any],
    business_model: dict[str, Any],
    unit: dict[str, Any],
    seed: dict[str, Any],
    compliance_items: list[dict[str, Any]],
    overall_compliance: str,
    error: str = "",
) -> dict[str, Any]:
    """
    模型失败时的专家质询启发式降级包（不编造市场规模数字）。

    Args:
        opportunity: 商机载荷。
        business_model: 商业模式载荷。
        unit: 单位经济测算结果。
        seed: 计算器默认输入。
        compliance_items: 合规条目。
        overall_compliance: pass|warn|block。
        error: 降级原因。

    Returns:
        可直接并入 risk 的字典。
    """

    blob = f"{opportunity.get('recommended')} {business_model.get('positioning')}".lower()
    ue_status = str(unit.get("status") or "warn")
    ltv_cac = unit.get("ltv_cac")
    ltv_rule = "unknown"
    if isinstance(ltv_cac, (int, float)):
        ltv_rule = "pass" if float(ltv_cac) >= 3 else "fail"

    pain = "mid"
    if any(k in blob for k in ("分期", "风控", "合规", "坏账", "回款")):
        pain = "high"
    if any(k in blob for k in ("美化", "锦上添花", "效率小工具")):
        pain = "low"

    moat_level = "warn"
    if overall_compliance == "block" or ue_status == "block" or ltv_rule == "fail":
        moat_level = "block"

    recommendation = RECOMMEND_CAUTIOUS
    if overall_compliance == "block" or ue_status == "block" or ltv_rule == "fail":
        recommendation = RECOMMEND_ABANDON
    elif overall_compliance == "pass" and ue_status == "ok" and ltv_rule == "pass" and pain == "high":
        recommendation = RECOMMEND_STRONG

    return {
        "battlefield": {
            "tob_toc": "Both",
            "online_offline": "hybrid",
            "regulation": "strong" if any(k in blob for k in ("分期", "医疗", "金融")) else "mixed",
            "notes": error or "启发式降级",
        },
        "five_dimensions": {
            "demand": {
                "pain_level": pain,
                "painkiller_or_vitamin": "painkiller" if pain == "high" else "unclear",
                "current_alternatives": "未知，需验证是否仅为忍受/Excel/成熟低价替代",
                "alternative_gaps": "unknown",
                "non_money_willingness": "需测试用户是否愿付出时间/隐私/学习成本",
                "mvp_core_metric": "测试预算内转化或付费意向",
                "score": 0.5 if pain == "mid" else (0.7 if pain == "high" else 0.3),
            },
            "market": {
                "tam": "unknown",
                "sam": "unknown",
                "som": "unknown",
                "sources": [],
                "niche_entry": str(opportunity.get("recommended") or "unknown"),
                "trend": "unknown",
                "score": 0.4,
            },
            "unit_economics": {
                "gross_margin_est": (business_model.get("unit_economics") or {}).get("gross_margin_est"),
                "ltv_cac_rule": ltv_rule,
                "payback_months": unit.get("payback_months"),
                "breakeven_customers": unit.get("customers_to_cover_test_and_rd"),
                "cashflow_health": "poor" if ue_status == "block" else ("weak" if ue_status == "warn" else "good"),
                "cashflow_structure": "unknown",
                "marginal_cost_declining": not bool(unit.get("is_outsourcing_risk")),
                "score": 0.3 if ltv_rule == "fail" else 0.55,
            },
            "moat": {
                "risk_level": moat_level,
                "data_flywheel": "unknown — 需确认是否积累行业负样本/黑话数据",
                "switching_cost": "low",
                "network_effects": "unknown",
                "platform_dependency": "默认按高依赖假设复核",
                "defense_vs_giant": "不能只靠体验；需数据/关系链/工作流锁定",
                "main_competitors": [],
                "score": 0.35,
            },
            "execution": {
                "mvp_path": f"测试预算 ≤{seed.get('test_budget_cny')} 元，优先人工流程验证",
                "milestones": ["定义止损指标", "最小付费/意向测试", "复核合规"],
                "stop_loss": f"投入 {seed.get('test_budget_cny')} 元无有效转化则停",
                "asset_residual": "评估技术栈与流程 SOP 可迁移性",
                "org_friction": "避免三栖稀缺人才堆叠；优先 SOP 化",
                "sales_cycle_note": "小预算避免长招投标；找短平快切入",
                "score": 0.5,
            },
        },
        "expert_interrogation": {
            "moat_vs_giant": {
                "q": "若巨头明日上线同功能，靠什么活？",
                "a": "证据不足；禁止用「体验更好」作唯一答案，需补数据飞轮/迁移成本/关系链。",
                "must_not_be_ux_only": True,
            },
            "kill_factors": [
                {"risk": "单一渠道或资方切断", "plan_b": "补齐备选渠道/资方前不大额投放"},
                {"risk": "合规红线触发", "plan_b": "技术服务与资金流隔离或停做强监管环节"},
                {"risk": "单位经济倒挂（人工兜底过高）", "plan_b": "提价/降 Bad Case/砍范围"},
            ],
            "unit_econ_truth": {
                "q": "扣隐性成本后毛利是否仍正？",
                "a": f"测算状态={ue_status}；LTV/CAC={ltv_cac}",
                "still_positive": ue_status != "block" and ltv_rule != "fail",
            },
            "endgame": {
                "q": "3年后更值钱还是更不值钱？",
                "a": "未知；需判断是否能从工具演进到平台/生态。",
                "trajectory": "unclear",
            },
            "non_consensus": {
                "opportunity": "待补：需写清「市场忽视了什么」",
                "hidden_risk": error or "模型降级，需人工补非共识判断",
            },
        },
        "capital_exit": {
            "strategic_buyers": [],
            "ipo_plausible": False,
            "cash_cow_ok": True,
            "data_fit_for_mna": "需保证数据干净、无纠纷、代码可交接",
        },
        "second_curve": {
            "stages": ["工具", "平台", "生态"],
            "reuse_domains": [],
            "next_bet": "unknown",
        },
        "antifragility": {
            "single_points_of_failure": ["平台账号", "支付接口", "单一云厂商", "单一资方"],
            "gray_compliance": "动态合规：预留加密/隐私计算升级接口",
            "pr_crisis_plan": "避免被定性为骚扰/杀熟；准备下架与澄清话术",
        },
        "verdict": {
            "recommendation": recommendation,
            "go": recommendation != RECOMMEND_ABANDON,
            "summary": "专家质询启发式降级结果，请人工复核后再放行。",
            "reasons": [i.get("detail") or i.get("title") for i in compliance_items][:3]
            + list(unit.get("alerts") or [])[:2],
            "must_fix_before_go": [i.get("action") for i in compliance_items if i.get("action")],
        },
    }


def normalize_risk_with_expert(risk: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
    """
    确保 risk 含五维与专家质询字段；补齐 verdict.recommendation。

    Args:
        risk: LLM 或启发式产出的 risk。
        unit: 单位经济结果。

    Returns:
        规范化后的 risk。
    """

    if not isinstance(risk, dict):
        risk = {}

    verdict = risk.get("verdict") if isinstance(risk.get("verdict"), dict) else {}
    rec = str(verdict.get("recommendation") or "").strip()
    if rec not in {RECOMMEND_STRONG, RECOMMEND_CAUTIOUS, RECOMMEND_ABANDON}:
        # 由 go + 单位经济/合规推断
        compliance = risk.get("compliance") if isinstance(risk.get("compliance"), dict) else {}
        overall = str(compliance.get("overall") or "warn")
        if overall == "block" or unit.get("status") == "block" or verdict.get("go") is False:
            rec = RECOMMEND_ABANDON
        elif overall == "pass" and unit.get("status") == "ok":
            rec = RECOMMEND_STRONG
        else:
            rec = RECOMMEND_CAUTIOUS
        verdict["recommendation"] = rec
    if "go" not in verdict:
        verdict["go"] = rec != RECOMMEND_ABANDON
    if not isinstance(verdict.get("reasons"), list):
        verdict["reasons"] = []
    if not isinstance(verdict.get("must_fix_before_go"), list):
        verdict["must_fix_before_go"] = []
    risk["verdict"] = verdict

    # 若缺五维/质询，用轻量占位，避免 UI 空白
    if not isinstance(risk.get("five_dimensions"), dict):
        risk["five_dimensions"] = {}
    if not isinstance(risk.get("expert_interrogation"), dict):
        risk["expert_interrogation"] = {
            "moat_vs_giant": {
                "q": "若巨头明日上线同功能，靠什么活？",
                "a": "未生成，请刷新穿透简报",
                "must_not_be_ux_only": True,
            },
            "kill_factors": [],
            "unit_econ_truth": {"q": "扣隐性成本后毛利是否仍正？", "a": "未生成", "still_positive": None},
            "endgame": {"q": "3年后更值钱还是更不值钱？", "a": "未生成", "trajectory": "unclear"},
            "non_consensus": {"opportunity": "unknown", "hidden_risk": "unknown"},
        }
    if not isinstance(risk.get("battlefield"), dict):
        risk["battlefield"] = {
            "tob_toc": "unknown",
            "online_offline": "unknown",
            "regulation": "unknown",
            "notes": "",
        }
    for key in ("capital_exit", "second_curve", "antifragility"):
        if not isinstance(risk.get(key), dict):
            risk[key] = {}
    return risk
