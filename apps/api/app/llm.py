# -*- coding: utf-8 -*-
"""
LeadForge LLM 路由客户端。

作用: 按全局档案或「单 Agent 绑定」解析模型；直连 Agnes / 自定义 / LiteLLM。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import httpx

from app.envelope import ModelRoute
from app.providers import any_llm_key_present, get_profile, resolve_endpoint
from app.settings import get_active_routes, get_settings


def _extract_json(text: str) -> dict[str, Any]:
    """
    从模型输出中提取 JSON 对象。

    兼容 Markdown 围栏、前后杂质、尾逗号，以及对截断 JSON 做一次括号闭合修复。
    """

    text = (text or "").strip()
    if not text:
        raise ValueError("模型返回空内容")

    candidates: list[str] = [text]
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    if start >= 0:
        # 截断常见：只有开头没有完整结尾
        candidates.append(text[start:])

    errors: list[str] = []
    for raw in candidates:
        for variant in (raw, re.sub(r",\s*([}\]])", r"\1", raw), _repair_truncated_json(raw)):
            if not variant:
                continue
            try:
                data = json.loads(variant)
                if isinstance(data, dict):
                    return data
                errors.append(f"JSON 根类型非 object: {type(data)}")
            except json.JSONDecodeError as exc:
                errors.append(str(exc))
    raise ValueError(f"无法解析 JSON: {errors[:2]} | preview={text[:200]}")


def _repair_truncated_json(raw: str) -> str:
    """
    尝试闭合被截断的 JSON（未闭合引号/括号）。

    仅用于抢救部分字段；失败则返回空串。
    """

    s = (raw or "").strip()
    if not s or not s.startswith("{"):
        return ""
    # 去掉最后一个残缺键值（常见于 "key": "unterminated...）
    if s.count('"') % 2 == 1:
        # 闭合悬空字符串
        s = s + '"'
    # 去掉尾部残缺逗号后的半截
    s = re.sub(r",\s*\"[^\"]*$", "", s)
    s = re.sub(r",\s*$", "", s)
    open_curly = s.count("{") - s.count("}")
    open_square = s.count("[") - s.count("]")
    if open_curly < 0 or open_square < 0 or open_curly > 40 or open_square > 40:
        return ""
    s += "]" * max(0, open_square)
    s += "}" * max(0, open_curly)
    return s


class LLMClient:
    """动态模型路由客户端。"""

    def __init__(self) -> None:
        self.settings = get_settings()

    def resolve_model(self, route: ModelRoute) -> str:
        """将逻辑档位解析为物理模型名。"""

        routes = get_active_routes()
        return routes.get(route.value, route.value)

    def _resolve_from_binding(
        self,
        route: ModelRoute,
        model_binding: Optional[dict[str, Any]],
    ) -> tuple[str, dict[str, Any]]:
        """
        按 Agent 模型绑定解析端点。

        mode:
          - route: 用全局档案的 tier 映射
          - profile: 临时使用指定 profile_id 的路由
          - explicit: 直连 api_base + model + api_key/api_key_env
        """

        binding = model_binding or {}
        mode = binding.get("mode") or "route"

        if mode == "explicit":
            api_key = str(binding.get("api_key") or "")
            env_name = binding.get("api_key_env")
            if not api_key and env_name:
                api_key = os.getenv(str(env_name), "")
            api_base = str(binding.get("api_base") or "https://apihub.agnes-ai.com").rstrip("/")
            model = str(binding.get("model") or "agnes-2.0-flash")
            provider = str(binding.get("provider") or "custom")
            return model, {
                "provider": provider,
                "api_base": api_base,
                "api_key": api_key,
                "model": model,
            }

        if mode == "profile" and binding.get("profile_id"):
            profile = get_profile(str(binding["profile_id"]))
            route_name = binding.get("route") or route.value
            routes = profile.get("routes") or {}
            model = routes.get(route_name) or routes.get("tier_m") or route_name
            endpoint = resolve_endpoint(str(model), profile)
            return endpoint["model"], endpoint

        # route mode（可覆盖 route）
        route_name = binding.get("route") or route.value
        try:
            resolved_route = ModelRoute(route_name)
        except ValueError:
            resolved_route = route
        # 若绑定指定了 profile_id 但仍用 route 模式，优先该档案的 routes
        if binding.get("profile_id"):
            profile = get_profile(str(binding["profile_id"]))
            model = (profile.get("routes") or {}).get(resolved_route.value) or self.resolve_model(resolved_route)
            endpoint = resolve_endpoint(str(model), profile)
            return endpoint["model"], endpoint

        model = self.resolve_model(resolved_route)
        profile = get_profile()
        endpoint = resolve_endpoint(model, profile)
        return endpoint["model"], endpoint

    async def complete_json(
        self,
        *,
        route: ModelRoute,
        system: str,
        user: str,
        mock_payload: Optional[dict[str, Any]] = None,
        model_binding: Optional[dict[str, Any]] = None,
        allow_mock: bool = True,
        temperature: float = 0.3,
    ) -> tuple[dict[str, Any], str, bool]:
        """
        请求 JSON 对象响应。

        Args:
            model_binding: 可选的单 Agent 模型绑定覆盖。
            allow_mock: False 时禁止 MOCK_LLM / 无 Key / 降级假数据，失败直接抛错。
            temperature: 采样温度；推荐换批时应提高以增加多样性。

        Returns:
            (payload, model_name, used_mock)
        """

        model, endpoint = self._resolve_from_binding(route, model_binding)
        if self.settings.mock_llm:
            if not allow_mock:
                raise RuntimeError("当前 MOCK_LLM=true，无法获取真实模型推荐。请关闭 MOCK_LLM 并配置 API Key。")
            return mock_payload or {"note": "mock"}, model, True
        if not endpoint.get("api_key") and not any_llm_key_present():
            if not allow_mock:
                raise RuntimeError("未检测到可用 API Key，无法调用真实模型。")
            return mock_payload or {"note": "mock-no-key"}, model, True
        if not endpoint.get("api_key") and endpoint.get("provider") != "ollama":
            # 尝试回退全局解析
            profile = get_profile()
            endpoint = resolve_endpoint(model, profile)
            if not endpoint.get("api_key") and endpoint.get("provider") != "ollama":
                if not allow_mock:
                    raise RuntimeError("当前模型档案缺少 API Key，无法调用真实模型。")
                return mock_payload or {"note": "mock-no-key"}, model, True

        api_base = endpoint["api_base"].rstrip("/")
        if api_base.endswith("/v1"):
            url = f"{api_base}/chat/completions"
        else:
            url = f"{api_base}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {endpoint['api_key']}",
            "Content-Type": "application/json",
        }
        temperature = max(0.0, min(float(temperature), 1.5))
        body: dict[str, Any] = {
            "model": endpoint["model"],
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": user + "\n\n请仅输出合法 JSON 对象，不要 Markdown 代码块。",
                },
            ],
            "temperature": temperature,
            "max_tokens": 2048,
        }
        if endpoint["provider"] in {
            "openai",
            "openai_compat",
            "litellm",
            "qwen",
            "deepseek",
            "custom",
            "agnes",
        }:
            body["response_format"] = {"type": "json_object"}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code >= 400 and "response_format" in body:
                    body.pop("response_format", None)
                    resp = await client.post(url, headers=headers, json=body)
                if resp.status_code >= 400:
                    if not allow_mock:
                        raise RuntimeError(
                            f"模型调用失败 HTTP {resp.status_code}: {resp.text[:400]}"
                        )
                    fallback = {
                        **(mock_payload or {}),
                        "degraded": True,
                        "provider_error": resp.text[:500],
                        "http_status": resp.status_code,
                    }
                    return fallback, endpoint["model"], True
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return _extract_json(content), endpoint["model"], False
        except Exception as exc:  # noqa: BLE001
            if not allow_mock:
                raise
            fallback = {**(mock_payload or {}), "degraded": True, "error": str(exc)}
            return fallback, endpoint["model"], True
