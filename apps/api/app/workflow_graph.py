# -*- coding: utf-8 -*-
"""
LeadForge 工作流图（n8n/Dify 风格节点链路）。

作用: 持久化每个节点的多模态输入/输出，支持模板、节点级 config，以及中途修改输入并重跑。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.media_types import MediaBlock, blocks_to_prompt, text_block
from app.settings import data_dir
from app.workflow_templates import load_workflow_template


# 兼容旧引用
NODE_DEFS: list[dict[str, Any]] = load_workflow_template("default-closed-loop")["nodes"]
EDGES: list[list[str]] = load_workflow_template("default-closed-loop")["edges"]


def _graph_path(trace_id: str) -> Path:
    folder = data_dir() / "graphs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{trace_id}.json"


def new_graph(
    trace_id: str,
    *,
    topic: str = "",
    theme_pack: str = "local-service-leadgen",
    template_id: str = "default-closed-loop",
) -> dict[str, Any]:
    """
    按模板创建空工作流图。

    Args:
        template_id: 工作流模板 id。
    """

    template = load_workflow_template(template_id)
    bindings = template.get("node_bindings") if isinstance(template.get("node_bindings"), dict) else {}
    nodes: dict[str, Any] = {}
    for defn in template.get("nodes") or []:
        nid = defn["id"]
        node_bind = bindings.get(nid) if isinstance(bindings.get(nid), dict) else {}
        config = {
            "agent_key": defn.get("agent_key") or node_bind.get("agent_key"),
            "model": node_bind.get("model"),
            "skills": node_bind.get("skills"),
            "rules": node_bind.get("rules"),
            "mcp": node_bind.get("mcp"),
            "prompt": node_bind.get("prompt") or "",
            "extra_system": node_bind.get("extra_system") or "",
        }
        nodes[nid] = {
            "id": nid,
            "label": defn.get("label") or nid,
            "kind": defn.get("kind") or "agent",
            "x": defn.get("x") or 0,
            "y": defn.get("y") or 0,
            "agent_key": config.get("agent_key"),
            "config": config,
            "status": "idle",
            "inputs": [],
            "outputs": [],
            "error": None,
            "updated_at": None,
        }
    if "start" in nodes:
        nodes["start"]["inputs"] = [text_block(topic or "（自动推荐主题）", "topic")]
        nodes["start"]["outputs"] = [text_block(topic or "（自动推荐主题）", "topic")]
        nodes["start"]["status"] = "success"
    graph = {
        "trace_id": trace_id,
        "theme_pack": theme_pack,
        "topic": topic,
        "template_id": template.get("id") or template_id,
        "template_name": template.get("name") or template_id,
        "nodes": nodes,
        "edges": template.get("edges") or EDGES,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_graph(graph)
    return graph


def load_graph(trace_id: str) -> Optional[dict[str, Any]]:
    """加载工作流图。"""

    path = _graph_path(trace_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_graph(graph: dict[str, Any]) -> None:
    """保存工作流图。"""

    graph["updated_at"] = datetime.now(timezone.utc).isoformat()
    _graph_path(graph["trace_id"]).write_text(
        json.dumps(graph, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def set_node(
    trace_id: str,
    node_id: str,
    *,
    status: Optional[str] = None,
    inputs: Optional[list[dict[str, Any]]] = None,
    outputs: Optional[list[dict[str, Any]]] = None,
    error: Any = None,
    merge_outputs: bool = False,
) -> dict[str, Any]:
    """更新单个节点状态。"""

    graph = load_graph(trace_id)
    if not graph:
        raise KeyError(f"graph not found: {trace_id}")
    node = graph["nodes"].get(node_id)
    if not node:
        raise KeyError(f"node not found: {node_id}")
    if status is not None:
        node["status"] = status
    if inputs is not None:
        node["inputs"] = inputs
    if outputs is not None:
        if merge_outputs:
            node["outputs"] = (node.get("outputs") or []) + outputs
        else:
            node["outputs"] = outputs
    if error is not None:
        node["error"] = error
    node["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_graph(graph)
    return graph


def update_node_inputs(trace_id: str, node_id: str, inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """人工干预：修改节点输入（强校验 MediaBlock）。"""

    validated = [MediaBlock.model_validate(item).model_dump() for item in inputs]
    return set_node(trace_id, node_id, inputs=validated, status="idle")


def get_upstream_output_as_input(graph: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    """取上游节点输出作为本节点默认输入。"""

    preds = [a for a, b in graph.get("edges", []) if b == node_id]
    blocks: list[dict[str, Any]] = []
    for pid in preds:
        blocks.extend(graph["nodes"].get(pid, {}).get("outputs") or [])
    return blocks


def node_input_prompt(graph: dict[str, Any], node_id: str) -> str:
    """读取节点当前输入并折叠为 prompt。"""

    node = graph["nodes"][node_id]
    inputs = node.get("inputs") or get_upstream_output_as_input(graph, node_id)
    return blocks_to_prompt(inputs)


def update_node_config(trace_id: str, node_id: str, config: dict[str, Any]) -> dict[str, Any]:
    """
    更新节点级配置（模型/Skill/提示词/替换 agent_key）。

    Args:
        trace_id: Trace。
        node_id: 节点。
        config: 部分更新字段。

    Returns:
        更新后的图。
    """

    graph = load_graph(trace_id)
    if not graph:
        raise KeyError(f"graph not found: {trace_id}")
    node = graph["nodes"].get(node_id)
    if not node:
        raise KeyError(f"node not found: {node_id}")
    current = dict(node.get("config") or {})
    for key, value in (config or {}).items():
        if value is None:
            continue
        current[key] = value
    node["config"] = current
    if current.get("agent_key"):
        node["agent_key"] = current["agent_key"]
    if config.get("label"):
        node["label"] = str(config["label"])
    node["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_graph(graph)
    return graph
