# -*- coding: utf-8 -*-
"""
全网热榜采集（与 TrendRadar 同源：newsnow）。

作用: 在 TrendRadar MCP 未启动时，直接请求 newsnow 公开热榜 API，
      覆盖微博/知乎/百度/抖音等平台，保证「看热点」为真实热搜而非 GitHub 仓库。
作者: LeadForge
创建时间: 2026-07-26
参考: https://github.com/sansan0/TrendRadar （数据源 newsnow）
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.settings import get_settings
from app.tools.hotspot_sources import _item

# 与 TrendRadar config.yaml platforms.sources 对齐的常用平台
NEWSNOW_PLATFORMS: list[tuple[str, str]] = [
    ("weibo", "微博"),
    ("zhihu", "知乎"),
    ("baidu", "百度热搜"),
    ("toutiao", "今日头条"),
    ("douyin", "抖音"),
    ("bilibili-hot-search", "哔哩哔哩"),
    ("thepaper", "澎湃新闻"),
    ("cls-hot", "财联社"),
    ("wallstreetcn-hot", "华尔街见闻"),
    ("tieba", "贴吧"),
    ("ifeng", "凤凰网"),
]


def _newsnow_base() -> str:
    """newsnow API 根地址（可配置，默认公共实例）。"""

    settings = get_settings()
    base = (getattr(settings, "newsnow_api_url", None) or "").strip().rstrip("/")
    return base or "https://newsnow.busiyi.world/api/s"


async def fetch_newsnow_platform(platform_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """
    拉取单一平台热榜。

    Args:
        platform_id: newsnow 平台 id（如 weibo）。
        limit: 条数上限。

    Returns:
        归一化热点列表。

    Raises:
        RuntimeError: HTTP 失败或无数据。
    """

    platform_id = (platform_id or "").strip()
    if not platform_id:
        raise ValueError("platform_id 必填")
    url = f"{_newsnow_base()}?id={platform_id}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://newsnow.busiyi.world/",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"newsnow {platform_id} HTTP {resp.status_code}")
        data = resp.json()
    items_raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items_raw, list) or not items_raw:
        raise RuntimeError(f"newsnow {platform_id} 无条目")

    name_map = {pid: label for pid, label in NEWSNOW_PLATFORMS}
    label = name_map.get(platform_id, platform_id)
    out: list[dict[str, Any]] = []
    # 默认最多取前 30 名；名次 1 热度最高，严格可按 rank/heat 排序
    take = max(1, min(int(limit or 30), 50))
    for i, row in enumerate(items_raw[:take]):
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        link = str(row.get("url") or row.get("mobileUrl") or "").strip()
        rank = i + 1
        heat = float(1000 - rank)  # 第1名=999 … 保证名次与热度同序
        out.append(
            _item(
                title=title[:200],
                url=link or f"newsnow://{platform_id}/{i}",
                snippet=f"{label}热榜 · 第{rank}名",
                provider=f"newsnow:{platform_id}",
                heat=heat,
                meta={
                    "platform": platform_id,
                    "platform_label": label,
                    "rank": rank,
                    "data_source": "newsnow",
                    "trendradar_compatible": True,
                },
            )
        )
    return out


async def collect_newsnow_hotspots(
    *,
    limit: int = 240,
    platforms: Optional[list[str]] = None,
    per_platform: int = 30,
) -> dict[str, Any]:
    """
    聚合多平台真实热搜（TrendRadar 同源）。

    Args:
        limit: 总条数上限。
        platforms: 平台 id 列表；默认主流中文平台。
        per_platform: 每平台条数（默认前 30 名）。

    Returns:
        {items, sources_used, errors, generated_at, provider}
    """

    wanted = platforms or [p for p, _ in NEWSNOW_PLATFORMS[:8]]
    errors: list[str] = []
    sources_used: list[str] = []
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    per = max(1, min(int(per_platform or 30), 50))

    for pid in wanted:
        try:
            rows = await fetch_newsnow_platform(pid, limit=per)
            if rows:
                sources_used.append(f"newsnow:{pid}")
            for row in rows:
                key = (row.get("url") or row.get("title") or "").strip()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(row)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{pid}: {str(exc)[:120]}")

    # 跨平台汇总时仍保留各平台内名次信息；按平台名次再热度排序
    def _k(row: dict[str, Any]) -> tuple[str, int, float]:
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        plat = str(meta.get("platform") or "")
        try:
            rank = int(meta.get("rank") or 9999)
        except (TypeError, ValueError):
            rank = 9999
        return (plat, rank, -float(row.get("heat") or 0))

    merged.sort(key=_k)
    max_total = max(per * len(wanted), int(limit or 240))
    items = merged[: max(1, min(max_total, 400))]
    if not items:
        raise RuntimeError("全网热榜全部失败: " + " | ".join(errors[:6]))

    return {
        "items": items,
        "count": len(items),
        "sources_used": sources_used,
        "errors": errors,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider": "newsnow",
        "note": "与 TrendRadar 同源（newsnow 多平台热榜）",
    }
