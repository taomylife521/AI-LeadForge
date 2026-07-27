# -*- coding: utf-8 -*-
"""
商机综合成功率评分。

作用: 按 A(可快速验证) / B(付费意愿) / C(竞争缺口) 等权综合打分，供排序与展示。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

from typing import Any


# A/B/C 等权（用户明确三项都看重）
WEIGHT_VALIDATE = 1.0 / 3.0  # A: 1–2 周可验证、成本低
WEIGHT_PAY = 1.0 / 3.0  # B: 付费意愿强、客单价清晰
WEIGHT_GAP = 1.0 / 3.0  # C: 竞争弱、缺口明确


def _clamp01(value: Any, fallback: float = 0.0) -> float:
    """
    将分值规范到 [0, 1]。

    Args:
        value: 原始分值。
        fallback: 无效时的默认值。

    Returns:
        0~1 浮点数。
    """

    try:
        num = float(value)
    except (TypeError, ValueError):
        num = fallback
    if num < 0:
        return 0.0
    if num > 1:
        return 1.0
    return num


def score_opportunity_abc(row: dict[str, Any]) -> dict[str, float]:
    """
    计算单条商机的 A/B/C 分与综合分。

    Args:
        row: 商机对象（可含 validate_ease / willingness_to_pay / competition_gap 等字段）。

    Returns:
        {a_validate, b_pay, c_gap, composite, success_likelihood, feasibility}
    """

    conf = _clamp01(row.get("confidence"), 0.0)
    # 兼容旧字段：feasibility≈可验证，success_likelihood≈综合观感
    a = _clamp01(
        row.get("validate_ease")
        or row.get("a_validate")
        or row.get("feasibility")
        or conf,
        conf,
    )
    b = _clamp01(
        row.get("willingness_to_pay")
        or row.get("b_pay")
        or row.get("pay_clarity")
        or conf,
        conf,
    )
    c = _clamp01(
        row.get("competition_gap")
        or row.get("c_gap")
        or row.get("gap_score")
        or conf,
        conf,
    )
    composite = a * WEIGHT_VALIDATE + b * WEIGHT_PAY + c * WEIGHT_GAP
    return {
        "a_validate": round(a, 4),
        "b_pay": round(b, 4),
        "c_gap": round(c, 4),
        "composite": round(composite, 4),
        "success_likelihood": round(composite, 4),
        "feasibility": round(a, 4),
    }


def enrich_opportunity_scores(opportunities: list[Any]) -> list[dict[str, Any]]:
    """
    为商机列表写入 A/B/C 与综合分，并按综合分降序排序。

    Args:
        opportunities: 原始商机列表。

    Returns:
        带 scores / 规范化字段后的列表。
    """

    enriched: list[dict[str, Any]] = []
    for row in opportunities:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        scores = score_opportunity_abc(item)
        item["scores"] = {
            "A_validate_ease": scores["a_validate"],
            "B_willingness_to_pay": scores["b_pay"],
            "C_competition_gap": scores["c_gap"],
            "composite": scores["composite"],
            "weights": {"A": WEIGHT_VALIDATE, "B": WEIGHT_PAY, "C": WEIGHT_GAP},
        }
        item["validate_ease"] = scores["a_validate"]
        item["willingness_to_pay"] = scores["b_pay"]
        item["competition_gap"] = scores["c_gap"]
        item["success_likelihood"] = scores["success_likelihood"]
        item["feasibility"] = scores["feasibility"]
        enriched.append(item)
    enriched.sort(key=lambda x: float(x.get("scores", {}).get("composite") or 0), reverse=True)
    return enriched
