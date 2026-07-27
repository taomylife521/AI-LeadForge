# -*- coding: utf-8 -*-
"""
LeadForge Harness Agent 适配层。

作用: 把「长期运行 / 计划-执行-校验」Harness 模式接到现有 Orchestrator + HITL，
      用于 PitchHub 全量爬取、主题提炼、商机工作流等长任务。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir
from app.tools.pitchhub_36kr import crawl_pitchhub_all
from app.tools.project_library import library_stats


def _jobs_dir() -> Path:
    path = data_dir() / "harness_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{job_id}.json"


def list_harness_jobs(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近 Harness 任务摘要。"""

    rows: list[dict[str, Any]] = []
    for path in sorted(_jobs_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(doc, dict):
            rows.append(
                {
                    "id": doc.get("id"),
                    "kind": doc.get("kind"),
                    "status": doc.get("status"),
                    "created_at": doc.get("created_at"),
                    "updated_at": doc.get("updated_at"),
                    "summary": doc.get("summary"),
                }
            )
        if len(rows) >= limit:
            break
    return rows


def get_harness_job(job_id: str) -> Optional[dict[str, Any]]:
    """读取任务详情。"""

    path = _job_path(job_id)
    if not path.exists():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _save_job(doc: dict[str, Any]) -> None:
    doc["updated_at"] = datetime.now(timezone.utc).isoformat()
    _job_path(str(doc["id"])).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


async def _run_pitchhub_crawl_job(job_id: str, *, max_items: int, reset: bool) -> None:
    """后台执行 PitchHub 分片爬取。"""

    doc = get_harness_job(job_id) or {"id": job_id}
    doc["status"] = "running"
    doc["phase"] = "crawl"
    _save_job(doc)
    try:
        result = await crawl_pitchhub_all(max_items=max_items, reset=reset)
        stats = library_stats()
        doc["status"] = "success"
        doc["phase"] = "done"
        doc["result"] = result
        doc["library_stats"] = stats
        doc["summary"] = (
            f"PitchHub 入库 +{result.get('upsert', {}).get('added', 0)} / "
            f"更新 {result.get('upsert', {}).get('updated', 0)} · 库总量 {stats.get('count')}"
        )
        _save_job(doc)
    except Exception as exc:  # noqa: BLE001
        doc["status"] = "failed"
        doc["error"] = str(exc)[:400]
        doc["summary"] = f"失败: {exc}"
        _save_job(doc)


def start_harness_job(
    *,
    kind: str = "pitchhub_crawl",
    max_items: int = 2000,
    reset: bool = False,
) -> dict[str, Any]:
    """
    启动 Harness 任务（当前支持 pitchhub_crawl）。

    Harness 模式映射到 LeadForge：
    - Planner: 选定任务种类与分片策略（行业×轮次）
    - Worker: 执行爬取 / 后续可挂工作流节点
    - Judge/HITL: 失败重试 + 控制台人工干预
    """

    kind = (kind or "pitchhub_crawl").strip()
    if kind not in {"pitchhub_crawl"}:
        raise ValueError(f"暂不支持的 harness kind: {kind}")

    job_id = uuid.uuid4().hex[:16]
    doc: dict[str, Any] = {
        "id": job_id,
        "kind": kind,
        "status": "queued",
        "phase": "plan",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "params": {"max_items": max_items, "reset": reset},
        "harness": {
            "pattern": "plan-worker-judge",
            "planner": "shard_by_industry_x_round",
            "worker": "pitchhub_gateway_crawler",
            "judge": "upsert_dedupe + checkpoint",
            "note": (
                "对齐 Cursor Harness：长任务用断点续跑、分片执行、结果校验；"
                "模型可热替换，编排层负责可靠性。"
            ),
        },
        "summary": "已排队",
    }
    _save_job(doc)
    asyncio.create_task(_run_pitchhub_crawl_job(job_id, max_items=max_items, reset=reset))
    return doc
