# -*- coding: utf-8 -*-
"""
增量抓取同步日志。

作用: 记录定时/手动同步的耗时、数量、错误，供控制台查看。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir

_MAX_LOGS = 120


def _logs_path() -> Path:
    return data_dir() / "sync_logs.json"


def _config_path() -> Path:
    return data_dir() / "sync_scheduler.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_doc() -> dict[str, Any]:
    path = _logs_path()
    if not path.exists():
        return {"runs": [], "updated_at": ""}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("runs"), list):
            return doc
    except Exception:  # noqa: BLE001
        pass
    return {"runs": [], "updated_at": ""}


def _write_doc(doc: dict[str, Any]) -> None:
    path = _logs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = _now()
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def load_scheduler_config() -> dict[str, Any]:
    """
    读取定时同步配置。

    Returns:
        {enabled, interval_minutes, sources, updated_at}
    """

    defaults = {
        "enabled": True,
        "interval_minutes": 30,
        "sources": ["36kr", "cyzone", "cid_indie", "github", "newsnow"],
        "updated_at": "",
    }
    path = _config_path()
    if not path.exists():
        return dict(defaults)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            return dict(defaults)
        out = dict(defaults)
        out.update({k: doc[k] for k in defaults if k in doc})
        out["enabled"] = bool(out.get("enabled", True))
        try:
            out["interval_minutes"] = max(5, min(int(out.get("interval_minutes") or 30), 24 * 60))
        except (TypeError, ValueError):
            out["interval_minutes"] = 30
        src = out.get("sources")
        if isinstance(src, list) and src:
            out["sources"] = [str(x) for x in src]
        return out
    except Exception:  # noqa: BLE001
        return dict(defaults)


def save_scheduler_config(
    *,
    enabled: Optional[bool] = None,
    interval_minutes: Optional[int] = None,
    sources: Optional[list[str]] = None,
) -> dict[str, Any]:
    """更新定时同步配置并落盘。"""

    cfg = load_scheduler_config()
    if enabled is not None:
        cfg["enabled"] = bool(enabled)
    if interval_minutes is not None:
        cfg["interval_minutes"] = max(5, min(int(interval_minutes), 24 * 60))
    if sources is not None:
        cleaned = [str(x).strip() for x in sources if str(x).strip()]
        if cleaned:
            cfg["sources"] = cleaned
    cfg["updated_at"] = _now()
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def start_sync_run(*, kind: str, trigger: str = "manual", sources: Optional[list[str]] = None) -> dict[str, Any]:
    """
    开始一条同步日志（running）。

    Args:
        kind: 任务类型，如 vc_incremental / github / full。
        trigger: manual | schedule | startup。
        sources: 本次要抓的源。

    Returns:
        新建 run 文档。
    """

    run = {
        "id": str(uuid.uuid4()),
        "kind": (kind or "sync").strip() or "sync",
        "trigger": (trigger or "manual").strip() or "manual",
        "sources": list(sources or []),
        "status": "running",
        "started_at": _now(),
        "finished_at": "",
        "duration_ms": 0,
        "counts": {},
        "added": 0,
        "updated": 0,
        "errors": [],
        "note": "",
        "_t0": time.perf_counter(),
    }
    doc = _read_doc()
    runs = list(doc.get("runs") or [])
    # 持久化时去掉内部计时字段的副本
    persist = {k: v for k, v in run.items() if not str(k).startswith("_")}
    runs.insert(0, persist)
    doc["runs"] = runs[:_MAX_LOGS]
    _write_doc(doc)
    return run


def finish_sync_run(
    run: dict[str, Any],
    *,
    status: str = "ok",
    counts: Optional[dict[str, Any]] = None,
    added: int = 0,
    updated: int = 0,
    errors: Optional[list[str]] = None,
    note: str = "",
) -> dict[str, Any]:
    """
    结束同步并写回日志。

    Args:
        run: start_sync_run 返回值（可含 _t0）。
        status: ok | error | partial。
        counts: 各源数量。
        added/updated: 入库增量。
        errors: 错误摘要列表。
        note: 备注。

    Returns:
        完成后的 run（无内部字段）。
    """

    t0 = float(run.pop("_t0", 0) or 0)
    duration_ms = int((time.perf_counter() - t0) * 1000) if t0 else 0
    run_id = str(run.get("id") or "")
    finished = {
        **{k: v for k, v in run.items() if not str(k).startswith("_")},
        "status": status,
        "finished_at": _now(),
        "duration_ms": duration_ms,
        "counts": dict(counts or {}),
        "added": int(added or 0),
        "updated": int(updated or 0),
        "errors": [str(e)[:200] for e in (errors or [])][:20],
        "note": (note or "")[:500],
    }

    doc = _read_doc()
    runs = list(doc.get("runs") or [])
    found = False
    for i, row in enumerate(runs):
        if str(row.get("id") or "") == run_id:
            runs[i] = finished
            found = True
            break
    if not found:
        runs.insert(0, finished)
    doc["runs"] = runs[:_MAX_LOGS]
    _write_doc(doc)
    return finished


def list_sync_logs(*, limit: int = 40, kind: str = "") -> dict[str, Any]:
    """
    列出同步日志（新→旧）。

    Args:
        limit: 条数。
        kind: 可选按 kind 过滤。

    Returns:
        {ok, items, count, config}
    """

    doc = _read_doc()
    runs = list(doc.get("runs") or [])
    kind_l = (kind or "").strip().lower()
    if kind_l:
        runs = [r for r in runs if str(r.get("kind") or "").lower() == kind_l]
    lim = max(1, min(int(limit or 40), 100))
    items = runs[:lim]
    return {
        "ok": True,
        "items": items,
        "count": len(runs),
        "shown": len(items),
        "config": load_scheduler_config(),
        "updated_at": doc.get("updated_at") or "",
    }
