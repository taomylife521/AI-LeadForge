# -*- coding: utf-8 -*-
"""
增量抓取同步日志。

作用: 记录定时/手动同步的耗时、数量、错误，供控制台查看。
说明: Serverless（如 Vercel）下 /tmp 不跨实例共享，因此同时维护进程内环形缓冲，
      避免「刚同步完刷新却看不到 / 一直停在 running」的体验问题。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir

_MAX_LOGS = 120
# 进程内最近日志（同实例热刷新可读；跨实例仍可能不一致）
_MEMORY_RUNS: list[dict[str, Any]] = []
# running 超过该秒数仍未 finish → 视为被 Serverless 打断
_STALE_RUNNING_SEC = 120


def _logs_path() -> Path:
    return data_dir() / "sync_logs.json"


def _config_path() -> Path:
    return data_dir() / "sync_scheduler.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> Optional[float]:
    """把 ISO 时间解析为 epoch 秒；失败返回 None。"""

    raw = (ts or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    """去掉内部字段后的可序列化 run。"""

    return {k: v for k, v in run.items() if not str(k).startswith("_")}


def _remember(run: dict[str, Any]) -> None:
    """写入进程内存环形缓冲（按 id 覆盖）。"""

    global _MEMORY_RUNS
    pub = _public_run(run)
    rid = str(pub.get("id") or "")
    rows = [r for r in _MEMORY_RUNS if str(r.get("id") or "") != rid]
    rows.insert(0, pub)
    _MEMORY_RUNS = rows[:_MAX_LOGS]


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
        新建 run 文档（含内部 _t0）。
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
        "note": "同步进行中…",
        "_t0": time.perf_counter(),
    }
    doc = _read_doc()
    runs = list(doc.get("runs") or [])
    persist = _public_run(run)
    runs.insert(0, persist)
    doc["runs"] = runs[:_MAX_LOGS]
    _write_doc(doc)
    _remember(persist)
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
        **_public_run(run),
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
    _remember(finished)
    return finished


def _mark_stale_running(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    将超时仍为 running 的条目标为 interrupted（Serverless 被杀常见）。

    Args:
        runs: 原始日志列表。

    Returns:
        处理后的列表（原地副本）。
    """

    now = time.time()
    out: list[dict[str, Any]] = []
    dirty = False
    for row in runs:
        item = dict(row) if isinstance(row, dict) else {}
        if str(item.get("status") or "") == "running":
            started = _parse_iso(str(item.get("started_at") or ""))
            if started is not None and (now - started) > _STALE_RUNNING_SEC:
                item["status"] = "interrupted"
                item["finished_at"] = item.get("finished_at") or _now()
                item["note"] = (item.get("note") or "") or "同步未正常收尾（可能超时或实例切换）"
                item.setdefault("errors", [])
                errs = list(item.get("errors") or [])
                if "stale_running" not in errs:
                    errs.append("stale_running: Serverless 请求中断或跨实例未写回")
                item["errors"] = errs[:20]
                dirty = True
                _remember(item)
        out.append(item)
    if dirty:
        try:
            doc = _read_doc()
            by_id = {str(r.get("id") or ""): r for r in out if str(r.get("id") or "")}
            merged = []
            for r in list(doc.get("runs") or []):
                rid = str(r.get("id") or "")
                merged.append(by_id.get(rid) or r)
            # 补上仅存在于内存的
            seen = {str(r.get("id") or "") for r in merged}
            for r in out:
                rid = str(r.get("id") or "")
                if rid and rid not in seen:
                    merged.insert(0, r)
            doc["runs"] = merged[:_MAX_LOGS]
            _write_doc(doc)
        except Exception:  # noqa: BLE001
            pass
    return out


def list_sync_logs(*, limit: int = 40, kind: str = "") -> dict[str, Any]:
    """
    列出同步日志（新→旧）。合并磁盘 + 进程内存，避免 Serverless 热路径丢日志。

    Args:
        limit: 条数。
        kind: 可选按 kind 过滤。

    Returns:
        {ok, items, count, config, ephemeral}
    """

    doc = _read_doc()
    disk_runs = [r for r in list(doc.get("runs") or []) if isinstance(r, dict)]
    # 内存优先（同实例刚写完的 finish 一定在这里）
    by_id: dict[str, dict[str, Any]] = {}
    for row in disk_runs + list(_MEMORY_RUNS):
        rid = str(row.get("id") or "")
        if not rid:
            continue
        prev = by_id.get(rid)
        # 已完成的覆盖 running；同状态取 finished_at 更新的
        if prev is None:
            by_id[rid] = dict(row)
            continue
        prev_st = str(prev.get("status") or "")
        new_st = str(row.get("status") or "")
        if prev_st == "running" and new_st != "running":
            by_id[rid] = dict(row)
        elif new_st == "running" and prev_st != "running":
            continue
        else:
            by_id[rid] = dict(row)

    runs = list(by_id.values())
    runs.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    runs = _mark_stale_running(runs)

    kind_l = (kind or "").strip().lower()
    if kind_l:
        runs = [r for r in runs if str(r.get("kind") or "").lower() == kind_l]
    lim = max(1, min(int(limit or 40), 100))
    items = runs[:lim]
    ephemeral = bool(os.getenv("VERCEL") or os.getenv("VERCEL_ENV") or os.getenv("LEADFORGE_SKIP_BACKGROUND") == "1")
    return {
        "ok": True,
        "items": items,
        "count": len(runs),
        "shown": len(items),
        "config": load_scheduler_config(),
        "updated_at": doc.get("updated_at") or "",
        "ephemeral": ephemeral,
        "hint": (
            "当前为 Serverless：日志存在本实例 /tmp，刷新可能打到其他实例而暂时看不到；"
            "请以「立即同步」返回结果为准，或稍后再刷新。"
            if ephemeral
            else ""
        ),
    }
