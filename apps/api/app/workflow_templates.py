# -*- coding: utf-8 -*-
"""
工作流模板加载。

作用: 从 config/workflow_templates 读取可切换拓扑与每节点默认绑定（模型/Skill/提示词）。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Optional

import yaml

from app.settings import config_dir, data_dir


def _templates_dir() -> Path:
    return config_dir() / "workflow_templates"


def _custom_templates_dir() -> Path:
    """用户可写模板目录（data/workflow_templates）。"""

    path = data_dir() / "workflow_templates"
    path.mkdir(parents=True, exist_ok=True)
    return path


# 与 workflow_graph 默认拓扑一致的内置模板（文件缺失时兜底）
BUILTIN_DEFAULT: dict[str, Any] = {
    "id": "default-closed-loop",
    "name": "默认商业闭环",
    "description": "商机→模式→红队→HITL→开发→部署→营销→投放HITL→飞轮",
    "nodes": [
        {"id": "start", "label": "主题输入", "kind": "input", "x": 40, "y": 220},
        {"id": "opportunity", "label": "商机", "kind": "agent", "x": 220, "y": 220, "agent_key": "opportunity"},
        {"id": "business_model", "label": "模式", "kind": "agent", "x": 400, "y": 220, "agent_key": "business_model"},
        {"id": "redteam_model", "label": "红队·模式", "kind": "redteam", "x": 580, "y": 120, "agent_key": "redteam"},
        {"id": "hitl_model", "label": "HITL·模式", "kind": "hitl", "x": 760, "y": 220},
        {"id": "dev", "label": "开发", "kind": "agent", "x": 940, "y": 220, "agent_key": "dev"},
        {"id": "deploy", "label": "部署", "kind": "agent", "x": 1120, "y": 220, "agent_key": "deploy"},
        {"id": "marketing", "label": "营销", "kind": "agent", "x": 1300, "y": 220, "agent_key": "marketing"},
        {"id": "redteam_marketing", "label": "红队·营销", "kind": "redteam", "x": 1480, "y": 120, "agent_key": "redteam"},
        {"id": "hitl_ads", "label": "HITL·投放", "kind": "hitl", "x": 1660, "y": 220},
        {"id": "flywheel", "label": "飞轮", "kind": "agent", "x": 1840, "y": 220, "agent_key": "flywheel"},
    ],
    "edges": [
        ["start", "opportunity"],
        ["opportunity", "business_model"],
        ["business_model", "redteam_model"],
        ["redteam_model", "hitl_model"],
        ["hitl_model", "dev"],
        ["dev", "deploy"],
        ["deploy", "marketing"],
        ["marketing", "redteam_marketing"],
        ["redteam_marketing", "hitl_ads"],
        ["hitl_ads", "flywheel"],
    ],
    "node_bindings": {},
}


def list_workflow_templates() -> list[dict[str, Any]]:
    """
    列出可用工作流模板（摘要）：内置 config + 用户 data 自定义。

    Returns:
        [{id, name, description, node_count, source, editable}, ...]
    """

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def absorb(folder: Path, *, editable: bool, source_tag: str) -> None:
        if not folder.exists():
            return
        for path in sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml")):
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            tid = str(doc.get("id") or path.stem)
            if tid in seen:
                # 用户自定义覆盖同名内置摘要展示
                items[:] = [i for i in items if i["id"] != tid]
            seen.add(tid)
            items.append(
                {
                    "id": tid,
                    "name": str(doc.get("name") or tid),
                    "description": str(doc.get("description") or ""),
                    "node_count": len(doc.get("nodes") or []),
                    "source": source_tag if not editable else str(path.name),
                    "editable": editable,
                }
            )

    absorb(_templates_dir(), editable=False, source_tag="config")
    absorb(_custom_templates_dir(), editable=True, source_tag="custom")
    if not any(i["id"] == BUILTIN_DEFAULT["id"] for i in items):
        items.insert(
            0,
            {
                "id": BUILTIN_DEFAULT["id"],
                "name": BUILTIN_DEFAULT["name"],
                "description": BUILTIN_DEFAULT["description"],
                "node_count": len(BUILTIN_DEFAULT["nodes"]),
                "source": "builtin",
                "editable": False,
            },
        )
    return items


def load_workflow_template(template_id: str = "") -> dict[str, Any]:
    """
    加载完整工作流模板。

    Args:
        template_id: 模板 id；空则返回默认闭环。

    Returns:
        含 nodes/edges/node_bindings 的模板文档。
    """

    wanted = (template_id or "").strip() or BUILTIN_DEFAULT["id"]
    # 优先用户自定义
    for folder in (_custom_templates_dir(), _templates_dir()):
        if not folder.exists():
            continue
        for path in list(folder.glob("*.yaml")) + list(folder.glob("*.yml")):
            try:
                doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:  # noqa: BLE001
                continue
            tid = str(doc.get("id") or path.stem)
            if tid == wanted:
                out = _normalize_template(doc, fallback_id=tid)
                out["editable"] = folder == _custom_templates_dir()
                return out
    if wanted == BUILTIN_DEFAULT["id"] or not wanted:
        doc = copy.deepcopy(BUILTIN_DEFAULT)
        doc["editable"] = False
        return doc
    # 未知 id：仍回退默认，并标注
    doc = copy.deepcopy(BUILTIN_DEFAULT)
    doc["requested_id"] = wanted
    doc["fallback"] = True
    doc["editable"] = False
    return doc


def save_workflow_template(payload: dict[str, Any], *, overwrite: bool = True) -> dict[str, Any]:
    """
    保存用户自定义工作流模板到 data/workflow_templates。

    Args:
        payload: 含 id/name/description/nodes/edges/node_bindings；可只给 clone_from。
        overwrite: 是否允许覆盖已有自定义模板。

    Returns:
        规范化后的模板摘要。
    """

    clone_from = str(payload.get("clone_from") or "").strip()
    if clone_from:
        base = load_workflow_template(clone_from)
        doc = copy.deepcopy(base)
        doc.pop("fallback", None)
        doc.pop("requested_id", None)
        doc.pop("editable", None)
    else:
        doc = dict(payload)

    tid = str(payload.get("id") or doc.get("id") or "").strip()
    if not tid:
        raise ValueError("模板 id 必填")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_\-]{1,48}$", tid):
        raise ValueError("模板 id 仅允许字母开头的字母数字_-")
    if tid == BUILTIN_DEFAULT["id"] and not clone_from:
        raise ValueError("不可直接覆盖内置 default-closed-loop，请用新 id 另存")

    doc["id"] = tid
    if payload.get("name"):
        doc["name"] = str(payload["name"]).strip()
    if "description" in payload:
        doc["description"] = str(payload.get("description") or "")
    if isinstance(payload.get("nodes"), list) and payload["nodes"]:
        doc["nodes"] = payload["nodes"]
    if isinstance(payload.get("edges"), list) and payload["edges"]:
        doc["edges"] = payload["edges"]
    if isinstance(payload.get("node_bindings"), dict):
        doc["node_bindings"] = payload["node_bindings"]

    doc = _normalize_template(doc, fallback_id=tid)
    path = _custom_templates_dir() / f"{tid}.yaml"
    if path.exists() and not overwrite:
        raise ValueError(f"模板已存在: {tid}")
    path.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return {
        "id": doc["id"],
        "name": doc["name"],
        "description": doc["description"],
        "node_count": len(doc.get("nodes") or []),
        "editable": True,
        "path": str(path),
    }


def delete_workflow_template(template_id: str) -> bool:
    """删除用户自定义模板（不可删 config 内置文件）。"""

    tid = (template_id or "").strip()
    if not tid or tid == BUILTIN_DEFAULT["id"]:
        raise ValueError("不可删除内置默认模板")
    path = _custom_templates_dir() / f"{tid}.yaml"
    alt = _custom_templates_dir() / f"{tid}.yml"
    removed = False
    for p in (path, alt):
        if p.exists():
            p.unlink()
            removed = True
    return removed


def _normalize_template(doc: dict[str, Any], *, fallback_id: str) -> dict[str, Any]:
    """补齐模板必要字段。"""

    out = copy.deepcopy(doc)
    out["id"] = str(out.get("id") or fallback_id)
    out["name"] = str(out.get("name") or out["id"])
    out["description"] = str(out.get("description") or "")
    nodes = out.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        out["nodes"] = copy.deepcopy(BUILTIN_DEFAULT["nodes"])
    edges = out.get("edges")
    if not isinstance(edges, list) or not edges:
        out["edges"] = copy.deepcopy(BUILTIN_DEFAULT["edges"])
    if not isinstance(out.get("node_bindings"), dict):
        out["node_bindings"] = {}
    return out


def resolve_node_binding(
    template: Optional[dict[str, Any]],
    node_id: str,
    node: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    合并模板默认绑定与节点运行时 config。

    优先级: 节点 config > 模板 node_bindings > 空。

    Returns:
        {agent_key, model, skills, rules, mcp, prompt, extra_system}
    """

    base: dict[str, Any] = {}
    if template and isinstance(template.get("node_bindings"), dict):
        raw = template["node_bindings"].get(node_id) or {}
        if isinstance(raw, dict):
            base.update(raw)
    cfg = (node or {}).get("config") if isinstance(node, dict) else None
    if isinstance(cfg, dict):
        base.update({k: v for k, v in cfg.items() if v is not None and v != ""})
    if isinstance(node, dict) and node.get("agent_key") and not base.get("agent_key"):
        base["agent_key"] = node["agent_key"]
    return base
