# -*- coding: utf-8 -*-
"""
LeadForge 多模态内容块。

作用: 统一节点输入/输出的文字、图片、视频结构，供工作流画布与重跑使用。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class MediaType(str, Enum):
    """媒体类型。"""

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"


class MediaBlock(BaseModel):
    """
    单条多模态内容。

    Attributes:
        type: text | image | video
        content: 文本正文；图片/视频可为说明文字
        url: 图片或视频可访问地址（本地 /media/... 或外链）
        mime: 可选 MIME
        name: 显示名
    """

    type: MediaType
    content: str = ""
    url: Optional[str] = None
    mime: Optional[str] = None
    name: Optional[str] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: Optional[str]) -> Optional[str]:
        """允许相对路径 /media/... 与 http(s) URL。"""

        if value is None or value == "":
            return None
        if value.startswith("/media/") or value.startswith("http://") or value.startswith("https://"):
            return value
        raise ValueError("url 必须是 /media/... 或 http(s) 链接")


def text_block(content: str, name: str = "text") -> dict[str, Any]:
    """快捷构造文本块。"""

    return MediaBlock(type=MediaType.TEXT, content=content or "", name=name).model_dump()


def image_block(url: str, content: str = "", name: str = "image") -> dict[str, Any]:
    """快捷构造图片块。"""

    return MediaBlock(type=MediaType.IMAGE, url=url, content=content, name=name).model_dump()


def video_block(url: str, content: str = "", name: str = "video") -> dict[str, Any]:
    """快捷构造视频块。"""

    return MediaBlock(type=MediaType.VIDEO, url=url, content=content, name=name).model_dump()


def blocks_to_prompt(blocks: list[dict[str, Any]]) -> str:
    """将多模态输入折叠为给 LLM 的文本提示（图/视频以 URL 引用）。"""

    parts: list[str] = []
    for idx, raw in enumerate(blocks or []):
        block = MediaBlock.model_validate(raw)
        if block.type == MediaType.TEXT:
            parts.append(f"[TEXT#{idx}] {block.content}")
        elif block.type == MediaType.IMAGE:
            parts.append(f"[IMAGE#{idx}] url={block.url or ''} note={block.content}")
        elif block.type == MediaType.VIDEO:
            parts.append(f"[VIDEO#{idx}] url={block.url or ''} note={block.content}")
    return "\n".join(parts) if parts else ""
