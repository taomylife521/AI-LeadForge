# -*- coding: utf-8 -*-
"""
LeadForge 模型提供商注册表。

作用: 管理内置档案、cheahjs 免费 LLM 目录、自定义 OpenAI 兼容模型，解析调用端点与密钥。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

from uuid6 import uuid7

from app.settings import (
    config_dir,
    data_dir,
    get_active_profile_id,
    load_model_profiles as load_builtin_profiles,
)


AGNES_API_BASE = "https://apihub.agnes-ai.com"
AGNES_MODEL = "agnes-2.0-flash"

# 免费目录相关 env，供 any_llm_key_present 扫描
FREE_CATALOG_ENV_KEYS = (
    "OPENROUTER_API_KEY",
    "GROQ_API_KEY",
    "CEREBRAS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "CODESTRAL_API_KEY",
    "COHERE_API_KEY",
    "GITHUB_TOKEN",
    "HF_TOKEN",
    "HUGGINGFACE_API_KEY",
    "NVIDIA_API_KEY",
    "AI_GATEWAY_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "FIREWORKS_API_KEY",
    "SAMBANOVA_API_KEY",
    "HYPERBOLIC_API_KEY",
    "NOVITA_API_KEY",
)


def _custom_path() -> Any:
    return data_dir() / "custom_providers.json"


def load_custom_providers() -> list[dict[str, Any]]:
    """加载用户可视化接入的自定义模型。"""

    path = _custom_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("providers") or [])


def save_custom_providers(providers: list[dict[str, Any]]) -> None:
    """持久化自定义模型列表。"""

    path = _custom_path()
    path.write_text(
        json.dumps(
            {"providers": providers, "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def add_custom_provider(payload: dict[str, Any]) -> dict[str, Any]:
    """
    新增自定义 OpenAI 兼容模型。

    Args:
        payload: 含 label/api_base/api_key/model 等字段。

    Returns:
        新建的 provider 记录（api_key 脱敏）。
    """

    label = str(payload.get("label") or "").strip()
    api_base = str(payload.get("api_base") or "").strip().rstrip("/")
    api_key = str(payload.get("api_key") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not label or not api_base or not api_key or not model:
        raise ValueError("label / api_base / api_key / model 均为必填")
    if not re.match(r"^https?://", api_base):
        raise ValueError("api_base 必须以 http:// 或 https:// 开头")

    provider_id = str(payload.get("id") or f"custom-{uuid7().hex[:8]}")
    item = {
        "id": provider_id,
        "label": label,
        "description": str(payload.get("description") or "用户可视化接入的 OpenAI 兼容模型"),
        "provider": "custom",
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "required_env": [],
        "routes": {
            "tier_s": model,
            "tier_m": model,
            "tier_xs": model,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    providers = load_custom_providers()
    providers = [p for p in providers if p.get("id") != provider_id]
    providers.append(item)
    save_custom_providers(providers)
    return mask_provider(item)


def delete_custom_provider(provider_id: str) -> bool:
    """删除自定义模型。"""

    providers = load_custom_providers()
    next_list = [p for p in providers if p.get("id") != provider_id]
    if len(next_list) == len(providers):
        return False
    save_custom_providers(next_list)
    return True


def mask_provider(item: dict[str, Any]) -> dict[str, Any]:
    """对外返回时脱敏 api_key。"""

    out = dict(item)
    key = str(out.get("api_key") or "")
    if key:
        out["api_key_masked"] = (key[:4] + "…" + key[-4:]) if len(key) > 8 else "****"
    out.pop("api_key", None)
    out["has_api_key"] = bool(key)
    return out


@lru_cache
def load_free_llm_catalog() -> dict[str, Any]:
    """
    加载 cheahjs/free-llm-api-resources 精选目录。

    Returns:
        含 source / providers 的目录对象。
    """

    path = config_dir() / "free_llm_catalog.json"
    if not path.exists():
        return {"source": "", "providers": []}
    return json.loads(path.read_text(encoding="utf-8"))


def reload_free_llm_catalog() -> dict[str, Any]:
    """
    清除缓存并重新加载免费 LLM 目录（编辑 free_llm_catalog.json 后调用）。

    Returns:
        最新目录对象。
    """

    load_free_llm_catalog.cache_clear()
    return load_free_llm_catalog()


def _resolve_api_base_template(api_base: str) -> str:
    """替换 api_base 中的 {ENV_NAME} 占位符。"""

    def repl(match: re.Match[str]) -> str:
        env_name = match.group(1)
        return os.getenv(env_name, "").strip()

    return re.sub(r"\{([A-Z0-9_]+)\}", repl, api_base)


def _env_for_catalog_item(item: dict[str, Any]) -> str:
    """读取目录项对应的 API Key（含常见别名）。"""

    primary = str(item.get("api_key_env") or "").strip()
    aliases = {
        "GEMINI_API_KEY": ["GOOGLE_API_KEY"],
        "HF_TOKEN": ["HUGGINGFACE_API_KEY", "HUGGING_FACE_HUB_TOKEN"],
        "GITHUB_TOKEN": ["GH_TOKEN", "GITHUB_MODELS_TOKEN"],
    }
    candidates = [primary] + list(aliases.get(primary, []))
    for name in candidates:
        if name and os.getenv(name):
            return str(os.getenv(name) or "")
    return ""


def free_catalog_to_profiles() -> list[dict[str, Any]]:
    """将免费目录展开为可切换档案列表。"""

    catalog = load_free_llm_catalog()
    profiles: list[dict[str, Any]] = []
    for item in catalog.get("providers") or []:
        model = str(item.get("default_model") or (item.get("models") or [""])[0] or "").strip()
        if not model:
            continue
        profiles.append(
            {
                "id": item["id"],
                "label": item.get("label") or item["id"],
                "description": item.get("description") or "",
                "provider": "openai_compat",
                "group": item.get("group") or "free",
                "signup_url": item.get("signup_url") or "",
                "limits": item.get("limits") or "",
                "required_env": list(item.get("required_env") or [item.get("api_key_env")] or []),
                "api_base": item.get("api_base"),
                "api_key_env": item.get("api_key_env"),
                "models": list(item.get("models") or [model]),
                "default_model": model,
                "routes": {"tier_s": model, "tier_m": model, "tier_xs": model},
                "free_catalog": True,
                "source": catalog.get("source") or "",
            }
        )
    return profiles


def get_free_catalog_item(provider_id: str) -> Optional[dict[str, Any]]:
    """按 ID 取免费目录项。"""

    for item in load_free_llm_catalog().get("providers") or []:
        if item.get("id") == provider_id:
            return item
    return None


def load_all_profiles() -> list[dict[str, Any]]:
    """内置档案 + 免费目录 + 自定义模型档案。"""

    profiles = load_builtin_profiles()
    profiles.extend(free_catalog_to_profiles())
    for item in load_custom_providers():
        profiles.append(
            {
                "id": item["id"],
                "label": item["label"],
                "description": item.get("description") or "",
                "provider": "custom",
                "required_env": [],
                "routes": item.get("routes")
                or {
                    "tier_s": item["model"],
                    "tier_m": item["model"],
                    "tier_xs": item["model"],
                },
                "api_base": item.get("api_base"),
                "custom": True,
                "has_api_key": bool(item.get("api_key")),
                "group": "custom",
            }
        )
    return profiles


def get_profile(profile_id: Optional[str] = None) -> dict[str, Any]:
    """按 ID 取档案；默认当前激活。"""

    pid = profile_id or get_active_profile_id()
    for profile in load_all_profiles():
        if profile["id"] == pid:
            if profile.get("custom"):
                for raw in load_custom_providers():
                    if raw["id"] == pid:
                        return {**profile, "api_key": raw.get("api_key"), "api_base": raw.get("api_base")}
            if profile.get("free_catalog") or profile.get("provider") == "openai_compat":
                catalog_item = get_free_catalog_item(pid) or profile
                return {
                    **profile,
                    "api_base": catalog_item.get("api_base") or profile.get("api_base"),
                    "api_key_env": catalog_item.get("api_key_env") or profile.get("api_key_env"),
                    "api_key": _env_for_catalog_item({**profile, **catalog_item}),
                }
            return profile
    return {
        "id": "agnes-free",
        "provider": "agnes",
        "routes": {"tier_s": AGNES_MODEL, "tier_m": AGNES_MODEL, "tier_xs": AGNES_MODEL},
    }


def resolve_endpoint(model: str, profile: Optional[dict[str, Any]] = None) -> dict[str, str]:
    """
    解析实际调用端点。

    Returns:
        dict: api_base, api_key, model, provider
    """

    profile = profile or get_profile()
    provider = str(profile.get("provider") or "")

    if provider == "agnes" or model.startswith("agnes") or "agnes" in model:
        key = os.getenv("AGNES_API_KEY") or os.getenv("AGNES_TOKEN") or os.getenv("AGNES_API_TOKEN") or ""
        return {
            "provider": "agnes",
            "api_base": AGNES_API_BASE,
            "api_key": key,
            "model": AGNES_MODEL,
        }

    if provider == "custom" or profile.get("custom"):
        return {
            "provider": "custom",
            "api_base": str(profile.get("api_base") or "").rstrip("/"),
            "api_key": str(profile.get("api_key") or ""),
            "model": model,
        }

    if provider == "openai_compat" or profile.get("free_catalog"):
        raw_base = str(profile.get("api_base") or "").strip()
        api_base = _resolve_api_base_template(raw_base).rstrip("/")
        api_key = str(profile.get("api_key") or "") or _env_for_catalog_item(profile)
        if not api_base or "{" in api_base:
            raise RuntimeError(
                f"免费模型档案 {profile.get('id')} 的 api_base 未就绪（检查 CLOUDFLARE_ACCOUNT_ID 等占位环境变量）"
            )
        return {
            "provider": "openai_compat",
            "api_base": api_base,
            "api_key": api_key,
            "model": model,
        }

    if model.startswith("qwen/") or provider == "qwen":
        return {
            "provider": "qwen",
            "api_base": os.getenv("QWEN_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "model": model.split("/", 1)[-1],
        }
    if model.startswith("deepseek/") or provider == "deepseek":
        return {
            "provider": "deepseek",
            "api_base": os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1").rstrip("/"),
            "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "model": model.split("/", 1)[-1],
        }
    if model.startswith("openai/") or provider == "openai":
        return {
            "provider": "openai",
            "api_base": os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1").rstrip("/"),
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "model": model.split("/", 1)[-1] if "/" in model else model,
        }

    from app.settings import get_settings

    settings = get_settings()
    return {
        "provider": "litellm",
        "api_base": settings.litellm_base_url.rstrip("/"),
        "api_key": settings.litellm_master_key,
        "model": model,
    }


def any_llm_key_present() -> bool:
    """是否配置了任意可用模型密钥。"""

    if os.getenv("AGNES_API_KEY") or os.getenv("AGNES_TOKEN") or os.getenv("AGNES_API_TOKEN"):
        return True
    if any(os.getenv(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "QWEN_API_KEY", "DEEPSEEK_API_KEY")):
        return True
    if any(os.getenv(k) for k in FREE_CATALOG_ENV_KEYS):
        return True
    return any(bool(p.get("api_key")) for p in load_custom_providers())


def ensure_agnes_profile_file() -> None:
    """确保 Agnes 档案文件存在。"""

    path = config_dir() / "model_profiles" / "agnes-free.json"
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": "agnes-free",
                "label": "Agnes 免费 (2.0 Flash)",
                "description": "Sapiens Agnes-2.0-Flash，OpenAI 兼容，适合全链路控成本。",
                "provider": "agnes",
                "required_env": ["AGNES_API_KEY"],
                "routes": {
                    "tier_s": "agnes-2.0-flash",
                    "tier_m": "agnes-2.0-flash",
                    "tier_xs": "agnes-2.0-flash",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
