# -*- coding: utf-8 -*-
"""
商机挖掘与重构引擎（四阶段狩猎）。

作用: 主动狩猎非共识机会——宏观趋势雷达 → 微观痛点探针 → 商业模式重构 → 专家质询裁决。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.tools.cn_search import SearchConfigError, china_web_search
from app.tools.expert_framework import (
    EXPERT_CHAIN_FOR_OPPORTUNITY,
    RECOMMEND_ABANDON,
    RECOMMEND_CAUTIOUS,
    RECOMMEND_STRONG,
)
from app.tools.hotspot_sources import collect_free_hotspots


def _has_search_key() -> bool:
    """是否配置商业搜索 Key。"""

    return bool(
        os.getenv("BOCHA_API_KEY")
        or os.getenv("BOCHAAI_API_KEY")
        or os.getenv("SERPER_API_KEY")
        or os.getenv("BING_SEARCH_API_KEY")
        or os.getenv("AZURE_BING_KEY")
    )


HUNT_REPORT_SCHEMA = (
    "{"
    '"mode":"hunt",'
    '"battlefield":{"tob_toc":"ToB|ToC|Both","online_offline":"online|offline|hybrid",'
    '"regulation":"strong|weak|mixed"},'
    '"phase1_macro":{"tech_forces":["..."],"population_policy_forces":["..."],'
    '"cross_tracks":[{"name":"...","why":"...","evidence_refs":["..."]}],'
    '"notes":"..."},'
    '"phase2_micro":{"selected_track":"...","misalignments":[{"type":"supply|cost|cognition","detail":"..."}],'
    '"dirty_hard_needs":[{"name":"...","why_avoided":"...","why_profitable":"..."}],'
    '"tech_vs_labor":[{"scene":"...","old_way":"...","new_way":"..."}],'
    '"pain_core":"...","audience_segment":"...","industry_niche":"...",'
    '"pain_level":"high|mid|low","painkiller_or_vitamin":"painkiller|vitamin|unclear"},'
    '"phase3_rebuild":{"pain_focus":"...","models":['
    '{"name":"...","shift":"工具→结果|重→轻|一次→订阅","canvas":{'
    '"customer":"...","value":"...","channel":"...","revenue":"...","cost":"...","key_resources":"..."},'
    '"unit_econ_sketch":{"price_cny":0,"cac_cny":0,"ltv_cny":0,"ltv_cac":0,"gross_margin":0,'
    '"payback_months":0,"assumptions":"..."},'
    '"leverage":"..."}],'
    '"preferred_model":"..."},'
    '"phase4_verdict":{"recommendation":"strongly_recommend|cautious_try|abandon",'
    '"reasons":["..."],"non_consensus":{"opportunity":"...","hidden_risk":"..."},'
    '"kill_factors":[{"risk":"...","plan_b":"..."}],'
    '"mvp":{"budget_cny":100,"steps":["..."],"success_metric":"...","stop_loss":"..."},'
    '"ltv_cac_check":"pass|fail|unknown"},'
    '"recommended_topic":"...","recommended_opportunity":"...","executive_summary":"..."}'
)


def _evidence_blob(hits: list[dict[str, Any]], limit: int = 18) -> str:
    """将搜索/热点结果压成证据文本。"""

    lines: list[str] = []
    for idx, h in enumerate(hits[:limit], start=1):
        lines.append(
            f"[{idx}] provider={h.get('provider')} heat={h.get('heat')}\n"
            f"title={h.get('title')}\nurl={h.get('url')}\n"
            f"snippet={(h.get('snippet') or '')[:400]}"
        )
    return "\n\n".join(lines) if lines else "(无外部证据，仅允许逻辑推演并标 unknown)"


async def _gather_hunt_evidence(
    *,
    direction: str,
    industry: str = "",
    limit: int = 12,
) -> dict[str, Any]:
    """
    采集狩猎证据（搜索 Key 优先，否则免费热点）。

    Args:
        direction: 模糊方向或空。
        industry: 行业。
        limit: 每路结果上限。

    Returns:
        {hits, sources_meta, errors}
    """

    hits: list[dict[str, Any]] = []
    errors: list[str] = []
    queries = []
    base = (direction or industry or "中国创业 AI 自动化 银发经济 宠物 合规").strip()
    queries = [
        f"{base} 创业机会 2024 2025",
        f"{base} 痛点 市场 增长",
        "中国 AI 应用落地 创业赛道",
        "银发经济 独居经济 政策 趋势",
    ]
    if _has_search_key():
        for q in queries[:3]:
            try:
                batch = await china_web_search(q, limit=min(limit, 8))
                for row in batch or []:
                    hits.append(
                        {
                            "title": row.get("title"),
                            "url": row.get("url"),
                            "snippet": row.get("snippet") or row.get("content") or "",
                            "provider": row.get("provider") or "china_web_search",
                            "heat": float(row.get("heat") or 0),
                        }
                    )
            except SearchConfigError as exc:
                errors.append(str(exc))
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"search:{exc}")
    try:
        pack = await collect_free_hotspots(topic=base, limit=limit)
        for row in (pack.get("items") or [])[:limit]:
            hits.append(
                {
                    "title": row.get("title"),
                    "url": row.get("url"),
                    "snippet": row.get("snippet") or "",
                    "provider": row.get("provider") or "hotspot",
                    "heat": float(row.get("heat") or 0),
                }
            )
        errors.extend(pack.get("errors") or [])
    except Exception as exc:  # noqa: BLE001
        errors.append(f"hotspot:{exc}")

    # 去重 URL
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for h in hits:
        url = str(h.get("url") or "")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        uniq.append(h)
    return {"hits": uniq[:40], "queries": queries, "errors": errors[:12]}


def _heuristic_hunt(direction: str, industry: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """模型失败时的保守狩猎降级（不编造精确市场规模）。"""

    d = (direction or industry or "中国可落地创业").strip()
    topic = f"{d} · 非共识切入（待验证）"
    return {
        "mode": "hunt",
        "battlefield": {
            "tob_toc": "Both",
            "online_offline": "hybrid",
            "regulation": "mixed",
        },
        "phase1_macro": {
            "tech_forces": ["AI/自动化", "软硬件协同"],
            "population_policy_forces": ["合规趋严", "人口结构变化"],
            "cross_tracks": [
                {"name": f"AI × {d}", "why": "技术降本叠加结构性需求", "evidence_refs": []},
            ],
            "notes": "启发式降级，需人工补证据",
        },
        "phase2_micro": {
            "selected_track": d,
            "misalignments": [{"type": "cost", "detail": "旧人工成本高，自动化可降维"}],
            "dirty_hard_needs": [
                {"name": "脏麻烦刚需场景", "why_avoided": "大众不愿碰", "why_profitable": "付费意愿可能更高"}
            ],
            "tech_vs_labor": [{"scene": d, "old_way": "人工", "new_way": "AI/自动化辅助"}],
            "pain_level": "mid",
            "painkiller_or_vitamin": "unclear",
        },
        "phase3_rebuild": {
            "pain_focus": d,
            "models": [
                {
                    "name": "卖结果(CPS)",
                    "shift": "工具→结果",
                    "canvas": {
                        "customer": "未知",
                        "value": "按成效收费",
                        "channel": "垂直渠道",
                        "revenue": "CPS/分润",
                        "cost": "获客+交付兜底",
                        "key_resources": "流程SOP+数据",
                    },
                    "unit_econ_sketch": {
                        "price_cny": 199,
                        "cac_cny": 80,
                        "ltv_cny": 600,
                        "ltv_cac": 7.5,
                        "gross_margin": 0.55,
                        "payback_months": 3,
                        "assumptions": "保守示意，非实测",
                    },
                    "leverage": "降低客户决策门槛",
                },
                {
                    "name": "轻资产订阅",
                    "shift": "一次→订阅",
                    "canvas": {
                        "customer": "未知",
                        "value": "持续服务",
                        "channel": "线上+伙伴",
                        "revenue": "SaaS/会员",
                        "cost": "云+客服",
                        "key_resources": "产品+内容",
                    },
                    "unit_econ_sketch": {
                        "price_cny": 99,
                        "cac_cny": 60,
                        "ltv_cny": 500,
                        "ltv_cac": 8.3,
                        "gross_margin": 0.6,
                        "payback_months": 2,
                        "assumptions": "保守示意，非实测",
                    },
                    "leverage": "复购与锁定",
                },
            ],
            "preferred_model": "卖结果(CPS)",
        },
        "phase4_verdict": {
            "recommendation": RECOMMEND_CAUTIOUS,
            "reasons": ["模型降级，证据不足", "需实测单位经济"],
            "non_consensus": {"opportunity": "unknown", "hidden_risk": "证据链不完整"},
            "kill_factors": [
                {"risk": "伪需求", "plan_b": "先人工验证付费再开发"},
                {"risk": "单位经济倒挂", "plan_b": "提价或换 CPS"},
                {"risk": "合规/平台拔管", "plan_b": "多渠道备份"},
            ],
            "mvp": {
                "budget_cny": 100,
                "steps": ["访谈10个目标用户", "人工流程跑通1单", "测算真实CAC"],
                "success_metric": "有人愿付非金钱成本或预付款",
                "stop_loss": "100元内无有效意向即停",
            },
            "ltv_cac_check": "unknown",
        },
        "recommended_topic": topic,
        "recommended_opportunity": topic,
        "executive_summary": f"针对「{d}」的狩猎报告已降级生成，请刷新或补证据后重跑。",
        "degraded": True,
        "evidence_count": len(evidence.get("hits") or []),
        "evidence_errors": evidence.get("errors") or [],
    }


async def hunt_opportunities(
    *,
    direction: str = "",
    industry: str = "",
    industry_name: str = "",
    theme_pack: str = "local-service-leadgen",
    llm: Optional[LLMClient] = None,
    tracer: Any = None,
) -> dict[str, Any]:
    """
    执行四阶段商机狩猎，输出《通用商机挖掘与深度验证报告》结构化 JSON。

    Args:
        direction: 模糊方向；空则全自动扫描。
        industry: 行业 id。
        industry_name: 行业中文名。
        theme_pack: 主题包。
        llm: 可选 LLM 客户端。
        tracer: 可选 NodeTracer。

    Returns:
        hunt_report 字典（含 recommended_topic 供下游研究）。
    """

    direction = (direction or "").strip()
    industry = (industry or "").strip()
    industry_name = (industry_name or "").strip() or industry

    async def _log(msg: str, stage: str) -> None:
        if tracer is not None and hasattr(tracer, "log"):
            await tracer.log(msg, stage=stage)

    await _log("狩猎引擎：采集宏观/微观证据…", "hunt_evidence")
    evidence = await _gather_hunt_evidence(direction=direction or industry_name, industry=industry_name)
    blob = _evidence_blob(evidence.get("hits") or [])

    client = llm or LLMClient()
    system = (
        "你是顶级风投背景的「商业机会猎手」与战略架构师。"
        "核心任务是主动狩猎隐藏在数据背后的非共识机会，不是被动验证陈词滥调。"
        "证据可能含中国独立开发者已上线产品：用作对标与细分灵感，禁止当融资事实。"
        "必须严格按四阶段输出："
        "1) 宏观趋势雷达：技术变量×人口/政策变量交叉，锁定2-3个高潜力赛道；"
        "2) 微观痛点探针：供需/成本/认知错位；脏麻烦刚需；新技术打旧人工；"
        "   并强制填写 pain_core / audience_segment / industry_niche（比行业名更细）；"
        "3) 商业模式重构：至少2种草案（卖结果/轻资产/订阅等），含模式画布与保守财务测算；"
        "4) 全链路验证与裁决：五维压力+专家质询，强制 LTV/CAC>3 才可强烈推荐。"
        "语言犀利、客观、数据驱动；禁止空泛理论；未知标 unknown，禁止编造精确融资额/牌照。"
        f"{EXPERT_CHAIN_FOR_OPPORTUNITY}"
        f"只输出 JSON：{HUNT_REPORT_SCHEMA}"
    )
    user = (
        f"theme_pack={theme_pack}; direction={direction or '(空=全自动狩猎)'}; "
        f"industry_id={industry}; industry_name={industry_name}; market=中国。\n"
        f"细分硬约束：audience_segment 禁止「大众/用户」；industry_niche 必须再细一级。\n"
        f"证据（真实抓取，必须引用 evidence_refs 序号或 URL）：\n{blob}\n"
        "请输出完整四阶段报告，并给出 recommended_topic（可直接作为下一阶段研究主题）与 "
        "recommended_opportunity、executive_summary。"
        "unit_econ_sketch 必须保守；ltv_cac<3 时 recommendation 不得为 strongly_recommend。"
    )

    await _log("狩猎引擎：四阶段推理…", "hunt_llm")
    model_name = ""
    try:
        if tracer is not None and hasattr(tracer, "llm_call"):
            await tracer.llm_call(
                model="(resolving)",
                route=ModelRoute.TIER_S.value,
                skills=["opportunity-hunt"],
                system=system,
                user=user,
                status="running",
                extra={"phase": "opportunity_hunt"},
            )
        payload, model_name, used_mock = await client.complete_json(
            route=ModelRoute.TIER_S,
            system=system,
            user=user,
            mock_payload=None,
            allow_mock=False,
            temperature=0.35,
        )
        if tracer is not None and hasattr(tracer, "llm_call"):
            await tracer.llm_call(
                model=model_name,
                route=ModelRoute.TIER_S.value,
                skills=["opportunity-hunt"],
                system=system,
                user=user,
                status="success" if not used_mock else "failed",
                extra={"used_mock": used_mock, "phase": "opportunity_hunt"},
            )
        if used_mock or not isinstance(payload, dict):
            raise RuntimeError("狩猎引擎禁止 mock 或非对象返回")
        report = payload
    except Exception as exc:  # noqa: BLE001
        await _log(f"狩猎 LLM 失败，启发式降级: {exc}", "hunt_fallback")
        report = _heuristic_hunt(direction, industry_name, evidence)
        report["error"] = str(exc)

    report = _normalize_hunt_report(report, direction=direction, industry_name=industry_name)
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["model"] = model_name
    report["evidence"] = {
        "hits": [
            {"title": h.get("title"), "url": h.get("url"), "provider": h.get("provider"), "heat": h.get("heat")}
            for h in (evidence.get("hits") or [])[:20]
        ],
        "queries": evidence.get("queries") or [],
        "errors": evidence.get("errors") or [],
    }
    report["source"] = "opportunity_hunter_v1"
    await _log(f"狩猎完成: {report.get('recommended_opportunity')}", "hunt_done")
    return report


def _normalize_hunt_report(
    report: dict[str, Any],
    *,
    direction: str,
    industry_name: str,
) -> dict[str, Any]:
    """规范化狩猎报告字段与裁决一致性。"""

    if not isinstance(report, dict):
        report = {}
    report.setdefault("mode", "hunt")
    p4 = report.get("phase4_verdict") if isinstance(report.get("phase4_verdict"), dict) else {}
    rec = str(p4.get("recommendation") or "").strip()
    if rec not in {RECOMMEND_STRONG, RECOMMEND_CAUTIOUS, RECOMMEND_ABANDON}:
        rec = RECOMMEND_CAUTIOUS
    # 强制 LTV/CAC 铁律
    models = []
    p3 = report.get("phase3_rebuild") if isinstance(report.get("phase3_rebuild"), dict) else {}
    if isinstance(p3.get("models"), list):
        models = [m for m in p3["models"] if isinstance(m, dict)]
    best_ltv_cac = None
    for m in models:
        ue = m.get("unit_econ_sketch") if isinstance(m.get("unit_econ_sketch"), dict) else {}
        try:
            ratio = float(ue.get("ltv_cac"))
            best_ltv_cac = ratio if best_ltv_cac is None else max(best_ltv_cac, ratio)
        except (TypeError, ValueError):
            continue
    if best_ltv_cac is not None and best_ltv_cac < 3 and rec == RECOMMEND_STRONG:
        rec = RECOMMEND_CAUTIOUS
        reasons = list(p4.get("reasons") or [])
        reasons.insert(0, f"LTV/CAC={best_ltv_cac:.2f}<3，降级为谨慎尝试")
        p4["reasons"] = reasons
        p4["ltv_cac_check"] = "fail"
    elif best_ltv_cac is not None:
        p4["ltv_cac_check"] = "pass" if best_ltv_cac >= 3 else "fail"
    else:
        p4.setdefault("ltv_cac_check", "unknown")
    p4["recommendation"] = rec
    report["phase4_verdict"] = p4

    topic = str(report.get("recommended_topic") or report.get("recommended_opportunity") or "").strip()
    if not topic:
        topic = (direction or industry_name or "中国非共识可验证商机").strip()
        report["recommended_topic"] = topic
    if not str(report.get("recommended_opportunity") or "").strip():
        report["recommended_opportunity"] = topic
    if not str(report.get("executive_summary") or "").strip():
        report["executive_summary"] = f"狩猎主题：{topic}；裁决：{rec}"
    return report
