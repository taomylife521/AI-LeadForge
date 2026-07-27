# -*- coding: utf-8 -*-
"""
节点运行可观测性。

作用: 推送节点日志、LLM 提示词/模型/Skill、卡住诊断到 ProgressBus，供控制台实时查看。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Optional

from app.progress import progress_bus

# 默认：某阶段超过该秒数无新事件 → 判定卡住并汇总原因
DEFAULT_STUCK_SECONDS = 90.0


class NodeTracer:
    """
    单节点/单次运行的观测上下文。

    记录阶段时间线，并向 progress_bus 推送可展示事件。
    """

    def __init__(
        self,
        trace_id: str,
        *,
        agent: str,
        node_id: str = "",
        stuck_after_sec: float = DEFAULT_STUCK_SECONDS,
    ) -> None:
        self.trace_id = trace_id
        self.agent = agent
        self.node_id = node_id or agent
        self.stuck_after_sec = stuck_after_sec
        self.stages: list[dict[str, Any]] = []
        self._last_emit_mono = time.monotonic()
        self._watch_task: Optional[asyncio.Task] = None
        self._closed = False

    def _touch(self) -> None:
        self._last_emit_mono = time.monotonic()

    async def log(
        self,
        message: str,
        *,
        stage: str = "",
        status: str = "running",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        写入节点执行日志。

        Args:
            message: 短标题。
            stage: 阶段 id（如 hotspots / llm_refine）。
            status: running|success|failed|stuck。
            detail: 附加结构化信息。
        """

        entry = {
            "stage": stage or message,
            "message": message,
            "status": status,
            "at": datetime.now(timezone.utc).isoformat(),
            "detail": detail or {},
        }
        self.stages.append(entry)
        self._touch()
        await progress_bus.emit(
            self.trace_id,
            {
                "type": "node_log",
                "agent": self.agent,
                "node_id": self.node_id,
                "title": message,
                "summary": message,
                "status": status,
                "stage": entry["stage"],
                "detail": detail or {},
                "stages": self.stages[-20:],
            },
        )

    async def llm_call(
        self,
        *,
        model: str,
        route: str,
        skills: list[str],
        system: str,
        user: str,
        status: str = "running",
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        推送即将/已经发给模型的提示词与绑定信息。

        Args:
            model: 物理模型名。
            route: 逻辑档位。
            skills: 绑定 Skill id 列表。
            system / user: 完整提示词（UI 可折叠查看）。
            status: running|success|failed。
            extra: 附加字段（如 temperature、error）。
        """

        self._touch()
        payload = {
            "type": "llm_call",
            "agent": self.agent,
            "node_id": self.node_id,
            "title": f"调用模型 {model or route}",
            "summary": f"model={model}; skills={','.join(skills[:8])}",
            "status": status,
            "model": model,
            "route": route,
            "skills": skills,
            "prompt": {
                "system": system,
                "user": user,
                "system_chars": len(system or ""),
                "user_chars": len(user or ""),
            },
            "extra": extra or {},
            "stages": self.stages[-12:],
        }
        await progress_bus.emit(self.trace_id, payload)

    async def stuck(self, reason: str, *, hints: Optional[list[str]] = None) -> None:
        """推送卡住诊断摘要。"""

        last = self.stages[-1] if self.stages else {}
        await progress_bus.emit(
            self.trace_id,
            {
                "type": "stuck",
                "agent": self.agent,
                "node_id": self.node_id,
                "title": "节点疑似卡住",
                "summary": reason,
                "status": "stuck",
                "diagnosis": {
                    "reason": reason,
                    "last_stage": last.get("stage"),
                    "last_message": last.get("message"),
                    "elapsed_idle_sec": round(time.monotonic() - self._last_emit_mono, 1),
                    "hints": hints
                    or [
                        "查看本节点 llm_call 事件中的提示词与模型",
                        "检查网络/创业邦/GitHub 是否超时",
                        "检查模型 Key 与 MOCK_LLM",
                    ],
                    "timeline": self.stages[-15:],
                },
            },
        )

    async def start_watchdog(self) -> None:
        """启动后台卡住监视（无新日志超过阈值则诊断）。"""

        if self._watch_task and not self._watch_task.done():
            return

        async def _loop() -> None:
            while not self._closed:
                await asyncio.sleep(15.0)
                if self._closed:
                    return
                idle = time.monotonic() - self._last_emit_mono
                if idle >= self.stuck_after_sec:
                    await self.stuck(
                        f"超过 {int(self.stuck_after_sec)}s 无新进度"
                        + (f"（停在: {self.stages[-1].get('message')}）" if self.stages else ""),
                        hints=[
                            "商机研究含多源抓取+多次 LLM，可能仍在跑；若持续无日志请重试",
                            "打开执行日志查看最后 stage / llm_call",
                        ],
                    )
                    # 避免刷屏：再等一个周期
                    self._touch()

        self._watch_task = asyncio.create_task(_loop())

    async def close(self, *, status: str = "success", message: str = "节点结束") -> None:
        """结束监视并写收尾日志。"""

        self._closed = True
        if self._watch_task and not self._watch_task.done():
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
        await self.log(message, stage="done", status=status)


def normalize_mcp_entries(raw: Any) -> list[dict[str, Any]]:
    """
    将 mcp 绑定规范为 [{name, enabled}, ...]。

    YAML 里常写成字符串列表（如 fetch），运行时 JSON 则为对象；统一兼容。
    """

    if not raw:
        return []
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        raw = [raw]
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                out.append({"name": name, "enabled": True})
            continue
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            out.append({**item, "name": name, "enabled": bool(item.get("enabled", True))})
    return out
