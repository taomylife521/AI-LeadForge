# -*- coding: utf-8 -*-
"""
LeadForge Agent 动态绑定注册表。

作用: 为每个 Agent 管理可热替换的 Skill / Rule / MCP / Model 绑定；支持 UI 与 API 动态更新。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from app.settings import config_dir, data_dir, load_skills_allowlist, repo_root
from app.run_trace import normalize_mcp_entries


AGENT_KEYS = [
    "opportunity",
    "business_model",
    "redteam",
    "dev",
    "deploy",
    "marketing",
    "flywheel",
    "harness",
]

# 工作流节点 → Agent 绑定键
NODE_TO_AGENT = {
    "opportunity": "opportunity",
    "business_model": "business_model",
    "redteam_model": "redteam",
    "redteam_marketing": "redteam",
    "dev": "dev",
    "deploy": "deploy",
    "marketing": "marketing",
    "flywheel": "flywheel",
    "hitl_model": "business_model",
    "hitl_ads": "marketing",
    "start": "opportunity",
}


def _defaults_path() -> Path:
    return config_dir() / "agent_bindings.yaml"


def _runtime_path() -> Path:
    return data_dir() / "agent_bindings.json"


def _load_yaml_defaults() -> dict[str, Any]:
    path = _defaults_path()
    if not path.exists():
        return {"agents": {}, "mcp_catalog": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"agents": {}, "mcp_catalog": []}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_bindings() -> dict[str, Any]:
    """
    加载绑定：默认 YAML + 运行时 JSON 覆盖。

    Returns:
        完整绑定文档。
    """

    defaults = _load_yaml_defaults()
    runtime_path = _runtime_path()
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
        merged = _deep_merge(defaults, runtime)
    else:
        merged = defaults
    # 保证 agents 键齐全，并规范化 mcp（YAML 字符串 → 对象）
    agents = merged.setdefault("agents", {})
    for key in AGENT_KEYS:
        agents.setdefault(
            key,
            {
                "label": key,
                "model": {"mode": "route", "route": "tier_m", "profile_id": None},
                "skills": [],
                "rules": [],
                "mcp": [],
            },
        )
        agent = agents[key]
        if not isinstance(agent.get("model"), dict):
            agent["model"] = {
                "mode": "route",
                "route": "tier_m",
                "profile_id": None,
                "legacy_model": agent.get("model"),
            }
        agent["mcp"] = normalize_mcp_entries(agent.get("mcp"))
    return merged


def save_runtime_bindings(doc: dict[str, Any]) -> dict[str, Any]:
    """将覆盖层写入 data/agent_bindings.json（不改仓库默认 YAML）。"""

    payload = {
        "agents": doc.get("agents") or {},
        "mcp_catalog": doc.get("mcp_catalog"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # 若 mcp_catalog 为空则不强制覆盖默认
    if payload["mcp_catalog"] is None:
        payload.pop("mcp_catalog", None)
    path = _runtime_path()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_bindings()


def get_agent_binding(agent_key: str) -> dict[str, Any]:
    """获取单个 Agent 绑定。"""

    key = NODE_TO_AGENT.get(agent_key, agent_key)
    doc = load_bindings()
    binding = doc.get("agents", {}).get(key)
    if not binding:
        raise KeyError(f"未知 Agent: {agent_key}")
    return {"agent_key": key, **copy.deepcopy(binding)}


def update_agent_binding(agent_key: str, patch: dict[str, Any]) -> dict[str, Any]:
    """
    动态更新某个 Agent 的绑定（Skill/Rule/MCP/Model）。

    Args:
        agent_key: Agent 或节点 ID。
        patch: 可含 model/skills/rules/mcp/label；skills/rules/mcp 为全量替换。
    """

    key = NODE_TO_AGENT.get(agent_key, agent_key)
    if key not in AGENT_KEYS:
        raise KeyError(f"不可绑定的 Agent: {agent_key}")

    doc = load_bindings()
    current = copy.deepcopy(doc["agents"][key])

    if "label" in patch and patch["label"] is not None:
        current["label"] = str(patch["label"])

    if "model" in patch and patch["model"] is not None:
        model = dict(current.get("model") or {})
        model.update(patch["model"])
        # 规范化 mode
        mode = model.get("mode") or "route"
        if mode not in {"route", "profile", "explicit"}:
            raise ValueError("model.mode 必须是 route | profile | explicit")
        model["mode"] = mode
        current["model"] = model

    if "skills" in patch and patch["skills"] is not None:
        current["skills"] = [str(s).strip() for s in patch["skills"] if str(s).strip()]

    if "rules" in patch and patch["rules"] is not None:
        current["rules"] = [str(s).strip() for s in patch["rules"] if str(s).strip()]

    if "mcp" in patch and patch["mcp"] is not None:
        mcp_list = []
        for item in patch["mcp"]:
            if isinstance(item, str):
                mcp_list.append({"name": item, "enabled": True})
            elif isinstance(item, dict) and item.get("name"):
                mcp_list.append(
                    {
                        "name": str(item["name"]),
                        "enabled": bool(item.get("enabled", True)),
                        "config": item.get("config") or {},
                    }
                )
        current["mcp"] = mcp_list

    if "prompt" in patch and patch["prompt"] is not None:
        current["prompt"] = str(patch["prompt"])
    if "extra_system" in patch and patch["extra_system"] is not None:
        current["extra_system"] = str(patch["extra_system"])

    # 只把 agents 覆盖写入 runtime
    runtime: dict[str, Any] = {"agents": {}}
    if _runtime_path().exists():
        runtime = json.loads(_runtime_path().read_text(encoding="utf-8"))
        runtime.setdefault("agents", {})
    runtime["agents"][key] = current
    save_runtime_bindings(runtime)
    return get_agent_binding(key)


def replace_agent_binding(agent_key: str, binding: dict[str, Any]) -> dict[str, Any]:
    """全量替换某个 Agent 绑定。"""

    return update_agent_binding(
        agent_key,
        {
            "label": binding.get("label"),
            "model": binding.get("model"),
            "skills": binding.get("skills"),
            "rules": binding.get("rules"),
            "mcp": binding.get("mcp"),
        },
    )


def reset_agent_binding(agent_key: str) -> dict[str, Any]:
    """重置为 YAML 默认（删除 runtime 覆盖）。"""

    key = NODE_TO_AGENT.get(agent_key, agent_key)
    runtime: dict[str, Any] = {"agents": {}}
    if _runtime_path().exists():
        runtime = json.loads(_runtime_path().read_text(encoding="utf-8"))
    runtime.setdefault("agents", {}).pop(key, None)
    save_runtime_bindings(runtime)
    return get_agent_binding(key)


def list_skill_catalog() -> list[dict[str, Any]]:
    """合并 allowlist 与当前绑定中出现的 skill id。"""

    allow = load_skills_allowlist()
    seen: dict[str, dict[str, Any]] = {}
    for item in allow.get("meta_skills") or []:
        seen[item["id"]] = {"id": item["id"], "repo": item.get("repo"), "tier": item.get("tier"), "source": "allowlist"}
    for agent_skills in (allow.get("agents") or {}).values():
        for item in agent_skills or []:
            seen[item["id"]] = {
                "id": item["id"],
                "repo": item.get("repo"),
                "tier": item.get("tier"),
                "source": "allowlist",
            }
    for agent in (load_bindings().get("agents") or {}).values():
        for sid in agent.get("skills") or []:
            seen.setdefault(sid, {"id": sid, "source": "binding"})
    return sorted(seen.values(), key=lambda x: x["id"])


def list_rule_catalog() -> list[dict[str, Any]]:
    """扫描 rules/ 与 theme-packs/*/AGENTS.md。"""

    items: dict[str, dict[str, Any]] = {}
    rules_root = repo_root() / "rules"
    if rules_root.exists():
        for path in rules_root.rglob("*.md"):
            rel = path.relative_to(rules_root).as_posix()
            rid = rel if rel.startswith("global/") else f"global/{rel}"
            items[rid] = {"id": rid, "path": str(path), "source": "rules"}

    packs = repo_root() / "theme-packs"
    if packs.exists():
        for path in packs.glob("*/AGENTS.md"):
            pack = path.parent.name
            rid = f"theme-pack:{pack}/AGENTS.md"
            items[rid] = {"id": rid, "path": str(path), "source": "theme"}
    return sorted(items.values(), key=lambda x: x["id"])


