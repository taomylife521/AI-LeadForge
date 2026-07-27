# -*- coding: utf-8 -*-
"""
LeadForge 多维记忆系统。

作用: 短期 Trace 链、程序性 Skill/Rule 索引、长期 SQLite+FTS5 经验；会话开始注入冻结快照。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.envelope import DataEnvelope
from app.settings import data_dir, load_skills_allowlist, repo_root


@dataclass
class MemorySnapshot:
    """注入系统提示词的冻结快照。"""

    theme_pack: str
    preferences: list[str]
    lessons: list[dict[str, Any]]
    procedural_skills: list[str]
    procedural_rules: list[str]
    generated_at: str

    def to_prompt_block(self) -> str:
        """渲染为不可改写的冻结区文本。"""

        lines = [
            "=== MEMORY SNAPSHOT (FROZEN — DO NOT MODIFY) ===",
            f"theme_pack: {self.theme_pack}",
            f"generated_at: {self.generated_at}",
            "preferences:",
        ]
        lines.extend(f"- {item}" for item in self.preferences[:10] or ["(none)"])
        lines.append("lessons (success+failure few-shot):")
        for lesson in self.lessons[:6]:
            lines.append(
                f"- [{lesson.get('outcome')}] {lesson.get('title')}: {lesson.get('summary')}"
            )
        if not self.lessons:
            lines.append("- (none yet — first run)")
        lines.append("procedural_skills: " + ", ".join(self.procedural_skills[:20]) or "(none)")
        lines.append("procedural_rules: " + ", ".join(self.procedural_rules[:20]) or "(none)")
        lines.append("=== END SNAPSHOT ===")
        return "\n".join(lines)


class MemoryStore:
    """
    SQLite + FTS5 长期记忆与 Trace 持久化。

    Attributes:
        db_path: SQLite 文件路径。
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (data_dir() / "leadforge.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """初始化表结构与 FTS5。"""

        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    theme_pack TEXT NOT NULL,
                    topic TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS envelopes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    status TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT,
                    theme_pack TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metrics_json TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
                    title, summary, theme_pack, outcome, content='lessons', content_rowid='id'
                );

                CREATE TABLE IF NOT EXISTS preferences (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS hitl_tasks (
                    id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    decided_at TEXT
                );
                """
            )

    def save_envelope(self, envelope: DataEnvelope) -> None:
        """持久化单条信封。"""

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO envelopes(trace_id, span_id, agent, status, body_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    envelope.trace_id,
                    envelope.span_id,
                    envelope.agent.value,
                    envelope.status.value,
                    envelope.model_dump_json(),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO traces(trace_id, theme_pack, topic, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(trace_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    theme_pack=excluded.theme_pack
                """,
                (
                    envelope.trace_id,
                    envelope.metadata.theme_pack,
                    envelope.payload.get("topic") or envelope.payload.get("theme"),
                    envelope.status.value,
                    now,
                    now,
                ),
            )

    def list_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        """列出最近 Trace。"""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM traces ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_trace_envelopes(self, trace_id: str) -> list[dict[str, Any]]:
        """按 Trace 拉取全链路信封。"""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT body_json FROM envelopes WHERE trace_id=? ORDER BY id ASC",
                (trace_id,),
            ).fetchall()
        return [json.loads(row["body_json"]) for row in rows]

    def add_lesson(
        self,
        *,
        theme_pack: str,
        outcome: str,
        title: str,
        summary: str,
        trace_id: Optional[str] = None,
        metrics: Optional[dict[str, Any]] = None,
    ) -> None:
        """写入长期经验并同步 FTS。"""

        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO lessons(trace_id, theme_pack, outcome, title, summary, metrics_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    theme_pack,
                    outcome,
                    title,
                    summary,
                    json.dumps(metrics or {}, ensure_ascii=False),
                    now,
                ),
            )
            row_id = cur.lastrowid
            conn.execute(
                """
                INSERT INTO lessons_fts(rowid, title, summary, theme_pack, outcome)
                VALUES (?, ?, ?, ?, ?)
                """,
                (row_id, title, summary, theme_pack, outcome),
            )

    def search_lessons(self, theme_pack: str, query: str = "", limit: int = 6) -> list[dict[str, Any]]:
        """
        检索历史成功/失败案例（Few-Shot 强制上下文）。

        Args:
            theme_pack: 主题包。
            query: FTS 查询；空则按主题取最近记录。
            limit: 条数。
        """

        raw = (query or "").strip()
        # theme_pack / 推荐主题常含 - / : 等；FTS5 会当运算符，必须避开。
        fts_unsafe = set('-:*^"()/\\@!~<>|{}[]?')
        use_fts = bool(raw) and raw != theme_pack and not any(ch in raw for ch in fts_unsafe)

        with self._connect() as conn:
            rows = []
            if use_fts:
                try:
                    rows = conn.execute(
                        """
                        SELECT l.* FROM lessons_fts f
                        JOIN lessons l ON l.id = f.rowid
                        WHERE lessons_fts MATCH ? AND l.theme_pack = ?
                        ORDER BY l.id DESC LIMIT ?
                        """,
                        (raw, theme_pack, limit),
                    ).fetchall()
                except Exception:  # noqa: BLE001
                    use_fts = False
                    rows = []
            if not use_fts and raw and raw != theme_pack:
                like = f"%{raw}%"
                rows = conn.execute(
                    """
                    SELECT * FROM lessons
                    WHERE theme_pack=? AND (title LIKE ? OR summary LIKE ?)
                    ORDER BY id DESC LIMIT ?
                    """,
                    (theme_pack, like, like, limit),
                ).fetchall()
            elif not rows:
                rows = conn.execute(
                    """
                    SELECT * FROM lessons WHERE theme_pack=? ORDER BY id DESC LIMIT ?
                    """,
                    (theme_pack, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def create_hitl(
        self,
        *,
        task_id: str,
        trace_id: str,
        gate: str,
        title: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """创建人工审批任务。"""

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": task_id,
            "trace_id": trace_id,
            "gate": gate,
            "title": title,
            "body_json": json.dumps(body, ensure_ascii=False),
            "status": "pending",
            "created_at": now,
            "decided_at": None,
        }
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hitl_tasks(id, trace_id, gate, title, body_json, status, created_at, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["trace_id"],
                    record["gate"],
                    record["title"],
                    record["body_json"],
                    record["status"],
                    record["created_at"],
                    record["decided_at"],
                ),
            )
        return {**record, "body": body}

    def list_hitl(self, status: str = "pending") -> list[dict[str, Any]]:
        """列出审批任务。"""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM hitl_tasks WHERE status=? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["body"] = json.loads(item.pop("body_json"))
            result.append(item)
        return result

    def get_hitl(self, task_id: str) -> dict[str, Any]:
        """按 id 读取 HITL 任务。"""

        with self._connect() as conn:
            row = conn.execute("SELECT * FROM hitl_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"HITL 任务不存在: {task_id}")
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        return item

    def update_hitl_body(self, task_id: str, body_patch: dict[str, Any]) -> dict[str, Any]:
        """
        合并更新 HITL body（如写入 decision_brief）。

        Args:
            task_id: 任务 id。
            body_patch: 要合并进 body 的字段。
        """

        item = self.get_hitl(task_id)
        body = dict(item.get("body") or {})
        body.update(body_patch or {})
        with self._connect() as conn:
            conn.execute(
                "UPDATE hitl_tasks SET body_json=? WHERE id=?",
                (json.dumps(body, ensure_ascii=False), task_id),
            )
        item["body"] = body
        return item

    def decide_hitl(self, task_id: str, approve: bool, note: str = "") -> dict[str, Any]:
        """审批通过/拒绝。"""

        status = "approved" if approve else "rejected"
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "UPDATE hitl_tasks SET status=?, decided_at=? WHERE id=?",
                (status, now, task_id),
            )
            row = conn.execute("SELECT * FROM hitl_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise KeyError(f"HITL 任务不存在: {task_id}")
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        item["note"] = note
        return item

    def build_snapshot(self, theme_pack: str, topic: str = "") -> MemorySnapshot:
        """构建冻结快照（含强制 ≥3 案例检索，不足则如实返回）。"""

        lessons = self.search_lessons(theme_pack, query=topic or theme_pack, limit=6)
        allowlist = load_skills_allowlist()
        skill_ids: list[str] = []
        for agent_skills in (allowlist.get("agents") or {}).values():
            for item in agent_skills or []:
                skill_ids.append(item.get("id", ""))
        for item in allowlist.get("meta_skills") or []:
            skill_ids.append(item.get("id", ""))

        rules_dir = repo_root() / "rules" / "global"
        rule_names = [p.name for p in rules_dir.glob("*.md")] if rules_dir.exists() else []

        return MemorySnapshot(
            theme_pack=theme_pack,
            preferences=["prefer_cn_ad_law_compliance", "require_hitl_for_paid_ads"],
            lessons=[
                {
                    "outcome": row.get("outcome"),
                    "title": row.get("title"),
                    "summary": row.get("summary"),
                }
                for row in lessons
            ],
            procedural_skills=[s for s in skill_ids if s],
            procedural_rules=rule_names,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
