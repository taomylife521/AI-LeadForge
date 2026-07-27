# -*- coding: utf-8 -*-
"""
LeadForge 运行过程事件总线。

作用: 为可视化时间线提供实时步骤事件（SSE / 轮询）。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional


class ProgressBus:
    """进程内 Trace 事件总线。"""

    def __init__(self) -> None:
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._queues: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def emit(self, trace_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """写入事件并通知订阅者。"""

        payload = {
            **event,
            "trace_id": trace_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        async with self._lock:
            self._events.setdefault(trace_id, []).append(payload)
            for queue in self._queues.get(trace_id, []):
                await queue.put(payload)
        return payload

    def list_events(self, trace_id: str) -> list[dict[str, Any]]:
        """返回已缓存事件。"""

        return list(self._events.get(trace_id, []))

    async def subscribe(self, trace_id: str) -> asyncio.Queue:
        """订阅后续事件。"""

        queue: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._queues.setdefault(trace_id, []).append(queue)
            for existing in self._events.get(trace_id, []):
                await queue.put(existing)
        return queue

    async def unsubscribe(self, trace_id: str, queue: asyncio.Queue) -> None:
        """取消订阅。"""

        async with self._lock:
            queues = self._queues.get(trace_id) or []
            if queue in queues:
                queues.remove(queue)


progress_bus = ProgressBus()
