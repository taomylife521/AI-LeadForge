# -*- coding: utf-8 -*-
"""
中国本土商机真实研究流水线。

作用: 商业搜索(可选) + 免Key热点(GitHub/HN/创投RSS) + 真抓页 + 大模型分析（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-24
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.tools.cn_search import SearchConfigError, SearchRequestError, build_china_search_queries, china_web_search
from app.tools.cyzone import analyze_cyzone_lead_clues, fetch_cyzone_projects
from app.tools.cid_indie import collect_cid_indie_clues
from app.tools.hotspot_sources import collect_free_hotspots
from app.tools.opportunity_score import enrich_opportunity_scores
from app.tools.web_fetch import FetchError, fetch_url_text


def has_commercial_search_key() -> bool:
    """是否配置了商业中文搜索 Key。"""

    return bool(
        os.getenv("BOCHA_API_KEY")
        or os.getenv("BOCHAAI_API_KEY")
        or os.getenv("SERPER_API_KEY")
        or os.getenv("BING_SEARCH_API_KEY")
        or os.getenv("AZURE_BING_KEY")
    )


async def research_china_opportunity(
    *,
    topic: str,
    industry_id: str = "",
    industry_name: str = "",
    theme_pack: str = "local-service-leadgen",
    llm: Optional[LLMClient] = None,
    search_limit: int = 6,
    fetch_limit: int = 4,
    prefer_free_hotspots: bool = True,
    tracer: Any = None,
) -> dict[str, Any]:
    """
    执行本土化真实商机研究。

    数据源优先级:
    1. 若有商业搜索 Key → Bocha/Serper/Bing
    2. 始终叠加（或在无 Key 时作为主源）GitHub Trending/Search + HN + 36氪创投 RSS
       + 创业邦 + 中国独立开发者项目（1c7）
    3. 真实抓页 + 大模型结构化（allow_mock=False）

    Args:
        tracer: 可选 NodeTracer，推送阶段日志与 LLM 提示词。

    Raises:
        RuntimeError: 无任何真实证据或模型失败。
    """

    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic 不能为空")

    async def _log(msg: str, stage: str = "") -> None:
        if tracer is not None:
            await tracer.log(msg, stage=stage or msg)

    client = llm or LLMClient()
    search_runs: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    source_mode: list[str] = []

    def _absorb(items: list[Any]) -> None:
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            hits.append(item)

    # —— 商业中文搜索（有 Key 才走）——
    if has_commercial_search_key():
        await _log("商业搜索中…", "commercial_search")
        queries = build_china_search_queries(topic=topic, industry_name=industry_name or industry_id)
        for query in queries[:3]:
            try:
                result = await china_web_search(query, limit=search_limit)
                search_runs.append(
                    {
                        "query": result["query"],
                        "provider": result["provider"],
                        "count": len(result["items"]),
                    }
                )
                _absorb(result["items"])
                source_mode.append(f"commercial:{result['provider']}")
            except (SearchConfigError, SearchRequestError) as exc:
                search_runs.append({"query": query, "provider": "commercial", "error": str(exc)})

    # —— 免 Key 热点：GitHub / HN / 创投 RSS / 创业邦 / 独立开发者 ——
    hotspot_pack: dict[str, Any] = {}
    cyzone_leads: dict[str, Any] = {}
    cyzone_pack: dict[str, Any] = {}
    cid_pack: dict[str, Any] = {}
    if prefer_free_hotspots or not hits:
        await _log("采集免 Key 热点（GitHub/HN/36氪/创业邦/独立开发者）…", "hotspots")
        try:
            hotspot_pack = await collect_free_hotspots(
                topic=topic,
                industry_name=industry_name or industry_id,
                limit=20,
            )
            _absorb(hotspot_pack.get("items") or [])
            source_mode.extend(list(hotspot_pack.get("sources_used") or []))
            search_runs.append(
                {
                    "query": f"hotspots:{topic}",
                    "provider": "free_hotspots",
                    "count": len(hotspot_pack.get("items") or []),
                    "sources_used": hotspot_pack.get("sources_used") or [],
                }
            )
            await _log(
                f"热点完成: {len(hotspot_pack.get('items') or [])} 条 / {(hotspot_pack.get('sources_used') or [])}",
                "hotspots_done",
            )
        except Exception as exc:  # noqa: BLE001
            search_runs.append({"query": "hotspots", "provider": "free_hotspots", "error": str(exc)})
            await _log(f"热点采集异常: {exc}", "hotspots_error")

        # 中国独立开发者：补充细分痛点/人群灵感（对标，非融资事件）
        await _log("中国独立开发者项目线索…", "cid_indie")
        try:
            cid_pack = await collect_cid_indie_clues(
                keyword=topic,
                industry=industry_name or industry_id,
                limit=12,
            )
            _absorb(cid_pack.get("hotspot_items") or [])
            if cid_pack.get("count"):
                source_mode.append("cid_indie")
            search_runs.append(
                {
                    "query": f"cid_indie:{topic}",
                    "provider": "cid_indie",
                    "count": cid_pack.get("count") or 0,
                    "parsed_total": cid_pack.get("parsed_total") or 0,
                }
            )
            if cid_pack.get("error"):
                await _log(f"CID 跳过: {cid_pack['error']}", "cid_indie_skip")
            else:
                await _log(f"CID 线索 {cid_pack.get('count') or 0} 条", "cid_indie_done")
        except Exception as exc:  # noqa: BLE001
            search_runs.append({"query": "cid_indie", "provider": "cid_indie", "error": str(exc)})
            await _log(f"CID 采集异常: {exc}", "cid_indie_error")

        # 创业邦线索：复用热点中的 cyzone 条目，避免二次全量抓取
        await _log("创业邦商机线索分析…", "cyzone_leads")
        try:
            cyzone_items = [
                h
                for h in hits
                if isinstance(h, dict)
                and (h.get("provider") == "cyzone" or "cyzone.cn" in str(h.get("url") or ""))
            ]
            if not cyzone_items:
                cyzone_pack = await fetch_cyzone_projects(
                    keyword=topic or industry_name,
                    limit=8,
                    enrich=True,
                )
                cyzone_items = list(cyzone_pack.get("items") or [])
                _absorb(cyzone_items)
            else:
                cyzone_pack = {
                    "source": "cyzone",
                    "channels_used": ["from_hotspots"],
                    "items": cyzone_items[:10],
                    "errors": [],
                }
            source_mode.append("cyzone")
            search_runs.append(
                {
                    "query": f"cyzone:{topic}",
                    "provider": "cyzone",
                    "count": len(cyzone_items),
                }
            )
            try:
                cyzone_leads = await analyze_cyzone_lead_clues(
                    topic=topic,
                    industry_name=industry_name or industry_id,
                    cyzone_pack=cyzone_pack,
                    llm=client,
                    limit=8,
                )
                await _log(
                    f"创业邦线索 {len(cyzone_leads.get('lead_clues') or [])} 条",
                    "cyzone_leads_done",
                )
            except Exception as lead_exc:  # noqa: BLE001
                search_runs.append({"query": "cyzone_leads", "provider": "cyzone", "error": str(lead_exc)})
                await _log(f"线索分析跳过: {lead_exc}", "cyzone_leads_skip")
        except Exception as exc:  # noqa: BLE001
            search_runs.append({"query": "cyzone", "provider": "cyzone", "error": str(exc)})
            await _log(f"创业邦采集异常: {exc}", "cyzone_error")

    if not hits:
        raise RuntimeError(
            "未获得任何真实证据。可配置 BOCHA/SERPER/BING，或检查 GitHub/HN/36氪 网络可达性。"
        )

    await _log(f"抓取证据页（最多 {fetch_limit}）…", "fetch_pages")

    def rank(row: dict[str, Any]) -> tuple:
        url = str(row.get("url") or "").lower()
        heat = float(row.get("heat") or 0)
        bonus = 0
        for host, w in (
            (".cn", 5),
            ("dianping", 6),
            ("meituan", 6),
            ("xiaohongshu", 5),
            ("36kr", 8),
            ("cyzone", 9),
            ("github.com", 4),
            ("cid_indie", 7),
            ("zhihu", 3),
            ("baidu", 2),
        ):
            if host in url or host == str(row.get("provider") or ""):
                bonus += w
        return (-(heat + bonus), url)

    fetch_targets = sorted(hits, key=rank)[:fetch_limit]
    pages: list[dict[str, Any]] = []
    fetch_errors: list[str] = []
    for item in fetch_targets:
        try:
            page = await fetch_url_text(item["url"], max_chars=5000)
            if page.get("ok"):
                pages.append(
                    {
                        "url": page["url"],
                        "title": page.get("title") or item.get("title") or "",
                        "text": page.get("text") or "",
                        "from_search_title": item.get("title") or "",
                        "snippet": item.get("snippet") or "",
                        "provider": item.get("provider") or "",
                        "heat": item.get("heat") or 0,
                    }
                )
            else:
                fetch_errors.append(f"{item['url']}: {page.get('error')}")
        except FetchError as exc:
            fetch_errors.append(str(exc))

    if not pages:
        pages = [
            {
                "url": h["url"],
                "title": h.get("title") or "",
                "text": h.get("snippet") or "",
                "from_search_title": h.get("title") or "",
                "snippet": h.get("snippet") or "",
                "provider": h.get("provider") or "",
                "heat": h.get("heat") or 0,
                "snippet_only": True,
            }
            for h in sorted(hits, key=rank)[:fetch_limit]
            if h.get("snippet") or h.get("title")
        ]
        if not pages:
            raise RuntimeError("抓取失败且热点无可用摘要: " + " | ".join(fetch_errors[:5]))

    evidence_blob = []
    for idx, page in enumerate(pages, start=1):
        evidence_blob.append(
            f"[{idx}] provider={page.get('provider')} heat={page.get('heat')}\n"
            f"title={page.get('title')}\nurl={page.get('url')}\n"
            f"snippet={page.get('snippet')}\nbody={(page.get('text') or '')[:1600]}"
        )

    system = (
        "你是中国市场「可落地商机」筛选官 + 顶级孵化器质询官，不是获客投放顾问。"
        "证据可能来自：创业邦项目/融资、中国独立开发者产品、GitHub、Hacker News、36氪、中文网页。"
        "禁止直接给建议：先定义战场(ToB/ToC,线上/线下,强/弱监管)，再做需求真伪与压力测试，最后裁决。"
        "目标：找出可靠、成功率高、可验证、可小步落地的商业机会；"
        "获客/落地页/投放只是商机定位准确之后的后续流程，本阶段不要以获客技巧当主结论。"
        "必须只依据证据；禁止编造融资额、下载量、未出现的公司名；未知标 unknown。"
        "综合成功率等权看重三项（缺一不可，都要打 0~1 分）："
        "A validate_ease=1–2周可验证且成本低；"
        "B willingness_to_pay=付费意愿强、客单价/付费方清晰；"
        "C competition_gap=竞争弱或有明确可切入缺口。"
        "每条商机必须写清「更精确的细分」："
        "pain_core=一句核心痛点；audience_segment=具体付费人群（勿用「用户/大众」）；"
        "industry_niche=行业内更细赛道（如「少儿教培·托管接送」而非笼统「教培」）；"
        "并判断痛点等级 pain_level=high|mid|low（止痛药/维生素），"
        "写清 current_alternative（忍受/Excel/成熟竞品）与 stop_loss_hint。"
        "可参考独立开发者对标产品做差异化，但禁止把开源项目直接当融资事实。"
        "只输出 JSON："
        '{"market":"中国","industry":"...","recommended":"...",'
        '"battlefield":{"tob_toc":"ToB|ToC|Both","online_offline":"online|offline|hybrid",'
        '"regulation":"strong|weak|mixed"},'
        '"selection_rationale":"为何在A/B/C与专家质询下更优",'
        '"recommendation":"strongly_recommend|cautious_try|abandon",'
        '"hotness":{"score":0.0,"rationale":"...","signals":["cyzone|cid_indie|github|rss|hn|..."]},'
        '"opportunities":[{"name":"...","pain_core":"...","pain":"...","pain_level":"high|mid|low",'
        '"painkiller_or_vitamin":"painkiller|vitamin|unclear",'
        '"audience_segment":"...","industry_niche":"...","current_alternative":"...","who_pays":"...","evidence":"...",'
        '"validate_ease":0.0,"willingness_to_pay":0.0,"competition_gap":0.0,'
        '"success_likelihood":0.0,"feasibility":0.0,"why_now":"...","validation_steps":["..."],'
        '"stop_loss_hint":"...","moat_hint":"...","kill_risks":["..."],'
        '"risks":["..."],"channel":"...","confidence":0.0,"source_urls":["..."],'
        '"cid_refs":["对标独立开发者产品名或URL"],'
        '"innovation_angle":"...","landing_note":"获客等落地动作延后到商机确认后"}],'
        '"lead_clues":[{"company":"...","event":"...","opportunity_angle":"...",'
        '"success_signal":"...","confidence":0.0,"source_url":"...",'
        '"pain_tags":[],"audience_tags":[],"industry_niches":[]}],'
        '"competitors":[{"name":"...","url":"...","positioning":"...","gap":"..."}],'
        '"trends":[{"title":"...","url":"...","heat_note":"...","direction":"rising|flat|declining|unknown"}],'
        '"expert_stress":{"investor_attacks":["..."],"giant_copy_path":"...","policy_kill_switch":"..."},'
        '"risks":["..."],"locale":"zh-CN"}'
    )
    user = (
        f"theme_pack={theme_pack}; topic={topic}; industry_id={industry_id}; "
        f"industry_name={industry_name}; locale=zh-CN; market=中国。\n"
        f"阶段目标=商机定位与筛选（非获客执行）；细分要精确到人群×痛点×赛道。\n"
        f"评分规则=A/B/C等权综合；推荐那条应是 composite 最高。\n"
        f"source_mode={source_mode}\nsearch_runs={search_runs}\n"
        f"创业邦已抽取线索(可参考合并，勿编造):\n{cyzone_leads.get('lead_clues') if cyzone_leads else []}\n"
        f"独立开发者对标线索(差异化灵感，勿当融资事实):\n{cid_pack.get('lead_clues') if cid_pack else []}\n"
        f"证据如下：\n" + "\n\n".join(evidence_blob)
    )

    await _log("调用大模型分析商机…", "llm_analyze")
    if tracer is not None:
        await tracer.llm_call(
            model="(resolving)",
            route=ModelRoute.TIER_S.value,
            skills=["opportunity-research"],
            system=system,
            user=user,
            status="running",
            extra={"phase": "opportunity_research"},
        )

    payload, model, used_mock = await client.complete_json(
        route=ModelRoute.TIER_S,
        system=system,
        user=user,
        mock_payload=None,
        allow_mock=False,
        temperature=0.4,
    )
    if tracer is not None:
        await tracer.llm_call(
            model=model,
            route=ModelRoute.TIER_S.value,
            skills=["opportunity-research"],
            system=system,
            user=user,
            status="success" if not used_mock else "failed",
            extra={"used_mock": used_mock, "phase": "opportunity_research"},
        )
    await _log(f"模型分析完成: {model}", "llm_analyze_done")
    if used_mock:
        raise RuntimeError("商机研究禁止 mock，但模型返回了 mock。")
    if not isinstance(payload, dict):
        raise RuntimeError("商机研究模型未返回 JSON 对象。")

    opportunities = payload.get("opportunities") if isinstance(payload.get("opportunities"), list) else []
    if not opportunities:
        raise RuntimeError("模型未产出 opportunities[]，拒绝空商机。")

    recommended = str(payload.get("recommended") or "").strip()
    if not recommended:
        first = opportunities[0] if isinstance(opportunities[0], dict) else {}
        recommended = str(first.get("name") or topic)

    opportunities = enrich_opportunity_scores(opportunities)
    if opportunities:
        top_name = str(opportunities[0].get("name") or "").strip()
        if top_name:
            recommended = top_name

    lead_clues = payload.get("lead_clues") if isinstance(payload.get("lead_clues"), list) else []
    if not lead_clues and cyzone_leads.get("lead_clues"):
        lead_clues = list(cyzone_leads["lead_clues"])
    # 合并 CID 对标线索（去重 company+url）
    if cid_pack.get("lead_clues"):
        seen_clue: set[str] = set()
        merged_clues: list[Any] = []
        for row in list(lead_clues) + list(cid_pack["lead_clues"]):
            if not isinstance(row, dict):
                continue
            key = f"{row.get('company')}|{row.get('source_url')}"
            if key in seen_clue:
                continue
            seen_clue.add(key)
            merged_clues.append(row)
        lead_clues = merged_clues[:24]

    return {
        "topic": topic,
        "theme_pack": theme_pack,
        "industry": {"id": industry_id, "name": industry_name},
        "locale": "zh-CN",
        "market": "中国",
        "phase": "opportunity_discovery",
        "score_policy": {
            "A": "validate_ease_1_2_weeks",
            "B": "willingness_to_pay",
            "C": "competition_gap",
            "weights": {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3},
        },
        "recommended": recommended,
        "selection_rationale": payload.get("selection_rationale") or "",
        "battlefield": payload.get("battlefield") if isinstance(payload.get("battlefield"), dict) else {},
        "recommendation": payload.get("recommendation") or "",
        "expert_stress": payload.get("expert_stress") if isinstance(payload.get("expert_stress"), dict) else {},
        "opportunities": opportunities,
        "lead_clues": lead_clues,
        "recommended_leads": cyzone_leads.get("recommended_leads") or [],
        "competitors": payload.get("competitors") if isinstance(payload.get("competitors"), list) else [],
        "trends": payload.get("trends") if isinstance(payload.get("trends"), list) else [],
        "hotness": payload.get("hotness") if isinstance(payload.get("hotness"), dict) else {},
        "risks": payload.get("risks") if isinstance(payload.get("risks"), list) else [],
        "research": {
            "mode": source_mode,
            "queries": search_runs,
            "search_hits": [
                {
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "provider": h.get("provider"),
                    "heat": h.get("heat"),
                }
                for h in hits[:25]
            ],
            "pages": [
                {
                    "url": p.get("url"),
                    "title": p.get("title"),
                    "snippet_only": bool(p.get("snippet_only")),
                    "chars": len(p.get("text") or ""),
                    "heat": p.get("heat"),
                    "provider": p.get("provider"),
                }
                for p in pages
            ],
            "fetch_errors": fetch_errors[:10],
            "hotspot_errors": (hotspot_pack or {}).get("errors") or [],
            "cyzone": {
                "channels": (cyzone_leads.get("cyzone") or {}).get("channels_used"),
                "lead_count": len([c for c in lead_clues if isinstance(c, dict) and c.get("event") != "cid_indie_project"]),
            },
            "cid_indie": {
                "count": cid_pack.get("count") or 0,
                "parsed_total": cid_pack.get("parsed_total") or 0,
                "source": cid_pack.get("source") or "",
            },
            "model": model,
            "commercial_search": has_commercial_search_key(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "china_real_research+cyzone+cid_indie+free_hotspots",
            "goal": "find_reliable_actionable_opportunity",
        },
        "sources": [p.get("url") for p in pages if p.get("url")],
    }
