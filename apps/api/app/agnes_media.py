# -*- coding: utf-8 -*-
"""
LeadForge Agnes 多模态生成（图片）。

作用: 调用 Agnes Image API 为营销/开发节点产出可视化素材；失败时优雅降级。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx


AGNES_API_BASE = os.getenv("AGNES_API_BASE", "https://apihub.agnes-ai.com").rstrip("/")


def _api_key() -> str:
    return os.getenv("AGNES_API_KEY") or os.getenv("AGNES_TOKEN") or os.getenv("AGNES_API_TOKEN") or ""


async def generate_image(prompt: str, *, size: str = "1024x768") -> Optional[str]:
    """
    文生图，返回图片 URL；失败返回 None。

    Args:
        prompt: 英文或中文提示（Agnes 对英文更稳，调用方可先翻译）。
        size: 输出尺寸。
    """

    key = _api_key()
    if not key or not prompt.strip():
        return None
    url = f"{AGNES_API_BASE}/v1/images/generations"
    body: dict[str, Any] = {
        "model": "agnes-image-2.1-flash",
        "prompt": prompt,
        "size": size,
        "extra_body": {"response_format": "url"},
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
            # 兼容多种返回形状
            if isinstance(data.get("data"), list) and data["data"]:
                item = data["data"][0]
                return item.get("url") or item.get("b64_json")
            return data.get("url") or data.get("image_url")
    except Exception:  # noqa: BLE001
        return None
