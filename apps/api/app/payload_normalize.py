# -*- coding: utf-8 -*-
"""
LeadForge 商业模式载荷规范化。

作用: 将 LLM 不稳定输出整理为红队可消费的结构，避免空输出/异常卡死。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _as_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_business_model_payload(raw: Any) -> dict[str, Any]:
    """
    规范化商业模式 JSON，补齐红队检查所需字段。

    Args:
        raw: 上游解析结果（可能为空、嵌套异常、字段类型错误）。

    Returns:
        可安全送入红队的 dict。
    """

    data = _as_dict(raw)
    # 兼容包在 data/result/business_model 里的情况
    for key in ("business_model", "payload", "result", "data"):
        nested = data.get(key)
        if isinstance(nested, dict) and (
            "pricing" in nested or "unit_economics" in nested or "budget_cap_test_cny" in nested
        ):
            data = nested
            break

    pricing = _as_dict(data.get("pricing"))
    if not pricing and isinstance(data.get("plans"), list):
        pricing = {"plans": data.get("plans")}

    cpl = _as_float(pricing.get("cpl_target_cny"), 35.0)
    if cpl < 10:
        cpl = 35.0
    pricing.setdefault("model", data.get("pricing_model") or "page_subscription_plus_cpl")
    pricing["cpl_target_cny"] = cpl
    if not isinstance(pricing.get("plans"), list) or not pricing.get("plans"):
        pricing["plans"] = [
            {"name": "单页版", "price_cny": 199, "unit": "月"},
            {"name": "增长版", "price_cny": 599, "unit": "月"},
        ]

    ue = _as_dict(data.get("unit_economics"))
    margin = _as_float(ue.get("gross_margin_est"), 0.55)
    if margin < 0.3:
        margin = 0.55
    ue["gross_margin_est"] = margin
    ue["payback_months"] = int(_as_float(ue.get("payback_months"), 1))

    budget = _as_float(data.get("budget_cap_test_cny"), 200.0)
    if budget <= 0:
        budget = 200.0

    return {
        "positioning": str(data.get("positioning") or "本地到店获客落地页订阅"),
        "pricing": pricing,
        "mvp_scope": data.get("mvp_scope")
        if isinstance(data.get("mvp_scope"), list)
        else ["落地页", "表单线索", "投放素材"],
        "unit_economics": ue,
        "budget_cap_test_cny": budget,
        "_normalized": True,
        "_raw_keys": list(data.keys()),
    }


def extract_json_dict_from_blocks(inputs: list[dict[str, Any]]) -> dict[str, Any]:
    """
    从多模态输入块中提取最像商业模式/商机的 JSON 对象。

    优先选择含 pricing/unit_economics/opportunities 的块。
    """

    import json

    candidates: list[dict[str, Any]] = []
    for block in inputs or []:
        if block.get("type") != "text":
            continue
        content = (block.get("content") or "").strip()
        if not content:
            continue
        parsed = None
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            start, end = content.find("{"), content.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(content[start : end + 1])
                except json.JSONDecodeError:
                    parsed = None
        if isinstance(parsed, dict):
            candidates.append(parsed)

    if not candidates:
        return {}

    def score(d: dict[str, Any]) -> int:
        s = 0
        for k in ("pricing", "unit_economics", "budget_cap_test_cny", "opportunities", "recommended", "mvp_scope"):
            if k in d:
                s += 3
        s += len(d)
        return s

    return max(candidates, key=score)
