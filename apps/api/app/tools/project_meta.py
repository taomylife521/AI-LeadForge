# -*- coding: utf-8 -*-
"""
创投/落地项目元数据抽取。

作用: 从标题与摘要中解析地区、融资额、阶段、公司性质；供推荐页签筛选使用（禁止 mock 编造）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import re
from typing import Any, Optional


_STAGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("种子轮", re.compile(r"种子轮|Seed\s*Round|seed\s*funding", re.I)),
    ("天使轮", re.compile(r"天使轮|Angel\s*Round", re.I)),
    ("Pre-A", re.compile(r"Pre[\-\s]?A|PreA", re.I)),
    ("A轮", re.compile(r"(?<![A-Za-z])A\s*轮|Series\s*A", re.I)),
    ("B轮", re.compile(r"(?<![A-Za-z])B\s*轮|Series\s*B", re.I)),
    ("C轮", re.compile(r"(?<![A-Za-z])C\s*轮|Series\s*C", re.I)),
    ("D轮+", re.compile(r"(?<![A-Za-z])[DEF]\s*轮|Series\s*[DEF]|战略融资|IPO|上市", re.I)),
]

_AMOUNT_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>亿|千万|百万|万)?\s*(?P<cur>美元|美金|人民币|元|CNY|USD|\$)?",
    re.I,
)

_REGION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("北京", ("北京", "海淀", "朝阳", "中关村")),
    ("上海", ("上海", "浦东", "徐汇")),
    ("深圳", ("深圳", "南山", "福田")),
    ("杭州", ("杭州", "余杭", "滨江")),
    ("广州", ("广州", "天河", "番禺")),
    ("成都", ("成都", "高新区")),
    ("武汉", ("武汉",)),
    ("南京", ("南京",)),
    ("苏州", ("苏州",)),
    ("中国香港", ("香港", "Hong Kong")),
    ("新加坡", ("新加坡", "Singapore")),
    ("美国", ("硅谷", "旧金山", "纽约", "美国", "USA", "US ", "Silicon Valley")),
    ("中国", ("中国", "国内", "全国")),
]

_NATURE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("开源项目", ("github", "开源", "open[- ]?source", "repo")),
    ("独立开发者产品", ("独立开发", "indie", "个人开发")),
    ("创业公司", ("创业", "初创", "startup", "获投", "融资")),
    ("成熟公司", ("独角兽", "上市公司", "集团", "IPO")),
]


def _to_amount_cny_wan(num: float, unit: str, cur: str) -> Optional[float]:
    """
    粗略换算为「万元人民币」便于筛选（汇率仅作区间参考，非精确报价）。

    Returns:
        万元人民币近似值；无法判断则 None。
    """

    unit = (unit or "").strip()
    cur = (cur or "").strip().upper()
    usd = cur in {"$", "USD", "美金", "美元"} or (not cur and unit in {"亿", "千万", "百万"} and False)
    # 有明确美元符号时按 7 估算；否则默认人民币
    if "$" in cur or "USD" in cur or "美金" in cur or "美元" in cur:
        usd = True
    mult = 1.0
    if unit == "亿":
        mult = 10000.0  # 亿 → 万
    elif unit == "千万":
        mult = 1000.0
    elif unit == "百万":
        mult = 100.0
    elif unit == "万":
        mult = 1.0
    else:
        # 裸数字且无单位：不可靠
        if not unit and not usd:
            return None
        mult = 1.0
    value = num * mult
    if usd:
        value *= 7.0
    return value


def extract_project_meta(name: str = "", summary: str = "", *, source: str = "", kind: str = "") -> dict[str, Any]:
    """
    从标题/摘要抽取筛选维度（仅依据原文，未知标 unknown）。

    Returns:
        region / funding_stage / funding_amount_raw / funding_amount_wan /
        company_nature / funding_band
    """

    text = f"{name or ''} {summary or ''}"
    blob = text.lower()

    stage = "unknown"
    for label, pat in _STAGE_PATTERNS:
        if pat.search(text):
            stage = label
            break

    amount_raw = ""
    amount_wan: Optional[float] = None
    for m in _AMOUNT_RE.finditer(text):
        unit = m.group("unit") or ""
        cur = m.group("cur") or ""
        # 跳过明显非融资的数字（年份、百分比等）
        ctx = text[max(0, m.start() - 8) : m.end() + 8]
        if re.search(r"年|%|折|人|次|天|月", ctx) and "融资" not in ctx and "获投" not in ctx and "轮" not in ctx:
            continue
        if not unit and not cur:
            continue
        try:
            num = float(m.group("num"))
        except ValueError:
            continue
        amount_raw = m.group(0).strip()
        amount_wan = _to_amount_cny_wan(num, unit, cur)
        break

    # 融资额区间（便于筛选）
    band = "unknown"
    if amount_wan is not None:
        if amount_wan < 500:
            band = "under_5m"  # <500万
        elif amount_wan < 5000:
            band = "5m_50m"
        elif amount_wan < 50000:
            band = "50m_500m"
        else:
            band = "over_500m"

    region = "unknown"
    for label, keys in _REGION_RULES:
        if any(k.lower() in blob or k in text for k in keys):
            region = label
            break
    if region == "unknown":
        if any(x in (source or "").lower() for x in ("36kr", "cyzone", "cid")):
            region = "中国"
        elif "github" in (source or "").lower() or kind == "github_repo":
            region = "全球/开源"

    nature = "unknown"
    src = (source or "").lower()
    if "github" in src or kind == "github_repo":
        nature = "开源项目"
    elif "cid" in src:
        nature = "独立开发者产品"
    else:
        for label, keys in _NATURE_RULES:
            hit = False
            for k in keys:
                if any(ch in k for ch in ".?*["):
                    if re.search(k, text, flags=re.I):
                        hit = True
                        break
                elif k.lower() in blob or k in text:
                    hit = True
                    break
            if hit:
                nature = label
                break
        if nature == "unknown" and stage != "unknown":
            nature = "创业公司"

    return {
        "region": region,
        "funding_stage": stage,
        "funding_amount_raw": amount_raw or "unknown",
        "funding_amount_wan": amount_wan,
        "funding_band": band,
        "company_nature": nature,
    }


def enrich_project_row(row: dict[str, Any]) -> dict[str, Any]:
    """为项目行补全筛选元数据（不覆盖已有非空字段）。"""

    if not isinstance(row, dict):
        return row
    meta = extract_project_meta(
        str(row.get("name") or ""),
        str(row.get("summary") or ""),
        source=str(row.get("source") or row.get("source_label") or ""),
        kind=str(row.get("kind") or ""),
    )
    out = dict(row)
    for k, v in meta.items():
        if out.get(k) in (None, "", "unknown") or k not in out:
            out[k] = v
    return out
