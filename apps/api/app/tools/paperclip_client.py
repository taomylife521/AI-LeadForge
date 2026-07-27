# -*- coding: utf-8 -*-
"""
Paperclip REST 客户端（MIT sidecar）。

作用: 将决策台商机/落地方案交接为 Paperclip Goal/Issue（含 plan 文档与可选子任务）。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import json
from typing import Any, Optional

import httpx

from app.settings import get_settings


class PaperclipNotConfigured(RuntimeError):
    """未配置 PAPERCLIP_BASE_URL 或公司 ID。"""


def _base() -> str:
    return (get_settings().paperclip_base_url or "").strip().rstrip("/")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    key = (get_settings().paperclip_api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def check_paperclip() -> dict[str, Any]:
    """
    探测 Paperclip 是否可达。

    Returns:
        {ok, url, configured, detail}
    """

    url = _base()
    configured = bool(url and get_settings().paperclip_company_id)
    if not url:
        return {"ok": False, "url": "", "configured": False, "detail": "PAPERCLIP_BASE_URL 未配置"}
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            for path in ("/api/health", "/health", "/"):
                try:
                    resp = await client.get(f"{url}{path}", headers=_headers())
                    if resp.status_code < 500:
                        return {
                            "ok": True,
                            "url": url,
                            "configured": configured,
                            "detail": f"HTTP {resp.status_code}",
                        }
                except Exception:  # noqa: BLE001
                    continue
        return {"ok": False, "url": url, "configured": configured, "detail": "unreachable"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "url": url, "configured": configured, "detail": str(exc)[:160]}


async def _request(method: str, path: str, *, json_body: Optional[dict[str, Any]] = None) -> Any:
    """发起 Paperclip API 请求。"""

    url = _base()
    if not url:
        raise PaperclipNotConfigured("PAPERCLIP_BASE_URL 未配置")
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        resp = await client.request(method, f"{url}{path}", headers=_headers(), json=json_body)
        if resp.status_code >= 400:
            raise RuntimeError(f"Paperclip {method} {path} → {resp.status_code}: {resp.text[:240]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except Exception:  # noqa: BLE001
            return {"raw": resp.text[:500]}


async def list_issues(limit: int = 30) -> dict[str, Any]:
    """
    列出当前公司的 Paperclip Issues（供内嵌面板，无需跳转）。

    Returns:
        {ok, items, url, company_id}
    """

    settings = get_settings()
    company_id = (settings.paperclip_company_id or "").strip()
    base = _base()
    if not base:
        raise PaperclipNotConfigured("PAPERCLIP_BASE_URL 未配置")
    if not company_id:
        raise PaperclipNotConfigured("PAPERCLIP_COMPANY_ID 未配置")
    data = await _request("GET", f"/api/companies/{company_id}/issues")
    rows = data if isinstance(data, list) else (data.get("items") or data.get("issues") or [])
    items = []
    for row in rows[: max(1, min(limit, 80))]:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("id") or "")
        items.append(
            {
                "id": iid,
                "title": row.get("title"),
                "status": row.get("status"),
                "priority": row.get("priority"),
                "url": f"{base}/issues/{iid}" if iid else base,
                "updated_at": row.get("updatedAt") or row.get("updated_at"),
            }
        )
    return {"ok": True, "items": items, "count": len(items), "url": base, "company_id": company_id}


async def find_issue_by_title(company_id: str, title: str) -> Optional[dict[str, Any]]:
    """按标题精确匹配已有 issue（幂等）。"""

    title = (title or "").strip()
    if not title:
        return None
    try:
        data = await _request("GET", f"/api/companies/{company_id}/issues")
        rows = data if isinstance(data, list) else (data.get("items") or data.get("issues") or [])
        for row in rows:
            if isinstance(row, dict) and str(row.get("title") or "").strip() == title:
                return row
    except Exception:  # noqa: BLE001
        return None
    return None


async def handoff_landing(
    *,
    topic: str,
    industry: str = "",
    opportunity_context: str = "",
    landing_plan_markdown: str = "",
    projects: Optional[list[dict[str, Any]]] = None,
    decision_brief_summary: str = "",
    trace_id: str = "",
    hitl_task_id: str = "",
) -> dict[str, Any]:
    """
    创建/复用 Paperclip Issue，写入落地方案文档，并拆出子任务草稿。

    Args:
        topic: 商机主题。
        industry: 行业。
        opportunity_context: 上下文。
        landing_plan_markdown: 落地方案 Markdown。
        projects: 可落地项目列表。
        decision_brief_summary: 决策台摘要。
        trace_id / hitl_task_id: 幂等与回溯。

    Returns:
        {ok, issue, url, reused, children}

    Raises:
        PaperclipNotConfigured: 缺配置。
        RuntimeError: API 失败。
    """

    settings = get_settings()
    company_id = (settings.paperclip_company_id or "").strip()
    if not _base():
        raise PaperclipNotConfigured("PAPERCLIP_BASE_URL 未配置；请先启动 Paperclip sidecar")
    if not company_id:
        raise PaperclipNotConfigured("PAPERCLIP_COMPANY_ID 未配置；请在 Paperclip UI 创建公司后写入 .env")

    topic = (topic or "").strip() or "未命名商机"
    title = f"LeadForge落地 · {topic}"[:120]
    if trace_id:
        title = f"LeadForge落地 · {topic[:80]} · {trace_id[:12]}"

    existing = await find_issue_by_title(company_id, title)
    reused = False
    if existing and existing.get("id"):
        issue = existing
        reused = True
    else:
        desc_parts = [
            f"## 商机\n{topic}",
            f"行业: {industry or '—'}",
            f"HITL: {hitl_task_id or '—'} · Trace: {trace_id or '—'}",
            "",
            "## 上下文",
            (opportunity_context or "")[:1500],
            "",
            "## 决策摘要",
            (decision_brief_summary or "")[:1500],
            "",
            "## 选定项目",
        ]
        for p in (projects or [])[:8]:
            desc_parts.append(f"- [{p.get('name')}]({p.get('url')}) · {p.get('landable_role') or p.get('portfolio_role') or ''}")
        body: dict[str, Any] = {
            "title": title,
            "description": "\n".join(desc_parts),
            "status": "todo",
            "priority": "high",
        }
        if settings.paperclip_project_id:
            body["projectId"] = settings.paperclip_project_id
        if settings.paperclip_goal_id:
            body["goalId"] = settings.paperclip_goal_id
        if settings.paperclip_agent_id:
            body["assigneeAgentId"] = settings.paperclip_agent_id
        issue = await _request("POST", f"/api/companies/{company_id}/issues", json_body=body)
        if not isinstance(issue, dict) or not issue.get("id"):
            # 偶发 500 已落库：再查一次
            issue = await find_issue_by_title(company_id, title) or issue

    issue_id = str((issue or {}).get("id") or "")
    if landing_plan_markdown and issue_id:
        try:
            await _request(
                "PUT",
                f"/api/issues/{issue_id}/documents/plan",
                json_body={"format": "markdown", "body": landing_plan_markdown[:20000]},
            )
        except Exception:  # noqa: BLE001
            # 部分版本路径不同：写入 notes
            try:
                await _request(
                    "PUT",
                    f"/api/issues/{issue_id}/documents/notes",
                    json_body={"format": "markdown", "body": landing_plan_markdown[:20000]},
                )
            except Exception:  # noqa: BLE001
                pass

    children: list[dict[str, Any]] = []
    if issue_id and not reused:
        child_titles = [
            "确认付费方与核心动作",
            "Fork 主仓库并跑通本地",
            "领域模型改造（预约/线索/合规）",
            "5 人真实用户验证",
        ]
        for ct in child_titles:
            child_body: dict[str, Any] = {
                "title": ct,
                "description": f"父任务落地拆解 · {topic}",
                "status": "todo",
                "parentId": issue_id,
            }
            if settings.paperclip_project_id:
                child_body["projectId"] = settings.paperclip_project_id
            if settings.paperclip_goal_id:
                child_body["goalId"] = settings.paperclip_goal_id
            try:
                child = await _request("POST", f"/api/companies/{company_id}/issues", json_body=child_body)
                if isinstance(child, dict):
                    children.append(child)
            except Exception:  # noqa: BLE001
                break

    base = _base()
    issue_url = f"{base}/issues/{issue_id}" if issue_id else base
    return {
        "ok": True,
        "reused": reused,
        "issue": issue,
        "issue_id": issue_id,
        "url": issue_url,
        "children": [{"id": c.get("id"), "title": c.get("title")} for c in children],
        "company_id": company_id,
        "envelope": {
            "topic": topic,
            "industry": industry,
            "project_count": len(projects or []),
        },
    }
