# -*- coding: utf-8 -*-
"""
中国独立开发者项目源（1c7/chinese-independent-developer）。

作用: 抓取公开 README 中的已上线项目，按关键词/行业筛选，作为商机证据与细分灵感（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.settings import data_dir
from app.tools.hotspot_sources import USER_AGENT

CID_README_URLS = (
    "https://cdn.jsdelivr.net/gh/1c7/chinese-independent-developer@master/README.md",
    "https://ghproxy.net/https://raw.githubusercontent.com/1c7/chinese-independent-developer/master/README.md",
    "https://raw.githubusercontent.com/1c7/chinese-independent-developer/master/README.md",
)
CID_README_URL = CID_README_URLS[0]
CID_SOURCE = "https://github.com/1c7/chinese-independent-developer"

# 项目行：* :white_check_mark: [名称](url)：一句话介绍
_ITEM_RE = re.compile(
    r"^\s*\*\s*:(?P<status>white_check_mark|clock8|x):\s*"
    r"\[(?P<name>[^\]]+)\]\((?P<url>[^)]+)\)\s*[：:]\s*(?P<desc>.+?)\s*$",
    re.MULTILINE,
)

# 核心痛点 / 人群 / 行业细分关键词（用于匹配与标注，非编造）
_PAIN_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("效率工具", ("一键", "自动", "批量", "无需", "秒", "转换", "整理", "管理")),
    ("内容创作", ("短视频", "小说", "写作", "配图", "文案", "杂志", "故事")),
    ("隐私安全", ("离线", "本地", "不上传", "端到端", "私密", "P2P")),
    ("获客变现", ("小红书", "爆款", "线索", "获客", "投放", "私域")),
    ("文件处理", ("PDF", "Markdown", "OCR", "发票", "文档", "传输")),
    ("金融理财", ("投资", "理财", "发票", "报销", "记账")),
    ("教育学习", ("阅读", "注释", "学习", "诗词", "课程")),
    ("社交协作", ("多人", "共同", "聊天", "协作", "分享")),
]

_AUDIENCE_TAGS: list[tuple[str, tuple[str, ...]]] = [
    ("独立开发者", ("开发者", "API", "模型", "Agent", "MCP", "开源")),
    ("自媒体创作者", ("小红书", "短视频", "爆款", "内容", "配图")),
    ("办公白领", ("发票", "报销", "剪贴板", "文档", "PDF", "邮件")),
    ("投资者", ("投资", "分析", "理财", "Fin")),
    ("家长/学生", ("阅读", "故事", "课程", "诗经", "学习")),
    ("跨境/出海", ("GEO", "出海", "国际", ".io", ".org")),
    ("本地生活用户", ("到店", "预约", "家政", "宠物", "洗护")),
]

_INDUSTRY_NICHES: list[tuple[str, tuple[str, ...]]] = [
    ("AI检测/模型质量", ("降智", "模型检测", "OpenAI Compatible", "API 检测")),
    ("AI内容生成", ("故事书", "菜谱", "杂志", "小说", "生成")),
    ("文档智能化", ("PDF", "Markdown", "Doc2Md", "OCR")),
    ("创作者工具", ("小红书", "工作台", "爆款", "配图")),
    ("效率办公", ("发票", "报销", "剪贴板", "快传", "文件传输")),
    ("语音输入", ("语音", "whisper", "输入法")),
    ("社交娱乐", ("听歌", "聊天", "游戏", "ASCII")),
    ("垂直 SaaS", ("GEO", "Fin-Agent", "工作台", "Agent")),
    ("牙科到店", ("牙科", "口腔", "种植", "矫正")),
    ("美业到店", ("医美", "皮肤", "美甲", "美睫")),
    ("少儿教培", ("托管", "试听", "兴趣班", "少儿")),
    ("宠物服务", ("宠物", "洗护", "寄养")),
    ("家政到店", ("保洁", "收纳", "家电清洗")),
    ("跨境出海", ("出海", "GEO", "跨境")),
]


def _cache_path() -> Path:
    return data_dir() / "cid_indie_readme.md"


def _status_label(code: str) -> str:
    return {"white_check_mark": "live", "clock8": "wip", "x": "dead"}.get(code, "unknown")


def _tag_by_keywords(text: str, table: list[tuple[str, tuple[str, ...]]]) -> list[str]:
    blob = text.lower()
    hits: list[str] = []
    for label, keys in table:
        if any(k.lower() in blob for k in keys):
            hits.append(label)
    return hits[:3]


async def fetch_cid_readme(*, force_refresh: bool = False, timeout: float = 90.0) -> str:
    """
    拉取或读取缓存的 CID README（多镜像回退）。

    Raises:
        RuntimeError: 网络失败且无缓存。
    """

    path = _cache_path()
    if path.exists() and not force_refresh:
        # 24h 内缓存可用
        age = datetime.now(timezone.utc).timestamp() - path.stat().st_mtime
        if age < 86400:
            return path.read_text(encoding="utf-8")

    errors: list[str] = []
    text = ""
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for url in CID_README_URLS:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    candidate = resp.text or ""
                    # README 很大，至少应包含项目列表标记
                    if len(candidate) < 2000 or ":white_check_mark:" not in candidate:
                        errors.append(f"{url}: content too short or unexpected")
                        continue
                    text = candidate
                    break
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{url}: {exc}")
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc))

    if not text:
        if path.exists():
            return path.read_text(encoding="utf-8")
        raise RuntimeError("无法拉取 CID README: " + " | ".join(errors[:4]))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def parse_cid_projects(markdown: str) -> list[dict[str, Any]]:
    """解析 README 中的项目条目。"""

    items: list[dict[str, Any]] = []
    for match in _ITEM_RE.finditer(markdown or ""):
        name = (match.group("name") or "").strip()
        url = (match.group("url") or "").strip()
        desc = (match.group("desc") or "").strip()
        # 去掉尾部 markdown 链接噪音
        desc = re.sub(r"\s*-\s*\[.*?\]\(.*?\)\s*$", "", desc).strip()
        if not name or not url:
            continue
        status = _status_label(match.group("status"))
        blob = f"{name} {desc}"
        items.append(
            {
                "name": name,
                "url": url,
                "description": desc[:280],
                "status": status,
                "pain_tags": _tag_by_keywords(blob, _PAIN_TAGS),
                "audience_tags": _tag_by_keywords(blob, _AUDIENCE_TAGS),
                "industry_niches": _tag_by_keywords(blob, _INDUSTRY_NICHES),
                "provider": "cid_indie",
                "source": CID_SOURCE,
            }
        )
    return items


def filter_cid_projects(
    items: list[dict[str, Any]],
    *,
    keyword: str = "",
    industry: str = "",
    only_live: bool = True,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """按关键词/行业过滤，优先已上线。"""

    kw = (keyword or "").strip().lower()
    ind = (industry or "").strip().lower()
    out: list[dict[str, Any]] = []
    for row in items:
        if only_live and row.get("status") != "live":
            continue
        blob = (
            f"{row.get('name')} {row.get('description')} "
            f"{' '.join(row.get('pain_tags') or [])} "
            f"{' '.join(row.get('audience_tags') or [])} "
            f"{' '.join(row.get('industry_niches') or [])}"
        ).lower()
        if kw and kw not in blob and not any(k in blob for k in kw.split() if len(k) >= 2):
            # 宽松：任一痛点/人群标签命中行业名也可
            if ind and ind not in blob:
                continue
            if not ind:
                continue
        if ind and ind not in blob and not any(ind in t.lower() for t in (row.get("industry_niches") or [])):
            # 行业未命中时，仍允许强关键词命中
            if not kw or kw not in blob:
                continue
        out.append(row)
        if len(out) >= limit:
            break
    # 若过滤过严，回退取最新 live
    if not out:
        for row in items:
            if only_live and row.get("status") != "live":
                continue
            out.append(row)
            if len(out) >= min(limit, 15):
                break
    return out


def cid_to_hotspot_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """转为热点条目结构，便于并入商机研究。"""

    result: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        niches = " / ".join(row.get("industry_niches") or []) or "独立开发"
        pains = " / ".join(row.get("pain_tags") or []) or "未标注"
        audience = " / ".join(row.get("audience_tags") or []) or "未标注"
        snippet = (
            f"{row.get('description')} | 痛点:{pains} | 人群:{audience} | 细分:{niches}"
        )
        result.append(
            {
                "title": row.get("name"),
                "url": row.get("url"),
                "snippet": snippet,
                "provider": "cid_indie",
                "heat": 40.0 + max(0, 20 - idx),
                "meta": {
                    "status": row.get("status"),
                    "pain_tags": row.get("pain_tags"),
                    "audience_tags": row.get("audience_tags"),
                    "industry_niches": row.get("industry_niches"),
                    "source": CID_SOURCE,
                },
            }
        )
    return result


def cid_to_lead_clues(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """转为商机线索（灵感/对标，非融资事件）。"""

    clues: list[dict[str, Any]] = []
    for row in rows:
        clues.append(
            {
                "company": row.get("name"),
                "event": "cid_indie_project",
                "opportunity_angle": (
                    f"对标独立开发者产品「{row.get('name')}」："
                    f"{(row.get('description') or '')[:80]}；"
                    f"可从痛点[{'/'.join(row.get('pain_tags') or [])}]、"
                    f"人群[{'/'.join(row.get('audience_tags') or [])}]、"
                    f"细分[{'/'.join(row.get('industry_niches') or [])}]切入差异化。"
                ),
                "success_signal": f"status={row.get('status')}",
                "confidence": 0.55 if row.get("status") == "live" else 0.35,
                "source_url": row.get("url"),
                "pain_tags": row.get("pain_tags"),
                "audience_tags": row.get("audience_tags"),
                "industry_niches": row.get("industry_niches"),
            }
        )
    return clues


async def collect_cid_indie_clues(
    *,
    keyword: str = "",
    industry: str = "",
    limit: int = 20,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    采集并筛选 CID 独立开发者项目。

    Returns:
        {items, lead_clues, hotspot_items, source, count, error?}
    """

    try:
        md = await fetch_cid_readme(force_refresh=force_refresh)
        parsed = parse_cid_projects(md)
        filtered = filter_cid_projects(
            parsed,
            keyword=keyword,
            industry=industry,
            only_live=True,
            limit=limit,
        )
        return {
            "source": CID_SOURCE,
            "count": len(filtered),
            "parsed_total": len(parsed),
            "items": filtered,
            "lead_clues": cid_to_lead_clues(filtered),
            "hotspot_items": cid_to_hotspot_items(filtered),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "source": CID_SOURCE,
            "count": 0,
            "parsed_total": 0,
            "items": [],
            "lead_clues": [],
            "hotspot_items": [],
            "error": str(exc),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