def list_mcp_catalog() -> list[dict[str, Any]]:
    """MCP 服务器目录。"""

    doc = load_bindings()
    return list(doc.get("mcp_catalog") or [])


def resolve_rule_texts(rule_ids: list[str], theme_pack: str = "local-service-leadgen") -> str:
    """读取 Rule 文件内容，拼接到系统提示。"""

    chunks: list[str] = []
    for rid in rule_ids or []:
        path: Optional[Path] = None
        if rid.startswith("theme-pack:"):
            # theme-pack:xxx/AGENTS.md
            rest = rid[len("theme-pack:") :]
            path = repo_root() / "theme-packs" / rest
        elif rid.startswith("global/"):
            path = repo_root() / "rules" / rid
            if not path.exists():
                path = repo_root() / "rules" / "global" / rid.split("/", 1)[-1]
        else:
            path = repo_root() / "rules" / rid
            if not path.exists():
                path = repo_root() / "rules" / "global" / rid
        # 主题默认 AGENTS
        if rid == "theme:AGENTS.md":
            path = repo_root() / "theme-packs" / theme_pack / "AGENTS.md"
        if path and path.exists():
            chunks.append(f"### RULE {rid}\n{path.read_text(encoding='utf-8')}")
        else:
            chunks.append(f"### RULE {rid}\n(missing file)")
    return "\n\n".join(chunks)


