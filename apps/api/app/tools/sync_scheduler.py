# -*- coding: utf-8 -*-
"""
创投/GitHub/热搜增量同步调度。

作用: 定时或手动增量抓取 36氪、创业邦、独立开发、GitHub、平台热搜，并写入同步日志。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

from app.tools.hotspot_sources import github_search_authenticated
from app.tools.hotspot_warehouse import upsert_hotspots, warm_hotspot_warehouse
from app.tools.sync_logs import (
    finish_sync_run,
    list_sync_logs,
    load_scheduler_config,
    save_scheduler_config,
    start_sync_run,
)

_scheduler_task: Optional[asyncio.Task] = None
_running_lock = asyncio.Lock()
_last_schedule_at: str = ""


def github_token_status() -> dict[str, Any]:
    """
    GitHub Token 配置状态（不含明文）。

    Returns:
        {configured, env_name, hint}
    """

    configured = github_search_authenticated()
    env_name = "GITHUB_TOKEN" if os.getenv("GITHUB_TOKEN") else ("GH_TOKEN" if os.getenv("GH_TOKEN") else "GITHUB_TOKEN")
    return {
        "configured": configured,
        "env_name": env_name,
        "hint": (
            "已配置，Search API 约 30 次/分钟"
            if configured
            else "未配置：匿名限额极低，请在系统设置粘贴 github.com/settings/tokens 生成的 Token（勾选 public_repo）"
        ),
    }


async def run_incremental_sync(
    *,
    trigger: str = "manual",
    sources: Optional[list[str]] = None,
    include_github: bool = True,
    include_newsnow: bool = True,
) -> dict[str, Any]:
    """
    执行一轮增量同步并写日志。

    Args:
        trigger: manual | schedule | startup。
        sources: 指定源；空则读配置。
        include_github: 是否抓 GitHub（需 Token 更稳）。
        include_newsnow: 是否同步平台热搜。

    Returns:
        完成的 run 日志 + 摘要。
    """

    cfg = load_scheduler_config()
    wanted = list(sources or cfg.get("sources") or ["36kr", "cyzone", "cid_indie", "github", "newsnow"])
    wanted = [str(x).strip().lower() for x in wanted if str(x).strip()]
    if not include_github:
        wanted = [x for x in wanted if x != "github"]
    if not include_newsnow:
        wanted = [x for x in wanted if x not in ("newsnow", "trendradar", "hotspots")]

    async with _running_lock:
        run = start_sync_run(kind="vc_incremental", trigger=trigger, sources=wanted)
        counts: dict[str, Any] = {}
        errors: list[str] = []
        added = 0
        updated = 0

        try:
            # —— 创投：36氪 RSS / PitchHub / 创业邦 / 独立开发
            if any(x in wanted for x in ("36kr", "cyzone", "cid_indie", "pitchhub", "vc")):
                try:
                    from app.tools.hotspot_lanes import collect_vc_hotspot_items

                    vc_rows = await collect_vc_hotspot_items(limit=36, industry="")
                    stats = upsert_hotspots(vc_rows, batch_source="vc_sync") if vc_rows else {"added": 0, "updated": 0, "total": 0}
                    counts["vc"] = len(vc_rows)
                    counts["vc_added"] = int(stats.get("added") or 0)
                    counts["vc_updated"] = int(stats.get("updated") or 0)
                    added += int(stats.get("added") or 0)
                    updated += int(stats.get("updated") or 0)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"vc:{exc}")

            # —— GitHub（Token 优先）
            if "github" in wanted:
                try:
                    from app.tools.hotspot_lanes import collect_github_hotspot_items

                    gh_rows = await collect_github_hotspot_items(
                        topic="AI SaaS agent",
                        industry="",
                        limit=24,
                    )
                    stats = upsert_hotspots(gh_rows, batch_source="github_sync") if gh_rows else {"added": 0, "updated": 0}
                    counts["github"] = len(gh_rows)
                    counts["github_authenticated"] = github_search_authenticated()
                    counts["github_added"] = int(stats.get("added") or 0)
                    added += int(stats.get("added") or 0)
                    updated += int(stats.get("updated") or 0)
                    if not github_search_authenticated():
                        errors.append("github:未配置 GITHUB_TOKEN，Search 可能被限流")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"github:{exc}")

            # —— 平台热搜 newsnow
            if any(x in wanted for x in ("newsnow", "trendradar", "hotspots")):
                try:
                    warm = await warm_hotspot_warehouse(limit_per_source=30, include_trendradar=True)
                    ups = warm.get("upsert") if isinstance(warm.get("upsert"), dict) else {}
                    counts["newsnow_total"] = int(ups.get("total") or 0)
                    counts["newsnow_added"] = int(ups.get("added") or 0)
                    added += int(ups.get("added") or 0)
                    updated += int(ups.get("updated") or 0)
                    if warm.get("errors"):
                        for e in (warm.get("errors") or [])[:5]:
                            errors.append(str(e)[:160])
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"newsnow:{exc}")

            status = "ok"
            if errors and (added or counts):
                status = "partial"
            elif errors and not counts:
                status = "error"

            finished = finish_sync_run(
                run,
                status=status,
                counts=counts,
                added=added,
                updated=updated,
                errors=errors,
                note="增量同步完成" if status == "ok" else ("部分成功" if status == "partial" else "同步失败"),
            )
            return {"ok": status != "error", "run": finished, "github": github_token_status()}
        except Exception as exc:  # noqa: BLE001
            finished = finish_sync_run(
                run,
                status="error",
                counts=counts,
                added=added,
                updated=updated,
                errors=errors + [str(exc)[:200]],
                note="同步异常中断",
            )
            return {"ok": False, "run": finished, "github": github_token_status()}


async def scheduler_loop() -> None:
    """后台循环：按配置间隔执行增量同步。"""

    global _last_schedule_at
    # 启动后稍等，避免与冷启动 warm 抢带宽
    await asyncio.sleep(45)
    while True:
        cfg = load_scheduler_config()
        interval_min = int(cfg.get("interval_minutes") or 30)
        interval_sec = max(300, interval_min * 60)
        if cfg.get("enabled", True):
            try:
                result = await run_incremental_sync(trigger="schedule")
                _last_schedule_at = str((result.get("run") or {}).get("finished_at") or "")
                print(
                    f"[leadforge] sync schedule: status={(result.get('run') or {}).get('status')} "
                    f"added={(result.get('run') or {}).get('added')} "
                    f"duration_ms={(result.get('run') or {}).get('duration_ms')}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[leadforge] sync schedule error: {exc}")
        await asyncio.sleep(interval_sec)


def start_background_scheduler() -> None:
    """在当前事件循环启动调度任务（幂等）。"""

    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(scheduler_loop())


def scheduler_status() -> dict[str, Any]:
    """调度器运行状态。"""

    cfg = load_scheduler_config()
    logs = list_sync_logs(limit=5)
    running = bool(_scheduler_task and not _scheduler_task.done())
    return {
        "ok": True,
        "scheduler_running": running,
        "last_schedule_at": _last_schedule_at,
        "config": cfg,
        "github": github_token_status(),
        "recent_logs": logs.get("items") or [],
    }


# 供 API 复用
__all__ = [
    "github_token_status",
    "run_incremental_sync",
    "scheduler_loop",
    "start_background_scheduler",
    "scheduler_status",
    "load_scheduler_config",
    "save_scheduler_config",
    "list_sync_logs",
]
