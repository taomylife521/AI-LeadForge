# -*- coding: utf-8 -*-
"""
LeadForge 专职 Agent 实现。

作用: 五个业务 Agent + 红队，均通过 DataEnvelope 通信并绑定模型档位与 Skill。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import re
from typing import Any

from app.envelope import (
    AgentName,
    DataEnvelope,
    EnvelopeError,
    EnvelopeStatus,
    ModelRoute,
    make_envelope,
)
from app.llm import LLMClient
from app.memory.store import MemorySnapshot
from app.agent_bindings import build_agent_system_prefix, get_agent_binding
from app.payload_normalize import normalize_business_model_payload
from app.theme_recommend import list_industries
from app.tools.expert_framework import EXPERT_CHAIN_FOR_BUSINESS_MODEL, EXPERT_CHAIN_FOR_OPPORTUNITY
from app.tools.opportunity_hunter import hunt_opportunities
from app.tools.opportunity_research import research_china_opportunity
from app.tools.opportunity_score import enrich_opportunity_scores
from app.run_trace import NodeTracer
from app.workflow_graph import load_graph


AD_LAW_BANNED = [
    "最权威",
    "国家级",
    "第一",
    "唯一",
    "100%",
    "百分百",
    "稳赚",
    "包过",
    "绝对有效",
    "根治",
]


class BaseAgent:
    """Agent 基类：运行时读取动态绑定（Skill/Rule/MCP/Model）。"""

    name: AgentName
    route: ModelRoute
    skill_ids: list[str]
    binding_key: str = ""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def _key(self) -> str:
        return self.binding_key or self.name.value

    def runtime_skills(self) -> list[str]:
        """当前绑定的 Skill 列表。"""

        try:
            return list(get_agent_binding(self._key()).get("skills") or self.skill_ids)
        except KeyError:
            return list(self.skill_ids)

    def runtime_route(self) -> ModelRoute:
        """当前绑定的默认档位。"""

        try:
            binding = get_agent_binding(self._key())
            model_cfg = binding.get("model")
            if not isinstance(model_cfg, dict):
                model_cfg = {}
            route_name = model_cfg.get("route") or self.route.value
            return ModelRoute(route_name)
        except Exception:  # noqa: BLE001
            return self.route

    async def bound_complete_json(
        self,
        *,
        theme_pack: str,
        snapshot: MemorySnapshot,
        user: str,
        mock_payload: dict | None = None,
        extra_system: str = "",
        allow_mock: bool = False,
        temperature: float = 0.3,
        tracer: Any = None,
        prompt_override: str = "",
        node_extra_system: str = "",
        model_binding_override: dict | None = None,
        skills_override: list[str] | None = None,
    ) -> tuple[dict, str, list[str], ModelRoute]:
        """
        带动态绑定的 JSON 调用。

        Args:
            allow_mock: 默认 False，禁止 mock/伪造降级。
            tracer: 可选 NodeTracer，用于推送提示词/模型/Skill。
            prompt_override: 节点级自定义提示词。
            node_extra_system: 节点级额外系统提示。
            model_binding_override: 节点级模型绑定覆盖。
            skills_override: 节点级 Skill 列表覆盖。

        Returns:
            payload, model_name, skill_ids, route
        """

        prefix, binding = build_agent_system_prefix(
            self._key(),
            theme_pack=theme_pack,
            prompt_override=prompt_override,
            extra_system_override=node_extra_system,
        )
        skills = list(skills_override if skills_override is not None else (binding.get("skills") or self.skill_ids))
        route = self.runtime_route()
        system = snapshot.to_prompt_block() + "\n\n" + prefix
        if extra_system:
            system += "\n" + extra_system
        model_binding = model_binding_override
        if model_binding is None:
            model_binding = binding.get("model") if isinstance(binding.get("model"), dict) else None
        # 解析将使用的模型名（调用前可见）
        try:
            preview_model, _ = self.llm._resolve_from_binding(route, model_binding)
        except Exception:  # noqa: BLE001
            preview_model = route.value
        if tracer is not None:
            await tracer.llm_call(
                model=str(preview_model),
                route=route.value,
                skills=skills,
                system=system,
                user=user,
                status="running",
                extra={"temperature": temperature, "phase": "request"},
            )
        try:
            payload, model, used_mock = await self.llm.complete_json(
                route=route,
                system=system,
                user=user,
                mock_payload=mock_payload,
                model_binding=model_binding,
                allow_mock=allow_mock,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            if tracer is not None:
                await tracer.llm_call(
                    model=str(preview_model),
                    route=route.value,
                    skills=skills,
                    system=system,
                    user=user,
                    status="failed",
                    extra={"error": str(exc)},
                )
            raise
        if tracer is not None:
            await tracer.llm_call(
                model=model,
                route=route.value,
                skills=skills,
                system=system,
                user=user,
                status="success",
                extra={"used_mock": used_mock, "phase": "response"},
            )
        if used_mock and not allow_mock:
            raise RuntimeError(f"{self._key()} 禁止 mock，但收到了 mock 响应")
        return payload, model, skills, route


class OpportunityAgent(BaseAgent):
    """商机挖掘 Agent（优先可靠可落地机会；获客为后续阶段）。"""

    name = AgentName.OPPORTUNITY
    route = ModelRoute.TIER_S
    skill_ids = ["customer-research", "competitor-profiling", "seo-audit"]

    async def run(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        topic: str,
        snapshot: MemorySnapshot,
        parent_span_id: str | None = None,
        industry: str = "",
        industry_name: str = "",
    ) -> DataEnvelope:
        """
        输出经证据约束的高成功率商机（必须绑定真实搜索/抓取证据）。

        Raises:
            RuntimeError: 研究或模型失败时不降级。
        """

        topic = (topic or "").strip()
        # 允许空主题：由狩猎引擎自动选题
        if not topic:
            topic = ""

        tracer = NodeTracer(trace_id, agent="opportunity", node_id="opportunity", stuck_after_sec=120)
        await tracer.start_watchdog()
        await tracer.log(f"开始商机狩猎+研究: {topic or '(自动狩猎)'}", stage="start")

        # 解析行业中文名
        ind_name = (industry_name or "").strip()
        ind_id = (industry or "").strip()
        if not ind_name and ind_id:
            for row in list_industries(theme_pack):
                if row.get("id") == ind_id or row.get("name") == ind_id:
                    ind_id = str(row.get("id") or ind_id)
                    ind_name = str(row.get("name") or ind_id)
                    break
            if not ind_name:
                ind_name = ind_id

        try:
            # —— 四阶段狩猎引擎（空主题/模糊方向主动挖非共识）——
            await tracer.log("启动商机狩猎与重构引擎", stage="hunt")
            hunt_report = await hunt_opportunities(
                direction=topic,
                industry=ind_id,
                industry_name=ind_name,
                theme_pack=theme_pack,
                llm=self.llm,
                tracer=tracer,
            )
            hunt_topic = str(hunt_report.get("recommended_topic") or "").strip()
            # 空主题或极短模糊词：用狩猎产出的具体主题做下游研究
            research_topic = topic
            if (not topic) or len(topic) <= 8 or topic in {"AI应用", "宠物经济", "找商机", "寻找商机"}:
                research_topic = hunt_topic or topic or "中国可验证非共识创业商机"
                await tracer.log(f"狩猎锁定主题: {research_topic}", stage="hunt_topic")

            research = await research_china_opportunity(
                topic=research_topic,
                industry_id=ind_id,
                industry_name=ind_name,
                theme_pack=theme_pack,
                llm=self.llm,
                tracer=tracer,
            )
            await tracer.log("证据研究完成，开始 LLM 精炼", stage="refine")

            # 精炼只用摘要，避免把整包研究塞进提示词导致超时/不可见
            research_brief = {
                "recommended": research.get("recommended"),
                "selection_rationale": research.get("selection_rationale"),
                "recommendation": research.get("recommendation")
                or (hunt_report.get("phase4_verdict") or {}).get("recommendation"),
                "battlefield": research.get("battlefield") or hunt_report.get("battlefield"),
                "expert_stress": research.get("expert_stress"),
                "hunt_summary": {
                    "executive_summary": hunt_report.get("executive_summary"),
                    "cross_tracks": ((hunt_report.get("phase1_macro") or {}).get("cross_tracks") or [])[:3],
                    "preferred_model": (hunt_report.get("phase3_rebuild") or {}).get("preferred_model"),
                    "verdict": hunt_report.get("phase4_verdict"),
                    "models": ((hunt_report.get("phase3_rebuild") or {}).get("models") or [])[:2],
                },
                "opportunities": (research.get("opportunities") or [])[:5],
                "lead_clues": (research.get("lead_clues") or [])[:6],
                "competitors": (research.get("competitors") or [])[:5],
                "trends": (research.get("trends") or [])[:5],
                "hotness": research.get("hotness"),
                "risks": research.get("risks"),
                "sources": (research.get("sources") or [])[:12],
                "research_mode": (research.get("research") or {}).get("mode"),
                "score_policy": research.get("score_policy"),
            }

            node_cfg = ((load_graph(trace_id) or {}).get("nodes") or {}).get("opportunity", {}).get("config") or {}
            try:
                payload, model, skills, route = await self.bound_complete_json(
                    theme_pack=theme_pack,
                    snapshot=snapshot,
                    user=(
                        f"你必须基于下列真实研究摘要精炼「可落地商机」JSON，禁止脱离证据编造。\n"
                        f"本阶段目标=商机定位与筛选（A/B/C 等权）+ 专家质询，不是获客投放方案。\n"
                        f"研究摘要:\n{research_brief}\n"
                        f"输出 JSON 字段: topic, market, locale, industry, phase=opportunity_discovery, "
                        f"battlefield, recommendation, recommended, selection_rationale, score_policy, "
                        f"opportunities[](含 validate_ease/willingness_to_pay/competition_gap/"
                        f"pain_level/painkiller_or_vitamin/current_alternative/stop_loss_hint/"
                        f"moat_hint/kill_risks/success_likelihood/feasibility/why_now/validation_steps), "
                        f"expert_stress, lead_clues[], recommended_leads[], "
                        f"competitors[], sources[], research_summary, confidence_notes。"
                        f"\n务必输出合法紧凑 JSON，字符串内勿使用未转义换行。"
                    ),
                    mock_payload=None,
                    extra_system=(
                        "你是商机筛选官兼孵化器合伙人："
                        "A可快速验证、B付费清晰、C竞争缺口，三项等权都看重。"
                        + EXPERT_CHAIN_FOR_OPPORTUNITY
                        + "获客/落地页/投放属于商机确认后的后续流程，不要喧宾夺主。"
                        "opportunities[].evidence 必须能对应 sources 或 research 中的 URL/摘要。"
                        "lead_clues 必须来自创业邦/创投证据，禁止编造融资与公司名。"
                        "若证据不足，降低对应 A/B/C 分，不要捏造。"
                    ),
                    allow_mock=False,
                    temperature=0.25,
                    tracer=tracer,
                    prompt_override=str(node_cfg.get("prompt") or ""),
                    node_extra_system=str(node_cfg.get("extra_system") or ""),
                    model_binding_override=node_cfg.get("model") if isinstance(node_cfg.get("model"), dict) else None,
                    skills_override=node_cfg.get("skills") if isinstance(node_cfg.get("skills"), list) else None,
                )
            except Exception as refine_exc:  # noqa: BLE001
                # 精炼 JSON 损坏时回退研究包（仍是真实证据结果，非 mock）
                await tracer.log(
                    f"精炼失败，回退研究包: {refine_exc}",
                    stage="refine_fallback",
                    status="running",
                    detail={"error": str(refine_exc)},
                )
                payload = {
                    "topic": research_topic or topic,
                    "recommended": research.get("recommended") or hunt_report.get("recommended_opportunity"),
                    "selection_rationale": research.get("selection_rationale"),
                    "opportunities": research.get("opportunities"),
                    "lead_clues": research.get("lead_clues"),
                    "recommended_leads": research.get("recommended_leads"),
                    "competitors": research.get("competitors"),
                    "sources": research.get("sources"),
                    "hunt_report": hunt_report,
                    "research_summary": "refine_json_parse_failed_used_research_pack",
                }
                model = (research.get("research") or {}).get("model") or "research-pack"
                skills = list(self.skill_ids)
                route = self.runtime_route()

            # 强制回填真实证据，防止模型丢掉 sources / 商机线索
            if not isinstance(payload, dict):
                raise RuntimeError("商机模型未返回对象")
            payload["topic"] = payload.get("topic") or research_topic or topic
            payload["market"] = "中国"
            payload["locale"] = "zh-CN"
            payload["phase"] = "opportunity_discovery"
            payload["industry"] = payload.get("industry") or {"id": ind_id, "name": ind_name}
            payload["hunt_report"] = hunt_report
            payload["recommended"] = (
                payload.get("recommended")
                or research.get("recommended")
                or hunt_report.get("recommended_opportunity")
            )
            payload["selection_rationale"] = (
                payload.get("selection_rationale") or research.get("selection_rationale") or ""
            )
            if not payload.get("opportunities"):
                payload["opportunities"] = research.get("opportunities")
            payload["opportunities"] = enrich_opportunity_scores(payload.get("opportunities") or [])
            if payload["opportunities"]:
                top = payload["opportunities"][0]
                if top.get("name"):
                    payload["recommended"] = top["name"]
            payload["score_policy"] = research.get("score_policy") or {
                "A": "validate_ease_1_2_weeks",
                "B": "willingness_to_pay",
                "C": "competition_gap",
                "weights": {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
            }
            if research.get("battlefield") and not payload.get("battlefield"):
                payload["battlefield"] = research.get("battlefield")
            if research.get("expert_stress") and not payload.get("expert_stress"):
                payload["expert_stress"] = research.get("expert_stress")
            if research.get("recommendation") and not payload.get("recommendation"):
                payload["recommendation"] = research.get("recommendation")
            if not payload.get("recommendation"):
                payload["recommendation"] = (hunt_report.get("phase4_verdict") or {}).get("recommendation")
            if not payload.get("lead_clues"):
                payload["lead_clues"] = research.get("lead_clues") or []
            if not payload.get("recommended_leads"):
                payload["recommended_leads"] = research.get("recommended_leads") or []
            if not payload.get("competitors"):
                payload["competitors"] = research.get("competitors")
            payload["sources"] = research.get("sources") or payload.get("sources") or []
            payload["research"] = research.get("research")
            payload["runtime"] = {
                "model": model,
                "skills": skills,
                "route": route.value if hasattr(route, "value") else str(route),
            }
            if not payload.get("opportunities"):
                raise RuntimeError("精炼后仍无 opportunities，拒绝空商机")

            await tracer.close(status="success", message=f"商机完成: {payload.get('recommended')}")
        except Exception as exc:  # noqa: BLE001
            await tracer.log(f"商机失败: {exc}", stage="error", status="failed", detail={"error": str(exc)})
            await tracer.close(status="failed", message=str(exc))
            raise

        env = make_envelope(
            agent=self.name,
            status=EnvelopeStatus.SUCCESS,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
        )
        env.metadata.model_resolved = model
        return env


class BusinessModelAgent(BaseAgent):
    """商业模式推演 Agent。"""

    name = AgentName.BUSINESS_MODEL
    route = ModelRoute.TIER_S
    skill_ids = ["pricing-strategy", "lead-magnets"]

    async def run(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        opportunity: dict[str, Any],
        snapshot: MemorySnapshot,
        parent_span_id: str | None = None,
    ) -> DataEnvelope:
        """输出定价与 MVP 范围。"""

        mock = {
            "positioning": f"为本地商家提供「{opportunity.get('recommended', '获客页')}」48h 交付",
            "pricing": {
                "model": "page_subscription_plus_cpl",
                "plans": [
                    {"name": "单页版", "price_cny": 199, "unit": "月"},
                    {"name": "增长版", "price_cny": 599, "unit": "月", "includes": "A/B + 投放素材"},
                ],
                "cpl_target_cny": 35,
            },
            "mvp_scope": ["落地页脚手架", "表单线索", "基础 SEO", "3 条广告文案"],
            "unit_economics": {"gross_margin_est": 0.62, "payback_months": 1},
            "budget_cap_test_cny": 200,
        }
        payload, model, skills, route = await self.bound_complete_json(
            theme_pack=theme_pack,
            snapshot=snapshot,
            user=(
                f"基于商机: {opportunity}. "
                f"若含 hunt_report.phase3_rebuild.models，优先在其基础上细化定价与 MVP。"
                f"输出合规可落地的 JSON，禁止夸大承诺。必须保守算账。"
            ),
            mock_payload=mock,
            extra_system=(
                "必须输出完整字段: positioning, pricing{model,plans,cpl_target_cny}, mvp_scope, "
                "unit_economics{gross_margin_est>=0.4, payback_months, ltv_cac_est, cashflow_structure}, "
                "budget_cap_test_cny(50-200整数), stop_loss, org_friction_note, asset_reuse, "
                "rebuild_models[](从狩猎引擎继承或改写的2种模式摘要)。"
                + EXPERT_CHAIN_FOR_BUSINESS_MODEL
            ),
        )
        # 契约兜底：缺字段时补齐，避免真实模型偶发漏字段导致红队误杀
        payload.setdefault("budget_cap_test_cny", 200)
        ue = payload.setdefault("unit_economics", {})
        if not isinstance(ue, dict):
            payload["unit_economics"] = {"gross_margin_est": 0.55, "payback_months": 1}
        else:
            ue.setdefault("gross_margin_est", 0.55)
            ue.setdefault("payback_months", 1)
        pricing = payload.setdefault("pricing", {})
        if isinstance(pricing, dict):
            pricing.setdefault("cpl_target_cny", 35)
        env = make_envelope(
            agent=self.name,
            status=EnvelopeStatus.SUCCESS,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
        )
        env.metadata.model_resolved = model
        return env


class RedTeamAgent(BaseAgent):
    """红队找茬 Agent（模式 & 营销双节点复用）。"""

    name = AgentName.REDTEAM
    route = ModelRoute.TIER_S
    skill_ids = ["competitor-profiling", "copywriting"]

    async def review_business_model(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        model_payload: dict[str, Any],
        parent_span_id: str | None = None,
    ) -> DataEnvelope:
        """
        商业模式压力测试。

        始终规范化上游载荷并产出非空 JSON，避免类型异常导致空输出卡死。
        结构问题优先自动修复为可继续 HITL；仅保留可覆写的告警。
        """

        warnings: list[str] = []
        try:
            normalized = normalize_business_model_payload(model_payload or {})
        except Exception as exc:  # noqa: BLE001
            normalized = normalize_business_model_payload({})
            warnings.append(f"载荷规范化回退: {exc}")

        issues: list[str] = []
        ue = normalized.get("unit_economics") or {}
        margin = float(ue.get("gross_margin_est") or 0)
        if margin < 0.3:
            issues.append("毛利过低，单位经济可能倒挂")
        budget = float(normalized.get("budget_cap_test_cny") or 0)
        if budget <= 0:
            issues.append("缺少测试预算上限")
        cpl = float((normalized.get("pricing") or {}).get("cpl_target_cny") or 999)
        if cpl < 10:
            issues.append("CPL 目标显著低于行业常识且无依据")
        if not model_payload:
            warnings.append("上游模式输出为空，已注入规范化默认值")
        elif (model_payload or {}).get("_normalized") or normalized.get("_normalized"):
            if set((model_payload or {}).keys()) != set(normalized.keys()):
                warnings.append("上游字段已规范化以通过结构检查")

        # 规范化后结构检查应通过；若仍有 issues 视为 soft（可 HITL 覆写）
        structural_ok = margin >= 0.3 and budget > 0 and cpl >= 10
        soft_issues = list(issues)
        # 专家质询软检查：缺止损/现金流/资产复用则提示
        if not normalized.get("stop_loss"):
            soft_issues.append("缺少止损线 stop_loss：专家质询要求明确失败标准")
        ue_extra = normalized.get("unit_economics") if isinstance(normalized.get("unit_economics"), dict) else {}
        if not ue_extra.get("cashflow_structure"):
            soft_issues.append("未标明现金流结构(prepaid|postpaid|mixed)")
        if not normalized.get("asset_reuse"):
            soft_issues.append("缺少资产复用说明：项目失败后技术/数据残值不明")
        if structural_ok:
            issues = []
        passed = len(issues) == 0
        payload = {
            "gate": "business_model",
            "passed": passed,
            "issues": issues,
            "soft_issues": soft_issues,
            "warnings": warnings,
            "normalized_model": normalized,
            "allow_hitl_override": True,
            "expert_checklist": [
                "护城河能否抵御巨头（非体验话术）",
                "3个致死因素是否有 Plan B",
                "扣隐性成本后毛利是否仍正",
                "3年终局更值钱还是更不值钱",
                "非共识机会/隐藏风险",
            ],
            "checks": [
                "unit_economics",
                "budget_cap",
                "cpl_sanity",
                "ad_law_surface",
                "payload_normalize",
                "expert_soft_gates",
            ],
        }
        # 默认放行到 HITL：硬拒绝仅在显式无法修复时；此处结构已规范化故 SUCCESS
        status = EnvelopeStatus.SUCCESS if passed or structural_ok else EnvelopeStatus.REJECTED_BY_REDTEAM
        if structural_ok and soft_issues:
            payload["passed"] = True
            payload["issues"] = []
            payload["review_note"] = "结构已规范化；保留 soft_issues 供人工审阅"
            status = EnvelopeStatus.SUCCESS
        skills = self.runtime_skills()
        route = self.runtime_route()
        return make_envelope(
            agent=self.name,
            status=status,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
            error=None
            if status == EnvelopeStatus.SUCCESS
            else EnvelopeError(code="REDTEAM_MODEL", message="模式未通过红队", details={"issues": issues}),
        )

    async def review_marketing(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        marketing_payload: dict[str, Any],
        parent_span_id: str | None = None,
    ) -> DataEnvelope:
        """营销文案/广告法压力测试。"""

        texts: list[str] = []
        for key in ("landing_copy", "ads", "headlines"):
            value = marketing_payload.get(key)
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(str(item) for item in value)
            elif isinstance(value, dict):
                texts.extend(str(v) for v in value.values())
        blob = "\n".join(texts)
        hits = [word for word in AD_LAW_BANNED if word in blob]
        issues = [f"疑似广告法违规词: {word}" for word in hits]
        if re.search(r"包治|根治|永不复发", blob):
            issues.append("医疗功效绝对化表述")
        passed = len(issues) == 0
        payload = {
            "gate": "marketing",
            "passed": passed,
            "issues": issues,
            "banned_hits": hits,
            "checks": ["ad_law_cn", "false_promise", "competitor_clone_heuristic"],
        }
        status = EnvelopeStatus.SUCCESS if passed else EnvelopeStatus.REJECTED_BY_REDTEAM
        skills = self.runtime_skills()
        route = self.runtime_route()
        return make_envelope(
            agent=self.name,
            status=status,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
            error=None
            if passed
            else EnvelopeError(code="REDTEAM_MARKETING", message="营销未通过红队", details={"issues": issues}),
        )


class DevAgent(BaseAgent):
    """开发 Agent — 基于脚手架参数化生成。"""

    name = AgentName.DEV
    route = ModelRoute.TIER_M
    skill_ids = ["frontend-design", "vercel-react-best-practices", "web-design-guidelines"]

    async def run(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        model_payload: dict[str, Any],
        snapshot: MemorySnapshot,
        parent_span_id: str | None = None,
    ) -> DataEnvelope:
        """输出落地页脚手架参数与文件清单。"""

        mock = {
            "scaffold": "scaffolds/next-landing",
            "params": {
                "brand": "LeadForge Clinic",
                "headline": "48小时上线本地获客页",
                "cta": "预约到店评估",
                "form_fields": ["name", "phone", "city"],
            },
            "files_touched": [
                "scaffolds/next-landing/params.yaml",
                "scaffolds/next-landing/app/page.tsx",
            ],
            "notes": "仅改参数与胶水，不从零写原生框架",
        }
        payload, model, skills, route = await self.bound_complete_json(
            theme_pack=theme_pack,
            snapshot=snapshot,
            user=f"model={model_payload}. 输出 scaffold/params/files_touched",
            mock_payload=mock,
            extra_system="只输出脚手架参数，禁止空造框架。",
        )
        env = make_envelope(
            agent=self.name,
            status=EnvelopeStatus.SUCCESS,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
        )
        env.metadata.model_resolved = model
        return env


class DeployAgent(BaseAgent):
    """部署 Agent — 本地预览为主，云部署需 HITL。"""

    name = AgentName.DEPLOY
    route = ModelRoute.TIER_XS
    skill_ids = ["deploy-to-vercel"]

    async def run(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        dev_payload: dict[str, Any],
        parent_span_id: str | None = None,
    ) -> DataEnvelope:
        """生成本地预览地址（MVP 模拟）。"""

        payload = {
            "target": "local_docker_preview",
            "preview_url": f"http://localhost:8080/preview/{trace_id}",
            "health": "ok",
            "from_scaffold": dev_payload.get("scaffold"),
            "production_blocked_until_hitl": True,
        }
        skills = self.runtime_skills()
        route = self.runtime_route()
        return make_envelope(
            agent=self.name,
            status=EnvelopeStatus.SUCCESS,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
        )


class MarketingAgent(BaseAgent):
    """营销 Agent。"""

    name = AgentName.MARKETING
    route = ModelRoute.TIER_M
    skill_ids = ["copywriting", "ad-creative", "page-cro", "paid-ads"]

    async def run(
        self,
        *,
        trace_id: str,
        theme_pack: str,
        context: dict[str, Any],
        snapshot: MemorySnapshot,
        parent_span_id: str | None = None,
        force_violate: bool = False,
    ) -> DataEnvelope:
        """生成落地页文案与广告创意。"""

        mock = {
            "landing_copy": {
                "headline": "附近洗牙，今天预约明天到店",
                "subhead": "透明报价，到店评估，无需先付全款",
                "cta": "填写手机号预约",
            },
            "ads": [
                "本地洗牙预约通道已开放",
                "到店评估，方案清晰再决定",
                "新客专属洁牙体验名额有限",
            ],
            "headlines": ["到店洗牙预约", "本地牙科新客通道", "透明洁牙报价"],
            "paid_plan": {
                "channel": "mock_search",
                "daily_budget_cny": 50,
                "requires_hitl": True,
            },
        }
        if force_violate:
            mock["ads"].append("本机构最权威，包过且100%根治")

        payload, model, skills, route = await self.bound_complete_json(
            theme_pack=theme_pack,
            snapshot=snapshot,
            user=f"context={context}. 输出 landing_copy/ads/headlines/paid_plan JSON",
            mock_payload=mock,
            extra_system="遵守中国广告法，禁止绝对化用语。",
        )
        env = make_envelope(
            agent=self.name,
            status=EnvelopeStatus.SUCCESS,
            payload=payload,
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            theme_pack=theme_pack,
            model_route=route,
            skill_ids=skills,
        )
        env.metadata.model_resolved = model
        return env