def resolve_skill_texts(skill_ids: list[str]) -> str:
    """尽量读取 skills/vendor 或常见 Cursor skills 路径中的 SKILL.md。"""

    chunks: list[str] = []
    search_roots = [
        repo_root() / "skills" / "vendor",
        repo_root() / "skills",
        Path.home() / ".cursor" / "skills",
        Path.home() / ".agents" / "skills",
    ]
    for sid in skill_ids or []:
        found = None
        for root in search_roots:
            if not root.exists():
                continue
            # 直接子目录
            candidate = root / sid / "SKILL.md"
            if candidate.exists():
                found = candidate
                break
            # 模糊：任意 **/sid/SKILL.md
            matches = list(root.glob(f"**/{sid}/SKILL.md"))
            if matches:
                found = matches[0]
                break
        if found:
            text = found.read_text(encoding="utf-8")
            # 截断避免撑爆上下文
            chunks.append(f"### SKILL {sid}\n{text[:6000]}")
        else:
            chunks.append(f"### SKILL {sid}\n(registered id only — SKILL.md not found locally)")
    return "\n\n".join(chunks)


def build_agent_system_prefix(
    agent_key: str,
    theme_pack: str = "local-service-leadgen",
    *,
    prompt_override: str = "",
    extra_system_override: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    根据绑定生成系统提示前缀，并返回绑定快照。

    Args:
        prompt_override: 节点级自定义提示词（追加）。
        extra_system_override: 节点级额外系统约束。

    Returns:
        (prefix_text, binding_snapshot)
    """

    binding = get_agent_binding(agent_key)
    skills = binding.get("skills") or []
    rules = binding.get("rules") or []
    mcp = normalize_mcp_entries(binding.get("mcp"))
    mcp = [m for m in mcp if m.get("enabled", True)]
    parts = [
        f"你是 LeadForge 的 {binding.get('label') or agent_key}。",
        "必须遵守已绑定的 Rule；优先复用已绑定 Skill 的程序知识。",
        f"已绑定 Skills: {', '.join(skills) or '(none)'}",
        f"已绑定 MCP: {', '.join(m.get('name') for m in mcp) or '(none)'}",
    ]
    # 全局绑定里的自定义提示词
    bind_prompt = str(binding.get("prompt") or binding.get("extra_system") or "").strip()
    if bind_prompt:
        parts.append("==== AGENT CUSTOM PROMPT ====\n" + bind_prompt)
    rule_text = resolve_rule_texts(rules, theme_pack=theme_pack)
    skill_text = resolve_skill_texts(skills)
    if rule_text:
        parts.append("==== BOUND RULES ====\n" + rule_text)
    if skill_text:
        parts.append("==== BOUND SKILLS ====\n" + skill_text)
    if mcp:
        parts.append(
            "==== BOUND MCP (names only; tools discovered at runtime) ====\n"
            + json.dumps(mcp, ensure_ascii=False)
        )
    node_prompt = (prompt_override or "").strip()
    if node_prompt:
        parts.append("==== NODE CUSTOM PROMPT ====\n" + node_prompt)
    node_extra = (extra_system_override or "").strip()
    if node_extra:
        parts.append("==== NODE EXTRA SYSTEM ====\n" + node_extra)
    return "\n\n".join(parts), binding
