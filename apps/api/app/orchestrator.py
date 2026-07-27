# -*- coding: utf-8 -*-
"""
LeadForge 编排器。

作用: 串联五 Agent + 红队拦截 + HITL 门禁，维持 TraceID 全链路，并向 ProgressBus 推送可视化步骤。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
from typing import Any, Optional

from uuid6 import uuid7

from app.agents.pipeline import (
    BusinessModelAgent,
    DeployAgent,
    DevAgent,
    MarketingAgent,
    OpportunityAgent,
    RedTeamAgent,
)
from app.agnes_media import generate_image
from app.envelope import (
    AgentName,
    DataEnvelope,
    EnvelopeStatus,
    ModelRoute,
    make_envelope,
    new_trace_id,
)
from app.llm import LLMClient
from app.media_types import image_block, text_block, video_block
from app.memory.store import MemoryStore
from app.progress import progress_bus
from app.payload_normalize import normalize_business_model_payload
from app.run_control import RunCancelled, run_control
from app.theme_recommend import recommend_topics
from app.tools.decision_brief import build_decision_brief
from app.workflow_graph import get_upstream_output_as_input, load_graph, new_graph, save_graph, set_node



AGENT_LABELS = {
    "orchestrator": "编排器",
    "opportunity": "商机 Agent",
    "business_model": "模式 Agent",
    "dev": "开发 Agent",
    "deploy": "部署 Agent",
    "marketing": "营销 Agent",
    "redteam": "红队 Agent",
    "flywheel": "数据飞轮",
}


class Orchestrator:
    """商业闭环编排器。"""

    def __init__(self, store: MemoryStore, llm: Optional[LLMClient] = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self.opportunity = OpportunityAgent(self.llm)
        self.business_model = BusinessModelAgent(self.llm)
        self.redteam = RedTeamAgent(self.llm)
        self.dev = DevAgent(self.llm)
        self.deploy = DeployAgent(self.llm)
        self.marketing = MarketingAgent(self.llm)

    async def _persist(self, envelope: DataEnvelope, *, title: str = "", summary: str = "") -> DataEnvelope:
        """持久化信封并推送可视化事件。"""

        self.store.save_envelope(envelope)
        await progress_bus.emit(
            envelope.trace_id,
            {
                "type": "step",
                "agent": envelope.agent.value,
                "label": AGENT_LABELS.get(envelope.agent.value, envelope.agent.value),
                "title": title or AGENT_LABELS.get(envelope.agent.value, envelope.agent.value),
                "summary": summary,
                "status": envelope.status.value,
                "model": envelope.metadata.model_resolved,
                "payload": envelope.payload,
                "error": envelope.error.model_dump() if envelope.error else None,
            },
        )
        return envelope

    async def start_run(
        self,
        *,
        topic: str = "",
        theme_pack: str = "local-service-leadgen",
        industry: str = "",
        force_bad_copy: bool = False,
        trace_id: Optional[str] = None,
        workflow_template: str = "default-closed-loop",
    ) -> dict[str, Any]:
        """启动商机→模式→红队→HITL#1。"""

        trace_id = trace_id or new_trace_id()
        run_control.ensure(trace_id)
        new_graph(
            trace_id,
            topic=topic,
            theme_pack=theme_pack,
            template_id=workflow_template or "default-closed-loop",
        )
        graph0 = load_graph(trace_id)
        if graph0 and industry:
            graph0["industry"] = industry
            save_graph(graph0)
        await progress_bus.emit(
            trace_id,
            {
                "type": "run_started",
                "title": "开始商业闭环",
                "summary": topic or "（自动推荐主题）",
                "status": "running",
                "topic": topic,
                "theme_pack": theme_pack,
                "industry": industry,
            },
        )

        # 空主题时按所选行业实时拉取推荐
        topic_source = "user"
        if not (topic or "").strip():
            rec = await recommend_topics(
                theme_pack=theme_pack,
                hint="",
                industry=industry or "",
                use_llm=True,
                limit=5,
            )
            topic = str(rec.get("recommended") or "").strip() or "中国可验证可落地创业商机"
            topic_source = f"recommend:{rec.get('source')}:{(rec.get('industry') or {}).get('id')}"
            if not industry:
                industry = str((rec.get("industry") or {}).get("id") or "")
            graph = load_graph(trace_id)
            if graph:
                graph["topic"] = topic
                graph["industry"] = industry or (rec.get("industry") or {}).get("id")
                save_graph(graph)
            await progress_bus.emit(
                trace_id,
                {
                    "type": "theme_recommended",
                    "title": "实时主题推荐",
                    "summary": topic,
                    "status": "success",
                    "items": rec.get("items") or [],
                    "source": rec.get("source"),
                    "industry": rec.get("industry"),
                },
            )

        snapshot = self.store.build_snapshot(theme_pack, topic)
        boot = make_envelope(
            agent=AgentName.ORCHESTRATOR,
            status=EnvelopeStatus.RUNNING,
            payload={
                "topic": topic,
                "theme_pack": theme_pack,
                "topic_source": topic_source,
                "memory_snapshot": snapshot.to_prompt_block(),
                "phase": "boot",
            },
            trace_id=trace_id,
            theme_pack=theme_pack,
            model_route=ModelRoute.TIER_XS,
        )
        await self._persist(boot, title="初始化 Trace", summary=f"theme={theme_pack}; topic={topic}")

        async def _gate(phase: str) -> None:
            """检查停止/暂停。"""

            try:
                await run_control.checkpoint(trace_id, phase=phase)
            except RunCancelled as exc:
                await progress_bus.emit(
                    trace_id,
                    {
                        "type": "run_finished",
                        "status": "cancelled",
                        "title": "已停止",
                        "summary": str(exc),
                        "phase": phase,
                    },
                )
                raise

        # —— 商机（中国本土真实研究）——
        await _gate("before_opportunity")
        set_node(trace_id, "opportunity", status="running", inputs=[text_block(topic or "自动选题", "topic")])
        await progress_bus.emit(
            trace_id,
            {
                "type": "phase",
                "title": "中国本土商机真实研究",
                "status": "running",
                "agent": "opportunity",
                "industry": industry,
            },
        )
        opp = await self.opportunity.run(
            trace_id=trace_id,
            theme_pack=theme_pack,
            topic=topic,
            snapshot=snapshot,
            parent_span_id=boot.span_id,
            industry=industry or "",
        )
        await self._persist(opp, title="商机挖掘完成", summary=str(opp.payload.get("recommended") or ""))
        set_node(
            trace_id,
            "opportunity",
            status="success",
            outputs=[
                text_block(json.dumps(opp.payload, ensure_ascii=False, indent=2), "opportunity.json"),
                text_block(str(opp.payload.get("recommended") or ""), "recommended"),
                text_block(json.dumps(opp.payload.get("sources") or [], ensure_ascii=False), "sources.json"),
            ],
        )

        # —— 模式 ——
        await _gate("before_business_model")
        set_node(
            trace_id,
            "business_model",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "business_model"),
        )
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "商业模式推演", "status": "running", "agent": "business_model"},
        )
        model = await self.business_model.run(
            trace_id=trace_id,
            theme_pack=theme_pack,
            opportunity=opp.payload,
            snapshot=snapshot,
            parent_span_id=opp.span_id,
        )
        model.payload = normalize_business_model_payload(model.payload)
        pricing = model.payload.get("pricing") if isinstance(model.payload.get("pricing"), dict) else {}
        await self._persist(model, title="模式推演完成", summary=str(pricing.get("model") or ""))
        set_node(
            trace_id,
            "business_model",
            status="success",
            outputs=[text_block(json.dumps(model.payload, ensure_ascii=False, indent=2), "business_model.json")],
        )

        # —— 红队模式 ——
        await _gate("before_redteam")
        set_node(
            trace_id,
            "redteam_model",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "redteam_model"),
        )
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "红队压力测试 #1", "status": "running", "agent": "redteam"},
        )
        try:
            rt1 = await self.redteam.review_business_model(
                trace_id=trace_id,
                theme_pack=theme_pack,
                model_payload=model.payload,
                parent_span_id=model.span_id,
            )
        except Exception as exc:  # noqa: BLE001
            # 兜底：红队异常时仍产出可审阅载荷，进入 HITL
            rt1 = make_envelope(
                agent=AgentName.REDTEAM,
                status=EnvelopeStatus.SUCCESS,
                payload={
                    "gate": "business_model",
                    "passed": True,
                    "issues": [],
                    "soft_issues": [f"红队执行异常已降级: {exc}"],
                    "warnings": ["redteam_exception_fallback"],
                    "normalized_model": model.payload,
                    "allow_hitl_override": True,
                },
                trace_id=trace_id,
                parent_span_id=model.span_id,
                theme_pack=theme_pack,
                model_route=ModelRoute.TIER_S,
            )
        # 若红队回写了规范化模式，下游统一使用
        if isinstance(rt1.payload.get("normalized_model"), dict):
            model.payload = rt1.payload["normalized_model"]
        await self._persist(rt1, title="红队#1 完成", summary="通过" if rt1.status == EnvelopeStatus.SUCCESS else "未通过")
        set_node(
            trace_id,
            "redteam_model",
            status="success",
            outputs=[text_block(json.dumps(rt1.payload, ensure_ascii=False, indent=2), "redteam_model.json")],
        )
        needs_override = rt1.status == EnvelopeStatus.REJECTED_BY_REDTEAM
        if needs_override:
            self.store.add_lesson(
                theme_pack=theme_pack,
                outcome="failure",
                title="红队打回商业模式（待人工覆写）",
                summary="; ".join(rt1.payload.get("issues") or []),
                trace_id=trace_id,
            )

        # 决策台深度简报：单位经济 + 合规/渠道/退出/TAM（禁止 mock）
        await _gate("before_decision_brief")
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "生成决策穿透简报", "status": "running", "agent": "orchestrator"},
        )
        decision_brief = await build_decision_brief(
            opportunity=opp.payload if isinstance(opp.payload, dict) else {},
            business_model=model.payload if isinstance(model.payload, dict) else {},
            redteam=rt1.payload if isinstance(rt1.payload, dict) else {},
            llm=self.llm,
        )
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "决策穿透简报就绪", "status": "success", "agent": "orchestrator"},
        )

        hitl_id = str(uuid7())
        task = self.store.create_hitl(
            task_id=hitl_id,
            trace_id=trace_id,
            gate="model_confirm",
            title="确认商业模式与测试预算" + ("（红队未通过，需人工覆写）" if needs_override else ""),
            body={
                "opportunity": opp.payload,
                "business_model": model.payload,
                "redteam": rt1.payload,
                "decision_brief": decision_brief,
                "force_bad_copy": force_bad_copy,
                "needs_override": needs_override,
            },
        )
        set_node(
            trace_id,
            "hitl_model",
            status="waiting",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "hitl_model"),
            outputs=[text_block("等待人工审批 HITL#1", "hitl")],
        )
        pause = make_envelope(
            agent=AgentName.ORCHESTRATOR,
            status=EnvelopeStatus.NEEDS_HITL,
            payload={"hitl_id": hitl_id, "gate": "model_confirm"},
            trace_id=trace_id,
            parent_span_id=rt1.span_id,
            theme_pack=theme_pack,
            model_route=ModelRoute.TIER_XS,
        )
        await self._persist(pause, title="等待人工审批 HITL#1", summary="确认商业模式")
        result = {
            "trace_id": trace_id,
            "status": "needs_hitl",
            "gate": "model_confirm",
            "hitl": task,
            "opportunity": opp.payload,
            "business_model": model.payload,
            "redteam": rt1.payload,
        }
        await progress_bus.emit(trace_id, {"type": "run_paused", "status": "needs_hitl", "result": result})
        run_control.mark_finished(trace_id)
        return result

    async def continue_after_model_hitl(
        self,
        trace_id: str,
        force_bad_copy: bool = False,
        *,
        budget_cap_test_cny: float | None = None,
        business_model_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """HITL#1 通过后: 若图中有开发节点则继续；否则结束于确认。"""

        envelopes = self.store.get_trace_envelopes(trace_id)
        theme_pack = "local-service-leadgen"
        model_payload: dict[str, Any] = {}
        for item in envelopes:
            if item.get("agent") == "business_model":
                model_payload = dict(item.get("payload") or {})
                theme_pack = item.get("metadata", {}).get("theme_pack") or theme_pack
        if business_model_patch and isinstance(business_model_patch, dict):
            model_payload.update(business_model_patch)
        if budget_cap_test_cny is not None:
            try:
                model_payload["budget_cap_test_cny"] = float(budget_cap_test_cny)
            except (TypeError, ValueError):
                pass
        # 将人工确认预算写回图节点输出，便于审计
        try:
            set_node(
                trace_id,
                "business_model",
                outputs=[
                    text_block(json.dumps(model_payload, ensure_ascii=False, indent=2), "business_model.json"),
                    text_block(str(model_payload.get("budget_cap_test_cny") or ""), "budget_cap_test_cny"),
                ],
            )
        except Exception:  # noqa: BLE001
            pass

        snapshot = self.store.build_snapshot(theme_pack)
        parent = envelopes[-1]["span_id"] if envelopes else None

        set_node(trace_id, "hitl_model", status="success", outputs=[text_block("HITL#1 已批准", "hitl")])

        graph = load_graph(trace_id) or {}
        if "dev" not in (graph.get("nodes") or {}):
            result = {
                "trace_id": trace_id,
                "status": "model_confirmed",
                "gate": "done",
                "business_model": model_payload,
                "note": "当前工作流模板在 HITL 后结束（无开发/投放节点）",
            }
            await progress_bus.emit(trace_id, {"type": "run_finished", "status": "model_confirmed", "result": result})
            return result

        set_node(
            trace_id,
            "dev",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "dev"),
        )
        await progress_bus.emit(trace_id, {"type": "phase", "title": "脚手架开发", "status": "running", "agent": "dev"})
        dev = await self.dev.run(
            trace_id=trace_id,
            theme_pack=theme_pack,
            model_payload=model_payload,
            snapshot=snapshot,
            parent_span_id=parent,
        )
        await self._persist(dev, title="开发完成", summary=str(dev.payload.get("scaffold") or ""))
        dev_outs = [text_block(json.dumps(dev.payload, ensure_ascii=False, indent=2), "dev.json")]
        params = (dev.payload or {}).get("params") or {}
        img_url = await generate_image(
            f"Landing page hero mockup for {params.get('brand','brand')}, "
            f"headline '{params.get('headline','')}', clean modern local service UI"
        )
        if img_url:
            dev_outs.append(image_block(img_url, content="landing mockup", name="landing_mockup"))
        set_node(trace_id, "dev", status="success", outputs=dev_outs)

        set_node(
            trace_id,
            "deploy",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "deploy"),
        )
        await progress_bus.emit(trace_id, {"type": "phase", "title": "部署预览", "status": "running", "agent": "deploy"})
        deploy = await self.deploy.run(
            trace_id=trace_id,
            theme_pack=theme_pack,
            dev_payload=dev.payload,
            parent_span_id=dev.span_id,
        )
        await self._persist(deploy, title="部署完成", summary=str(deploy.payload.get("preview_url") or ""))
        set_node(
            trace_id,
            "deploy",
            status="success",
            outputs=[
                text_block(json.dumps(deploy.payload, ensure_ascii=False, indent=2), "deploy.json"),
                text_block(str(deploy.payload.get("preview_url") or ""), "preview_url"),
            ],
        )

        set_node(
            trace_id,
            "marketing",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "marketing"),
        )
        await progress_bus.emit(trace_id, {"type": "phase", "title": "营销文案", "status": "running", "agent": "marketing"})
        marketing = await self.marketing.run(
            trace_id=trace_id,
            theme_pack=theme_pack,
            context={"business_model": model_payload, "deploy": deploy.payload},
            snapshot=snapshot,
            parent_span_id=deploy.span_id,
            force_violate=force_bad_copy,
        )
        await self._persist(marketing, title="营销产出完成", summary="文案/广告已生成")
        m_outs = [text_block(json.dumps(marketing.payload, ensure_ascii=False, indent=2), "marketing.json")]
        copy = marketing.payload.get("landing_copy") or {}
        if isinstance(copy, dict) and copy.get("headline"):
            m_outs.append(text_block(str(copy.get("headline")), "headline"))
        ad_url = await generate_image(
            f"Advertising creative poster for local service, headline: "
            f"{copy.get('headline','') if isinstance(copy, dict) else ''}, commercial photography"
        )
        if ad_url:
            m_outs.append(image_block(ad_url, content="ad creative", name="ad_creative"))
        # 透传上游图片到营销输出便于对照
        for block in (load_graph(trace_id) or {}).get("nodes", {}).get("marketing", {}).get("inputs") or []:
            if block.get("type") == "image" and block.get("url"):
                m_outs.append(image_block(block["url"], content="from_upstream", name="upstream_image"))
            if block.get("type") == "video" and block.get("url"):
                m_outs.append(video_block(block["url"], content=block.get("content") or "", name="upstream_video"))
        set_node(trace_id, "marketing", status="success", outputs=m_outs)

        set_node(
            trace_id,
            "redteam_marketing",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "redteam_marketing"),
        )
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "红队压力测试 #2", "status": "running", "agent": "redteam"},
        )
        try:
            rt2 = await self.redteam.review_marketing(
                trace_id=trace_id,
                theme_pack=theme_pack,
                marketing_payload=marketing.payload,
                parent_span_id=marketing.span_id,
            )
        except Exception as exc:  # noqa: BLE001
            rt2 = make_envelope(
                agent=AgentName.REDTEAM,
                status=EnvelopeStatus.SUCCESS,
                payload={
                    "gate": "marketing",
                    "passed": True,
                    "issues": [],
                    "soft_issues": [f"红队执行异常已降级: {exc}"],
                    "allow_hitl_override": True,
                },
                trace_id=trace_id,
                parent_span_id=marketing.span_id,
                theme_pack=theme_pack,
                model_route=ModelRoute.TIER_S,
            )
        await self._persist(
            rt2,
            title="红队#2 完成",
            summary="通过" if rt2.status == EnvelopeStatus.SUCCESS else "未通过",
        )
        set_node(
            trace_id,
            "redteam_marketing",
            status="success",
            outputs=[text_block(json.dumps(rt2.payload, ensure_ascii=False, indent=2), "redteam_marketing.json")],
        )
        needs_override = rt2.status == EnvelopeStatus.REJECTED_BY_REDTEAM
        if needs_override:
            self.store.add_lesson(
                theme_pack=theme_pack,
                outcome="failure",
                title="红队打回营销文案（待人工覆写）",
                summary="; ".join(rt2.payload.get("issues") or []),
                trace_id=trace_id,
            )

        hitl_id = str(uuid7())
        task = self.store.create_hitl(
            task_id=hitl_id,
            trace_id=trace_id,
            gate="paid_ads",
            title="确认付费投放（真金白银）" + ("（红队未通过，需人工覆写）" if needs_override else ""),
            body={
                "marketing": marketing.payload,
                "deploy": deploy.payload,
                "redteam": rt2.payload,
                "spend_cap_cny": (model_payload.get("budget_cap_test_cny") or 200),
                "needs_override": needs_override,
            },
        )
        set_node(
            trace_id,
            "hitl_ads",
            status="waiting",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "hitl_ads"),
            outputs=[text_block("等待人工审批 HITL#2", "hitl")],
        )
        pause = make_envelope(
            agent=AgentName.ORCHESTRATOR,
            status=EnvelopeStatus.NEEDS_HITL,
            payload={"hitl_id": hitl_id, "gate": "paid_ads"},
            trace_id=trace_id,
            parent_span_id=rt2.span_id,
            theme_pack=theme_pack,
            model_route=ModelRoute.TIER_XS,
        )
        await self._persist(pause, title="等待人工审批 HITL#2", summary="付费投放")
        result = {
            "trace_id": trace_id,
            "status": "needs_hitl",
            "gate": "paid_ads",
            "hitl": task,
            "marketing": marketing.payload,
            "deploy": deploy.payload,
            "redteam": rt2.payload,
        }
        await progress_bus.emit(trace_id, {"type": "run_paused", "status": "needs_hitl", "result": result})
        return result

    async def finalize_paid_ads(self, trace_id: str, spend_cny: float = 50.0) -> dict[str, Any]:
        """HITL#2 通过后模拟投放并回流飞轮。"""

        envelopes = self.store.get_trace_envelopes(trace_id)
        theme_pack = "local-service-leadgen"
        for item in envelopes:
            theme_pack = item.get("metadata", {}).get("theme_pack") or theme_pack

        set_node(trace_id, "hitl_ads", status="success", outputs=[text_block("HITL#2 已批准", "hitl")])
        set_node(
            trace_id,
            "flywheel",
            status="running",
            inputs=get_upstream_output_as_input(load_graph(trace_id), "flywheel"),
        )
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": "投放与飞轮回流", "status": "running", "agent": "flywheel"},
        )
        clicks = max(1, int(spend_cny * 2))
        conversions = max(0, clicks // 20)
        ctr = 0.045
        cvr = (conversions / clicks) if clicks else 0
        cpl = (spend_cny / conversions) if conversions else None
        roi = 1.4 if conversions else 0.0
        metrics = {
            "channel": "mock_search",
            "spend_cny": spend_cny,
            "clicks": clicks,
            "ctr": ctr,
            "conversions": conversions,
            "cvr": cvr,
            "cpl": cpl,
            "roi": roi,
        }
        fly = make_envelope(
            agent=AgentName.FLYWHEEL,
            status=EnvelopeStatus.SUCCESS,
            payload=metrics,
            trace_id=trace_id,
            theme_pack=theme_pack,
            model_route=ModelRoute.TIER_XS,
        )
        await self._persist(fly, title="飞轮回流", summary=f"ROI={roi}, CVR={cvr:.3f}")
        set_node(
            trace_id,
            "flywheel",
            status="success",
            outputs=[text_block(json.dumps(metrics, ensure_ascii=False, indent=2), "metrics.json")],
        )
        outcome = "success" if conversions > 0 else "failure"
        self.store.add_lesson(
            theme_pack=theme_pack,
            outcome=outcome,
            title=f"投放回流 ROI={roi}",
            summary=f"spend={spend_cny}, clicks={clicks}, cvr={cvr:.3f}, cpl={cpl}",
            trace_id=trace_id,
            metrics=metrics,
        )
        done = make_envelope(
            agent=AgentName.ORCHESTRATOR,
            status=EnvelopeStatus.SUCCESS,
            payload={"phase": "closed_loop", "metrics": metrics},
            trace_id=trace_id,
            theme_pack=theme_pack,
            model_route=ModelRoute.TIER_XS,
        )
        await self._persist(done, title="闭环完成", summary="closed_loop")
        result = {"trace_id": trace_id, "status": "closed_loop", "metrics": metrics}
        await progress_bus.emit(trace_id, {"type": "run_finished", "status": "closed_loop", "result": result})
        return result
