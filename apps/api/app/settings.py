# -*- coding: utf-8 -*-
"""
LeadForge 配置与路径解析。

作用: 统一读取环境变量、模型档案、主题包与数据目录。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


def _detect_root() -> Path:
    """推断仓库根目录（兼容 Docker / 本地 / Vercel）。"""

    candidates = [
        Path("/app"),
        Path(__file__).resolve().parents[3],
        Path.cwd(),
        Path(os.getenv("VERCEL_PROJECT_PATH") or ""),
        Path(__file__).resolve().parents[4] if len(Path(__file__).resolve().parents) > 4 else Path.cwd(),
    ]
    for candidate in candidates:
        if not candidate or str(candidate) in (".", ""):
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / "config").exists() or (resolved / "docker-compose.yml").exists():
            return resolved
        if (resolved / "model_profiles").exists():
            return resolved.parent if resolved.name == "config" else resolved
    return Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """运行时配置。"""

    model_config = SettingsConfigDict(
        env_file=(_detect_root() / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    leadforge_data_dir: str = "/data"
    litellm_base_url: str = "http://localhost:4000"
    litellm_master_key: str = ""
    qdrant_url: str = "http://localhost:6333"
    redis_url: str = "redis://localhost:6379/0"
    model_profile: str = "agnes-free"
    default_theme_pack: str = "local-service-leadgen"
    leadforge_api_token: str = ""
    mock_llm: bool = False
    agnes_api_key: str = ""
    agnes_token: str = ""
    agnes_api_base: str = "https://apihub.agnes-ai.com"

    # TrendRadar MCP sidecar（独立 GPL 服务）
    trendradar_mcp_url: str = "http://127.0.0.1:3333/mcp"
    trendradar_web_url: str = "http://127.0.0.1:8081"
    # TrendRadar 同源热榜（MCP 未启动时直连 newsnow）
    newsnow_api_url: str = "https://newsnow.busiyi.world/api/s"

    # Paperclip 控制面 sidecar
    paperclip_base_url: str = "http://127.0.0.1:3100"
    paperclip_company_id: str = ""
    paperclip_project_id: str = ""
    paperclip_goal_id: str = ""
    paperclip_agent_id: str = ""
    paperclip_api_key: str = ""

@lru_cache
def get_settings() -> Settings:
    """获取单例配置。"""

    return Settings()


def repo_root() -> Path:
    """仓库根路径。"""

    # Docker 中 config 挂载到 /app/config
    if Path("/app/config").exists():
        return Path("/app")
    return _detect_root()


def config_dir() -> Path:
    """配置目录。"""

    root = repo_root()
    if (root / "config").exists():
        return root / "config"
    return root


def data_dir() -> Path:
    """可写数据目录。"""

    settings = get_settings()
    raw = (settings.leadforge_data_dir or "").strip()
    # Vercel / 无持久盘：优先显式路径，否则 /tmp
    if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
        path = Path(raw) if raw and raw != "/data" else Path("/tmp/leadforge-data")
    else:
        path = Path(raw) if raw else Path("/data")
    if not path.exists():
        # 本地开发回退
        fallback = repo_root() / "data"
        path = fallback if not (os.getenv("VERCEL") or os.getenv("VERCEL_ENV")) else path
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_model_profiles() -> list[dict[str, Any]]:
    """加载全部模型档案。"""

    folder = config_dir() / "model_profiles"
    profiles: list[dict[str, Any]] = []
    if not folder.exists():
        return profiles
    for file in sorted(folder.glob("*.json")):
        profiles.append(json.loads(file.read_text(encoding="utf-8")))
    return profiles


def get_active_profile_id() -> str:
    """读取当前激活的模型档案 ID。"""

    runtime = data_dir() / "active_profile.json"
    if runtime.exists():
        data = json.loads(runtime.read_text(encoding="utf-8"))
        if data.get("profile_id"):
            return str(data["profile_id"])
    active = config_dir() / "active_profile.json"
    if active.exists():
        data = json.loads(active.read_text(encoding="utf-8"))
        return str(data.get("profile_id") or get_settings().model_profile)
    return get_settings().model_profile


def set_active_profile_id(profile_id: str, model_override: str = "") -> dict[str, Any]:
    """
    切换模型档案（一键切换）。

    Args:
        profile_id: 档案 ID。
        model_override: 可选；免费目录下覆盖三档模型名。

    Returns:
        写入后的 active_profile 内容。

    Raises:
        ValueError: 档案不存在。
    """

    from app.providers import get_free_catalog_item, load_all_profiles

    profiles = {p["id"]: p for p in load_all_profiles()}
    if profile_id not in profiles:
        raise ValueError(f"未知模型档案: {profile_id}")
    from datetime import datetime, timezone

    selected = profiles[profile_id]
    routes = dict(
        selected.get("routes")
        or {"tier_s": "agnes-2.0-flash", "tier_m": "agnes-2.0-flash", "tier_xs": "agnes-2.0-flash"}
    )
    override = (model_override or "").strip()
    if override:
        # 免费目录：校验模型属于该 provider 的 models 列表
        catalog_item = get_free_catalog_item(profile_id)
        if catalog_item:
            allowed = set(catalog_item.get("models") or [])
            allowed.add(str(catalog_item.get("default_model") or ""))
            if allowed and override not in allowed:
                raise ValueError(f"模型 {override} 不在 {profile_id} 的免费模型列表中")
        routes = {"tier_s": override, "tier_m": override, "tier_xs": override}

    payload = {
        "profile_id": profile_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "routes": routes,
        "provider": selected.get("provider"),
        "model_override": override or None,
        "group": selected.get("group"),
    }
    runtime = data_dir() / "active_profile.json"
    runtime.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    config_target = config_dir() / "active_profile.json"
    try:
        config_target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return payload


def get_active_routes() -> dict[str, str]:
    """获取当前档位→物理模型映射。"""

    runtime = data_dir() / "active_profile.json"
    if runtime.exists():
        data = json.loads(runtime.read_text(encoding="utf-8"))
        if "routes" in data:
            return dict(data["routes"])
    profile_id = get_active_profile_id()
    from app.providers import load_all_profiles

    for profile in load_all_profiles():
        if profile["id"] == profile_id:
            return dict(profile["routes"])
    return {
        "tier_s": "agnes-2.0-flash",
        "tier_m": "agnes-2.0-flash",
        "tier_xs": "agnes-2.0-flash",
    }


def load_skills_allowlist() -> dict[str, Any]:
    """加载 Skill 白名单。"""

    path = config_dir() / "skills.allowlist.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_theme_packs() -> list[dict[str, Any]]:
    """列出可插拔主题包。"""

    root = repo_root() / "theme-packs"
    packs: list[dict[str, Any]] = []
    if not root.exists():
        return packs
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        meta_path = folder / "pack.yaml"
        meta: dict[str, Any] = {"id": folder.name}
        if meta_path.exists():
            meta.update(yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {})
        packs.append(meta)
    return packs


def env_present(keys: list[str]) -> dict[str, bool]:
    """检查模型档案所需环境变量是否已配置。"""

    return {key: bool(os.getenv(key)) for key in keys}
