# -*- coding: utf-8 -*-
"""
工作流运行控制：停止 / 暂停 / 恢复。

作用: 为异步编排提供可检查点的取消与暂停信号（进程内）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class RunCancelled(Exception):
    """运行被用户停止。"""


class RunPausedWait(Exception):
    """内部：暂停等待（一般不向外抛出）。"""


@dataclass
class RunFlags:
    """单条 Trace 的控制标志。"""

    cancel: bool = False
    pause: bool = False
    status: str = "running"  # running|paused|cancelled|finished
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    note: str = ""


class RunControl:
    """
    进程内运行控制平面。

    编排器在关键阶段调用 checkpoint()；UI 通过 API 发 stop/pause/resume。
    """

    def __init__(self) -> None:
        self._flags: dict[str, RunFlags] = {}
        self._lock = asyncio.Lock()
        self._resume_events: dict[str, asyncio.Event] = {}

    def ensure(self, trace_id: str) -> RunFlags:
        """确保标志存在。"""

        if trace_id not in self._flags:
            self._flags[trace_id] = RunFlags()
            self._resume_events[trace_id] = asyncio.Event()
            self._resume_events[trace_id].set()
        return self._flags[trace_id]

    def snapshot(self, trace_id: str) -> dict[str, Any]:
        """读取控制状态。"""

        flags = self.ensure(trace_id)
        return {
            "trace_id": trace_id,
            "cancel": flags.cancel,
            "pause": flags.pause,
            "status": flags.status,
            "updated_at": flags.updated_at,
            "note": flags.note,
        }

    async def request_stop(self, trace_id: str, note: str = "") -> dict[str, Any]:
        """请求停止（不可恢复为继续同一 job，需 restart）。"""

        async with self._lock:
            flags = self.ensure(trace_id)
            flags.cancel = True
            flags.pause = False
            flags.status = "cancelled"
            flags.note = note or "user_stop"
            flags.updated_at = datetime.now(timezone.utc).isoformat()
            ev = self._resume_events.setdefault(trace_id, asyncio.Event())
            ev.set()
        return self.snapshot(trace_id)

    async def request_pause(self, trace_id: str, note: str = "") -> dict[str, Any]:
        """请求暂停，下一检查点阻塞直至 resume。"""

        async with self._lock:
            flags = self.ensure(trace_id)
            if flags.cancel:
                return self.snapshot(trace_id)
            flags.pause = True
            flags.status = "paused"
            flags.note = note or "user_pause"
            flags.updated_at = datetime.now(timezone.utc).isoformat()
            ev = self._resume_events.setdefault(trace_id, asyncio.Event())
            ev.clear()
        return self.snapshot(trace_id)

    async def request_resume(self, trace_id: str, note: str = "") -> dict[str, Any]:
        """恢复暂停中的运行。"""

        async with self._lock:
            flags = self.ensure(trace_id)
            if flags.cancel:
                return self.snapshot(trace_id)
            flags.pause = False
            flags.status = "running"
            flags.note = note or "user_resume"
            flags.updated_at = datetime.now(timezone.utc).isoformat()
            ev = self._resume_events.setdefault(trace_id, asyncio.Event())
            ev.set()
        return self.snapshot(trace_id)

    def mark_finished(self, trace_id: str) -> None:
        """运行正常结束。"""

        flags = self.ensure(trace_id)
        if not flags.cancel:
            flags.status = "finished"
            flags.pause = False
            flags.updated_at = datetime.now(timezone.utc).isoformat()

    async def checkpoint(self, trace_id: str, *, phase: str = "") -> None:
        """
        阶段检查点：若已 stop 则抛 RunCancelled；若 pause 则等待 resume。

        Args:
            trace_id: Trace。
            phase: 当前阶段名（用于日志）。

        Raises:
            RunCancelled: 用户请求停止。
        """

        flags = self.ensure(trace_id)
        if flags.cancel:
            raise RunCancelled(f"run cancelled at {phase or 'checkpoint'}")
        if flags.pause:
            ev = self._resume_events.setdefault(trace_id, asyncio.Event())
            # 等待恢复；恢复后再次检查 cancel
            await ev.wait()
            flags = self.ensure(trace_id)
            if flags.cancel:
                raise RunCancelled(f"run cancelled while paused at {phase or 'checkpoint'}")


run_control = RunControl()
