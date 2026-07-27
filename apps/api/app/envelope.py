# -*- coding: utf-8 -*-
"""
LeadForge 数据信封与 Trace 契约。

作用: 定义 Agent 间唯一合法的通信结构（Data Envelope），强制 TraceID 贯穿全链路。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field
from uuid6 import uuid7


class AgentName(str, Enum):
    """系统内全部 Agent 枚举。"""

    ORCHESTRATOR = "orchestrator"
    OPPORTUNITY = "opportunity"
    BUSINESS_MODEL = "business_model"
    DEV = "dev"
    DEPLOY = "deploy"
    MARKETING = "marketing"
    REDTEAM = "redteam"
    FLYWHEEL = "flywheel"


class EnvelopeStatus(str, Enum):
    """信封状态机。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    NEEDS_HITL = "needs_hitl"
    REJECTED_BY_REDTEAM = "rejected_by_redteam"
    APPROVED = "approved"
    REJECTED = "rejected"


class ModelRoute(str, Enum):
    """动态模型路由档位。"""

    TIER_S = "tier_s"
    TIER_M = "tier_m"
    TIER_XS = "tier_xs"


class EnvelopeMetadata(BaseModel):
    """信封元数据。"""

    theme_pack: str = "local-service-leadgen"
    model_route: ModelRoute = ModelRoute.TIER_M
    model_resolved: Optional[str] = None
    schema_ref: str = "schemas/envelope.v1.json"
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    skill_ids: list[str] = Field(default_factory=list)
    token_cost_est: float = 0.0


class EnvelopeError(BaseModel):
    """结构化错误。"""

    code: str
    message: str
    details: Optional[dict[str, Any]] = None


class DataEnvelope(BaseModel):
    """
    标准化数据信封。

    所有 Agent 输入/输出必须使用本结构；禁止以自由自然语言作为主契约。
    """

    envelope_version: str = "1.0"
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    agent: AgentName
    status: EnvelopeStatus
    metadata: EnvelopeMetadata
    payload: dict[str, Any] = Field(default_factory=dict)
    error: Optional[EnvelopeError] = None


def new_trace_id() -> str:
    """生成全局唯一 TraceID（UUIDv7）。"""

    return str(uuid7())


def new_span_id() -> str:
    """生成 SpanID。"""

    return uuid7().hex[:16]


def make_envelope(
    *,
    agent: AgentName,
    status: EnvelopeStatus,
    payload: dict[str, Any],
    trace_id: Optional[str] = None,
    parent_span_id: Optional[str] = None,
    theme_pack: str = "local-service-leadgen",
    model_route: ModelRoute = ModelRoute.TIER_M,
    skill_ids: Optional[list[str]] = None,
    error: Optional[EnvelopeError] = None,
) -> DataEnvelope:
    """
    构造合法数据信封。

    Args:
        agent: 产出方 Agent。
        status: 当前状态。
        payload: 已通过业务 Schema 校验的载荷。
        trace_id: 全局追踪 ID；空则新建。
        parent_span_id: 上游 span。
        theme_pack: 主题包 ID。
        model_route: 模型档位。
        skill_ids: 本步引用的 Skill。
        error: 失败时的结构化错误。

    Returns:
        DataEnvelope 实例。
    """

    return DataEnvelope(
        trace_id=trace_id or new_trace_id(),
        span_id=new_span_id(),
        parent_span_id=parent_span_id,
        agent=agent,
        status=status,
        metadata=EnvelopeMetadata(
            theme_pack=theme_pack,
            model_route=model_route,
            skill_ids=skill_ids or [],
        ),
        payload=payload,
        error=error,
    )
