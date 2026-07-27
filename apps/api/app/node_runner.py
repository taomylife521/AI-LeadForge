# -*- coding: utf-8 -*-
"""
LeadForge 单节点执行器。

作用: 支持工作流中途修改输入后，按节点重跑并可选级联下游。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
from typing import Any, Optional

from app.agents.pipeline import (
    BusinessModelAgent,
    DeployAgent,
    DevAgent,
    MarketingAgent,
    OpportunityAgent,
    RedTeamAgent,
)
from app.agnes_media import generate_image
from app.envelope import AgentName, EnvelopeStatus, ModelRoute, make_envelope
from app.llm import LLMClient
from app.media_types import blocks_to_prompt, image_block, text_block, video_block
from app.memory.store import MemoryStore
from app.payload_normalize import extract_json_dict_from_blocks, normalize_business_model_payload
from app.progress import progress_bus
from app.workflow_graph import (
    get_upstream_output_as_input,
    load_graph,
    node_input_prompt,
    save_graph,
    set_node,
)


class NodeRunner:
    """按节点 ID 执行 Agent，并写回多模态 I/O。"""

    CASCADE_ORDER = [
        "opportunity",
        "business_model",
        "redteam_model",
        "hitl_model",
        "dev",
        "deploy",
        "marketing",
        "redteam_marketing",
        "hitl_ads",
        "flywheel",
    ]

    def __init__(self, store: MemoryStore, llm: Optional[LLMClient] = None) -> None:
        self.store = store
        self.llm = llm or LLMClient()
        self.opportunity = OpportunityAgent(self.llm)
        self.business_model = BusinessModelAgent(self.llm)
        self.redteam = RedTeamAgent(self.llm)
        self.dev = DevAgent(self.llm)
        self.deploy = DeployAgent(self.llm)
        self.marketing = MarketingAgent(self.llm)

    async def run_node(
        self,
        trace_id: str,
        node_id: str,
        *,
        cascade: bool = False,
        generate_visuals: bool = True,
    ) -> dict[str, Any]:
        """
        重跑指定节点。

        Args:
            trace_id: Trace。
            node_id: 节点 ID。
            cascade: 是否级联重跑下游。
            generate_visuals: 营销等节点是否尝试 Agnes 生图。
        """

        graph = load_graph(trace_id)
        if not graph:
            raise KeyError("工作流图不存在")
        if node_id == "start":
            # start 只同步输出=输入
            inputs = graph["nodes"]["start"].get("inputs") or []
            set_node(trace_id, "start", status="success", outputs=list(inputs))
            return load_graph(trace_id)  # type: ignore[return-value]

        # 若输入为空，用上游输出填充
        node = graph["nodes"][node_id]
        if not node.get("inputs"):
            set_node(trace_id, node_id, inputs=get_upstream_output_as_input(graph, node_id))
            graph = load_graph(trace_id)  # type: ignore[assignment]

        set_node(trace_id, node_id, status="running", error=None)
        await progress_bus.emit(
            trace_id,
            {"type": "phase", "title": f"重跑节点 {node_id}", "status": "running", "agent": node_id},
        )

        try:
            outputs = await self._execute(trace_id, node_id, generate_visuals=generate_visuals)
            set_node(trace_id, node_id, status="success", outputs=outputs, error=None)
        except Exception as exc:  # noqa: BLE001
            set_node(trace_id, node_id, status="failed", error=str(exc))
            await progress_bus.emit(
                trace_id,
                {"type": "step", "title": f"节点失败 {node_id}", "status": "failed", "summary": str(exc)},
            )
            raise

        graph = load_graph(trace_id)
        await progress_bus.emit(
            trace_id,
            {
                "type": "step",
                "title": f"节点完成 {node_id}",
                "status": "success",
                "agent": node_id,
                "payload": {"outputs": outputs},
            },
        )

        if cascade:
            start_idx = self.CASCADE_ORDER.index(node_id) if node_id in self.CASCADE_ORDER else -1
            for nxt in self.CASCADE_ORDER[start_idx + 1 :]:
                # HITL 节点不自动越过，停在等待
                if nxt.startswith("hitl_"):
                    set_node(trace_id, nxt, status="waiting", inputs=get_upstream_output_as_input(load_graph(trace_id), nxt))  # type: ignore[arg-type]
                    break
                # 下游输入刷新为上游输出
                g2 = load_graph(trace_id)
                set_node(trace_id, nxt, inputs=get_upstream_output_as_input(g2, nxt))  # type: ignore[arg-type]
                await self.run_node(trace_id, nxt, cascade=False, generate_visuals=generate_visuals)

        return load_graph(trace_id)  # type: ignore[return-value]

    async def _execute(self, trace_id: str, node_id: str, *, generate_visuals: bool) -> list[dict[str, Any]]:
        """执行具体节点逻辑，返回多模态 outputs。"""

        graph = load_graph(trace_id)
        assert graph
        theme_pack = graph.get("theme_pack") or "local-service-leadgen"
        prompt = node_input_prompt(graph, node_id)
        snapshot = self.store.build_snapshot(theme_pack, graph.get("topic") or "")

        if node_id == "opportunity":
            env = await self.opportunity.run(
                trace_id=trace_id,
                theme_pack=theme_pack,
                topic=prompt or graph.get("topic") or "",
                snapshot=snapshot,
                industry=str(graph.get("industry") or ""),
            )
            self.store.save_envelope(env)
            return [
                text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "opportunity.json"),
                text_block(str(env.payload.get("recommended") or ""), "recommended"),
                text_block(json.dumps(env.payload.get("sources") or [], ensure_ascii=False), "sources.json"),
            ]

        if node_id == "business_model":
            opportunity = extract_json_dict_from_blocks(graph["nodes"][node_id].get("inputs") or [])
            env = await self.business_model.run(
                trace_id=trace_id,
                theme_pack=theme_pack,
                opportunity=opportunity or {"raw": prompt},
                snapshot=snapshot,
            )
            env.payload = normalize_business_model_payload(env.payload)
            self.store.save_envelope(env)
            return [text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "business_model.json")]

        if node_id == "redteam_model":
            model_payload = extract_json_dict_from_blocks(graph["nodes"][node_id].get("inputs") or [])
            model_payload = normalize_business_model_payload(model_payload)
            try:
                env = await self.redteam.review_business_model(
                    trace_id=trace_id,
                    theme_pack=theme_pack,
                    model_payload=model_payload or {},
                )
            except Exception as exc:  # noqa: BLE001
                env = make_envelope(
                    agent=AgentName.REDTEAM,
                    status=EnvelopeStatus.SUCCESS,
                    payload={
                        "gate": "business_model",
                        "passed": True,
                        "issues": [],
                        "soft_issues": [f"红队异常降级: {exc}"],
                        "normalized_model": model_payload,
                        "allow_hitl_override": True,
                    },
                    trace_id=trace_id,
                    theme_pack=theme_pack,
                    model_route=ModelRoute.TIER_S,
                )
            self.store.save_envelope(env)
            set_node(trace_id, node_id, status="success")
            return [text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "redteam_model.json")]

        if node_id == "dev":
            model_payload = extract_json_dict_from_blocks(graph["nodes"][node_id].get("inputs") or [])
            # 若上游是红队输出，取 normalized_model
            if isinstance(model_payload.get("normalized_model"), dict):
                model_payload = model_payload["normalized_model"]
            else:
                model_payload = normalize_business_model_payload(model_payload)
            env = await self.dev.run(
                trace_id=trace_id,
                theme_pack=theme_pack,
                model_payload=model_payload or {},
                snapshot=snapshot,
            )
            self.store.save_envelope(env)
            outs = [text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "dev.json")]
            if generate_visuals:
                params = (env.payload or {}).get("params") or {}
                img_prompt = (
                    f"Landing page hero mockup for {params.get('brand','brand')}, "
                    f"headline '{params.get('headline','')}', clean modern local service, UI screenshot"
                )
                url = await generate_image(img_prompt)
                if url:
                    outs.append(image_block(url, content=img_prompt, name="landing_mockup"))
            return outs

        if node_id == "deploy":
            dev_payload = self._parse_json_from_inputs(graph["nodes"][node_id].get("inputs") or [])
            env = await self.deploy.run(
                trace_id=trace_id,
                theme_pack=theme_pack,
                dev_payload=dev_payload or {},
            )
            self.store.save_envelope(env)
            return [
                text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "deploy.json"),
                text_block(str(env.payload.get("preview_url") or ""), "preview_url"),
            ]

        if node_id == "marketing":
            context = self._parse_json_from_inputs(graph["nodes"][node_id].get("inputs") or [])
            # 从输入提取图片 URL 作为参考
            ref_images = [
                b.get("url")
                for b in (graph["nodes"][node_id].get("inputs") or [])
                if b.get("type") == "image" and b.get("url")
            ]
            env = await self.marketing.run(
                trace_id=trace_id,
                theme_pack=theme_pack,
                context=context or {"raw": prompt},
                snapshot=snapshot,
            )
            self.store.save_envelope(env)
            outs = [text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "marketing.json")]
            copy = env.payload.get("landing_copy") or {}
            if isinstance(copy, dict) and copy.get("headline"):
                outs.append(text_block(str(copy.get("headline")), "headline"))
            if generate_visuals:
                ad_prompt = (
                    f"Advertising creative poster, Chinese local service marketing, "
                    f"headline: {copy.get('headline','') if isinstance(copy, dict) else ''}, "
                    f"clean commercial photography, no text overload"
                )
                url = await generate_image(ad_prompt)
                if url:
                    outs.append(image_block(url, content=ad_prompt, name="ad_creative"))
                # 若用户提供了参考图，保留到输出便于对照
                for ref in ref_images[:2]:
                    outs.append(image_block(str(ref), content="user_reference", name="reference"))
            # 占位：视频 URL 可由用户手动贴入输入后透传
            for b in graph["nodes"][node_id].get("inputs") or []:
                if b.get("type") == "video" and b.get("url"):
                    outs.append(video_block(b["url"], content=b.get("content") or "", name="input_video"))
            return outs

        if node_id == "redteam_marketing":
            marketing_payload = extract_json_dict_from_blocks(graph["nodes"][node_id].get("inputs") or [])
            # 也把文本块拼进去给广告法检查
            if not marketing_payload:
                marketing_payload = {"landing_copy": prompt, "ads": [prompt]}
            try:
                env = await self.redteam.review_marketing(
                    trace_id=trace_id,
                    theme_pack=theme_pack,
                    marketing_payload=marketing_payload,
                )
            except Exception as exc:  # noqa: BLE001
                env = make_envelope(
                    agent=AgentName.REDTEAM,
                    status=EnvelopeStatus.SUCCESS,
                    payload={
                        "gate": "marketing",
                        "passed": True,
                        "issues": [],
                        "soft_issues": [f"红队异常降级: {exc}"],
                        "allow_hitl_override": True,
                    },
                    trace_id=trace_id,
                    theme_pack=theme_pack,
                    model_route=ModelRoute.TIER_S,
                )
            self.store.save_envelope(env)
            set_node(trace_id, node_id, status="success")
            return [text_block(json.dumps(env.payload, ensure_ascii=False, indent=2), "redteam_marketing.json")]

        if node_id in {"hitl_model", "hitl_ads"}:
            # HITL 节点：输出=输入，等待人工
            inputs = graph["nodes"][node_id].get("inputs") or []
            set_node(trace_id, node_id, status="waiting")
            return list(inputs)

        if node_id == "flywheel":
            # 简易飞轮：基于输入文本生成指标摘要
            metrics = {
                "channel": "mock_search",
                "spend_cny": 50,
                "clicks": 100,
                "ctr": 0.045,
                "conversions": 5,
                "cvr": 0.05,
                "cpl": 10,
                "roi": 1.4,
                "note": prompt[:200],
            }
            env = make_envelope(
                agent=AgentName.FLYWHEEL,
                status=EnvelopeStatus.SUCCESS,
                payload=metrics,
                trace_id=trace_id,
                theme_pack=theme_pack,
                model_route=ModelRoute.TIER_XS,
            )
            self.store.save_envelope(env)
            return [text_block(json.dumps(metrics, ensure_ascii=False, indent=2), "metrics.json")]

        raise ValueError(f"不支持重跑的节点: {node_id}")

    def _parse_json_from_inputs(self, inputs: list[dict[str, Any]]) -> dict[str, Any]:
        """从输入块中解析最佳 JSON 文本（兼容多块）。"""

        return extract_json_dict_from_blocks(inputs)
