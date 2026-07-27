# -*- coding: utf-8 -*-
"""
本地落地任务中心（Paperclip 不可用时的商业版兜底）。

作用: 将商机落地方案写入 data/landing_tasks.json，支持增删查改与子任务状态。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.settings import data_dir

_VALID_STATUS = frozenset({"todo", "doing", "done", "blocked", "cancelled"})


def _store_path() -> Path:
    """落地任务存储路径；不存在则初始化为空数组。"""

    path = data_dir() / "landing_tasks.json"
    if not path.exists():
        path.write_text("[]", encoding="utf-8")
    return path


def _now() -> str:
    """UTC ISO 时间戳。"""

    return datetime.now(timezone.utc).isoformat()


def _read_all() -> list[dict[str, Any]]:
    """读取全部落地任务。"""

    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _write_all(rows: list[dict[str, Any]]) -> None:
    """写回落地任务列表。"""

    _store_path().write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_status(value: str, default: str = "todo") -> str:
    """规范化状态枚举。"""

    s = (value or "").strip().lower()
    return s if s in _VALID_STATUS else default


def _default_children() -> list[dict[str, Any]]:
    """默认拆解子项。"""

    titles = (
        "确认付费方与核心动作",
        "选定主项目并跑通试用",
        "两周 MVP 改造",
        "真实用户验证与止损判断",
    )
    return [{"id": str(uuid.uuid4()), "title": t, "status": "todo"} for t in titles]


def list_landing_tasks(*, limit: int = 40, q: str = "", status: str = "") -> dict[str, Any]:
    """
    列出本地落地任务（支持关键词与状态筛选）。

    Args:
        limit: 返回条数上限。
        q: 标题/主题/行业关键词（空=不过滤）。
        status: 父任务状态过滤（空=全部）。

    Returns:
        {ok, source, items, count}
    """

    rows = _read_all()
    needle = (q or "").strip().lower()
    st = (status or "").strip().lower()
    if needle:
        rows = [
            r
            for r in rows
            if needle in str(r.get("title") or "").lower()
            or needle in str(r.get("topic") or "").lower()
            or needle in str(r.get("industry") or "").lower()
            or any(needle in str(c.get("title") or "").lower() for c in (r.get("children") or []) if isinstance(c, dict))
        ]
    if st and st in _VALID_STATUS:
        rows = [r for r in rows if _norm_status(str(r.get("status") or "")) == st]
    rows = sorted(rows, key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    total = len(rows)
    return {
        "ok": True,
        "source": "local",
        "items": rows[: max(1, min(int(limit or 40), 80))],
        "count": total,
    }


def get_landing_task(task_id: str) -> Optional[dict[str, Any]]:
    """
    按 id 获取单条任务。

    Args:
        task_id: 任务 UUID。

    Returns:
        任务字典；不存在则 None。
    """

    tid = (task_id or "").strip()
    if not tid:
        return None
    for row in _read_all():
        if str(row.get("id") or "") == tid:
            return row
    return None


def create_landing_task(
    *,
    topic: str,
    industry: str = "",
    plan_markdown: str = "",
    projects: Optional[list[dict[str, Any]]] = None,
    context: str = "",
    title: str = "",
    children: Optional[list[dict[str, Any]]] = None,
    reuse_same_title: bool = True,
) -> dict[str, Any]:
    """
    创建落地任务及拆解子项。

    Args:
        topic: 商机主题。
        industry: 行业。
        plan_markdown: 落地方案 Markdown。
        projects: 关联项目列表。
        context: 备注/上下文。
        title: 自定义标题（空则「落地 · topic」）。
        children: 自定义子项；空则用默认四步。
        reuse_same_title: 同标题是否复用更新。

    Returns:
        {ok, reused, task, source}
    """

    topic = (topic or "").strip() or "未命名商机"
    now = _now()
    task_id = str(uuid.uuid4())
    if children and isinstance(children, list):
        kids: list[dict[str, Any]] = []
        for c in children[:20]:
            if not isinstance(c, dict):
                continue
            title_c = str(c.get("title") or "").strip()
            if not title_c:
                continue
            kids.append(
                {
                    "id": str(c.get("id") or uuid.uuid4()),
                    "title": title_c[:120],
                    "status": _norm_status(str(c.get("status") or "todo")),
                }
            )
        if not kids:
            kids = _default_children()
    else:
        kids = _default_children()

    doc = {
        "id": task_id,
        "title": ((title or "").strip() or f"落地 · {topic}")[:120],
        "topic": topic,
        "industry": (industry or "").strip(),
        "status": "todo",
        "priority": "high",
        "context": (context or "")[:2000],
        "plan_markdown": (plan_markdown or "")[:20000],
        "projects": [
            {
                "name": p.get("name"),
                "url": p.get("url"),
                "role": p.get("landable_role") or p.get("portfolio_role"),
            }
            for p in (projects or [])[:8]
            if isinstance(p, dict)
        ],
        "children": kids,
        "created_at": now,
        "updated_at": now,
        "source": "local",
    }
    rows = _read_all()
    if reuse_same_title:
        for row in rows:
            if str(row.get("title") or "") == doc["title"]:
                row["updated_at"] = now
                row["plan_markdown"] = doc["plan_markdown"] or row.get("plan_markdown")
                row["projects"] = doc["projects"] or row.get("projects")
                row["context"] = doc["context"] or row.get("context")
                row["industry"] = doc["industry"] or row.get("industry")
                _write_all(rows)
                return {"ok": True, "reused": True, "task": row, "source": "local"}
    rows.insert(0, doc)
    _write_all(rows[:200])
    return {"ok": True, "reused": False, "task": doc, "source": "local"}


def update_landing_task(task_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """
    更新父任务字段（标题/主题/行业/状态/方案/备注等）。

    Args:
        task_id: 任务 id。
        patch: 待更新字段。

    Returns:
        {ok, task}

    Raises:
        KeyError: 任务不存在。
        ValueError: 输入非法。
    """

    tid = (task_id or "").strip()
    if not tid:
        raise ValueError("task_id 不能为空")
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")

    rows = _read_all()
    for i, row in enumerate(rows):
        if str(row.get("id") or "") != tid:
            continue
        if "title" in patch:
            title = str(patch.get("title") or "").strip()
            if not title:
                raise ValueError("标题不能为空")
            row["title"] = title[:120]
        if "topic" in patch:
            row["topic"] = str(patch.get("topic") or "").strip()[:200]
        if "industry" in patch:
            row["industry"] = str(patch.get("industry") or "").strip()[:80]
        if "status" in patch:
            row["status"] = _norm_status(str(patch.get("status") or ""), default=str(row.get("status") or "todo"))
        if "priority" in patch:
            row["priority"] = str(patch.get("priority") or row.get("priority") or "high")[:20]
        if "context" in patch:
            row["context"] = str(patch.get("context") or "")[:2000]
        if "plan_markdown" in patch:
            row["plan_markdown"] = str(patch.get("plan_markdown") or "")[:20000]
        row["updated_at"] = _now()
        rows[i] = row
        _write_all(rows)
        return {"ok": True, "task": row, "source": "local"}
    raise KeyError(f"任务不存在: {tid}")


def delete_landing_task(task_id: str) -> dict[str, Any]:
    """
    删除落地任务。

    Args:
        task_id: 任务 id。

    Returns:
        {ok, deleted_id}

    Raises:
        KeyError: 不存在。
    """

    tid = (task_id or "").strip()
    if not tid:
        raise ValueError("task_id 不能为空")
    rows = _read_all()
    kept = [r for r in rows if str(r.get("id") or "") != tid]
    if len(kept) == len(rows):
        raise KeyError(f"任务不存在: {tid}")
    _write_all(kept)
    return {"ok": True, "deleted_id": tid, "source": "local"}


def add_landing_child(task_id: str, *, title: str, status: str = "todo") -> dict[str, Any]:
    """
    为任务新增子项。

    Args:
        task_id: 父任务 id。
        title: 子项标题。
        status: 初始状态。

    Returns:
        {ok, task, child}
    """

    title = (title or "").strip()
    if not title:
        raise ValueError("子任务标题不能为空")
    rows = _read_all()
    for i, row in enumerate(rows):
        if str(row.get("id") or "") != (task_id or "").strip():
            continue
        child = {
            "id": str(uuid.uuid4()),
            "title": title[:120],
            "status": _norm_status(status),
        }
        kids = list(row.get("children") or [])
        kids.append(child)
        row["children"] = kids[:40]
        row["updated_at"] = _now()
        rows[i] = row
        _write_all(rows)
        return {"ok": True, "task": row, "child": child, "source": "local"}
    raise KeyError(f"任务不存在: {task_id}")


def update_landing_child(
    task_id: str,
    child_id: str,
    *,
    title: Optional[str] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """
    更新子任务标题或状态。

    Args:
        task_id: 父任务 id。
        child_id: 子任务 id。
        title: 新标题（可选）。
        status: 新状态（可选）。

    Returns:
        {ok, task, child}
    """

    tid = (task_id or "").strip()
    cid = (child_id or "").strip()
    if not tid or not cid:
        raise ValueError("task_id / child_id 不能为空")
    rows = _read_all()
    for i, row in enumerate(rows):
        if str(row.get("id") or "") != tid:
            continue
        kids = list(row.get("children") or [])
        found = None
        for j, c in enumerate(kids):
            if not isinstance(c, dict) or str(c.get("id") or "") != cid:
                continue
            if title is not None:
                t = str(title).strip()
                if not t:
                    raise ValueError("子任务标题不能为空")
                c["title"] = t[:120]
            if status is not None:
                c["status"] = _norm_status(str(status), default=str(c.get("status") or "todo"))
            kids[j] = c
            found = c
            break
        if found is None:
            raise KeyError(f"子任务不存在: {cid}")
        row["children"] = kids
        # 父状态随子项：全部 done → done；有 doing → doing
        statuses = [_norm_status(str(x.get("status") or "")) for x in kids if isinstance(x, dict)]
        if statuses and all(s == "done" for s in statuses):
            row["status"] = "done"
        elif any(s == "doing" for s in statuses):
            row["status"] = "doing"
        elif any(s == "blocked" for s in statuses):
            row["status"] = "blocked"
        row["updated_at"] = _now()
        rows[i] = row
        _write_all(rows)
        return {"ok": True, "task": row, "child": found, "source": "local"}
    raise KeyError(f"任务不存在: {tid}")


def delete_landing_child(task_id: str, child_id: str) -> dict[str, Any]:
    """
    删除子任务。

    Args:
        task_id: 父任务 id。
        child_id: 子任务 id。

    Returns:
        {ok, task}
    """

    tid = (task_id or "").strip()
    cid = (child_id or "").strip()
    if not tid or not cid:
        raise ValueError("task_id / child_id 不能为空")
    rows = _read_all()
    for i, row in enumerate(rows):
        if str(row.get("id") or "") != tid:
            continue
        kids = [c for c in (row.get("children") or []) if isinstance(c, dict) and str(c.get("id") or "") != cid]
        if len(kids) == len(row.get("children") or []):
            raise KeyError(f"子任务不存在: {cid}")
        row["children"] = kids
        row["updated_at"] = _now()
        rows[i] = row
        _write_all(rows)
        return {"ok": True, "task": row, "source": "local"}
    raise KeyError(f"任务不存在: {tid}")
