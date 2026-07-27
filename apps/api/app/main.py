# -*- coding: utf-8 -*-
"""
LeadForge FastAPI 入口。

作用: 可视化控制台 + Agnes/自定义模型接入 + 运行过程 SSE + HITL/Trace API。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent_bindings import (
    get_agent_binding,
    list_mcp_catalog,
    list_rule_catalog,
    list_skill_catalog,
    load_bindings,
    reset_agent_binding,
    update_agent_binding,
)
from app.envelope import ModelRoute, new_trace_id
from app.llm import LLMClient
from app.memory.store import MemoryStore
from app.node_runner import NodeRunner
from app.orchestrator import Orchestrator
from app.progress import progress_bus
from app.providers import (
    add_custom_provider,
    any_llm_key_present,
    delete_custom_provider,
    get_free_catalog_item,
    get_profile,
    load_all_profiles,
    load_custom_providers,
    load_free_llm_catalog,
    mask_provider,
    reload_free_llm_catalog,
)
from app.runtime_keys import apply_runtime_keys_to_environ, save_runtime_key
from app.tools.decision_brief import build_decision_brief, calc_unit_economics
from app.tools.opportunity_hunter import hunt_opportunities
from app.run_control import RunCancelled, run_control
from app.settings import (
    data_dir,
    env_present,
    get_active_profile_id,
    get_active_routes,
    get_settings,
    list_theme_packs,
    load_skills_allowlist,
    set_active_profile_id,
)
from app.theme_recommend import (
    list_all_industries,
    list_cn_vc_market_industries,
    list_industries,
    recommend_topics,
    save_custom_industry,
)
from app.tools.cn_search import SearchConfigError
from app.tools.cid_indie import collect_cid_indie_clues
from app.tools.cyzone import analyze_cyzone_lead_clues, fetch_cyzone_projects
from app.tools.hotspot_sources import collect_free_hotspots
from app.tools.hotspot_opportunity import (
    extract_opportunities_from_hotspots,
    group_hotspots_by_platform,
    hotspot_to_opportunity_pack,
)
from app.tools.hotspot_lanes import build_hotspot_lanes, flatten_lane_items
from app.tools.hotspot_warehouse import (
    fetch_hotspots_cached,
    is_warehouse_stale,
    query_hotspots,
    warm_hotspot_warehouse,
    warehouse_ready,
    warehouse_stats,
)
from app.tools.sync_logs import list_sync_logs, load_scheduler_config, save_scheduler_config
from app.tools.sync_scheduler import (
    github_token_status,
    run_incremental_sync,
    scheduler_status,
    start_background_scheduler,
)
from app.tools.opportunity_research import has_commercial_search_key, research_china_opportunity
from app.tools.project_library import library_stats
from app.tools.project_recommend import recommend_landing_projects
from app.tools.project_theme import (
    ai_infer_theme_from_project,
    fetch_github_readme,
    infer_theme_from_project,
    theme_and_portfolio_from_project,
)
from app.tools.landing_plan import generate_landing_plan, portfolio_and_plan_for_opportunity
from app.tools.landable_filter import landable_portfolio_for_opportunity
from app.tools.trendradar_client import check_trendradar, fetch_trendradar_hotspots
from app.tools.paperclip_client import (
    PaperclipNotConfigured,
    check_paperclip,
    handoff_landing,
    list_issues as list_paperclip_issues,
)
from app.tools.embed_proxy import (
    paperclip_web_base,
    proxy_embed,
    trendradar_web_base,
)
from app.tools.landing_tasks import (
    add_landing_child,
    create_landing_task,
    delete_landing_child,
    delete_landing_task,
    get_landing_task,
    list_landing_tasks,
    update_landing_child,
    update_landing_task,
)
from app.tools.pitchhub_36kr import crawl_pitchhub_all, fetch_pitchhub_projects
from app.harness import get_harness_job, list_harness_jobs, start_harness_job

from app.workflow_graph import load_graph, set_node, update_node_config, update_node_inputs
from app.workflow_templates import (
    delete_workflow_template,
    list_workflow_templates,
    load_workflow_template,
    save_workflow_template,
)

app = FastAPI(title="LeadForge", version="0.3.0")
store = MemoryStore()
orchestrator = Orchestrator(store)
node_runner = NodeRunner(store)
settings = get_settings()
# 启动时注入控制台已保存的 API Key
apply_runtime_keys_to_environ()


@app.on_event("startup")
async def _seed_methodology_projects() -> None:
    """
    将 SeekMoney / 一人企业方法论仓库写入项目库（幂等）。
    """

    async def _job() -> None:
        try:
            await asyncio.sleep(1)
            from app.tools.methodology_playbooks import METHODOLOGY_PROJECTS
            from app.tools.project_library import upsert_projects

            stats = upsert_projects(list(METHODOLOGY_PROJECTS))
            print(f"[leadforge] methodology projects seeded: {stats}")
        except Exception as exc:  # noqa: BLE001
            print(f"[leadforge] methodology seed skipped: {exc}")

    asyncio.create_task(_job())


@app.on_event("startup")
async def _warm_project_library_if_empty() -> None:
    """
    库为空时后台预热一批项目（不阻塞启动；失败仅打日志）。
    """

    async def _job() -> None:
        try:
            stats = library_stats()
            if int(stats.get("count") or 0) >= 12:
                return
            await asyncio.sleep(2)
            await recommend_landing_projects(
                topic="SaaS AI 创业 本地服务",
                industry="",
                limit=24,
                mode="projects",
                use_cache=False,
                force_refresh=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[leadforge] project library warm skipped: {exc}")

    asyncio.create_task(_job())


@app.on_event("startup")
async def _warm_hotspot_warehouse_if_stale() -> None:
    """
    热点库为空或过期时后台预热（不阻塞启动）。
    """

    async def _job() -> None:
        try:
            if warehouse_ready(min_count=20) and not is_warehouse_stale():
                return
            await asyncio.sleep(3)
            result = await warm_hotspot_warehouse(limit_per_source=12, include_trendradar=True)
            print(
                f"[leadforge] hotspot warehouse warm: "
                f"total={result.get('upsert', {}).get('total')} "
                f"added={result.get('upsert', {}).get('added')}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[leadforge] hotspot warehouse warm skipped: {exc}")

    asyncio.create_task(_job())


@app.on_event("startup")
async def _start_incremental_sync_scheduler() -> None:
    """启动创投/GitHub/热搜增量同步定时任务。"""

    try:
        start_background_scheduler()
        print("[leadforge] incremental sync scheduler started")
    except Exception as exc:  # noqa: BLE001
        print(f"[leadforge] sync scheduler failed to start: {exc}")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MEDIA_DIR = data_dir() / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


class StartRunRequest(BaseModel):
    """启动商业闭环请求。"""

    topic: str = ""
    theme_pack: str = "local-service-leadgen"
    industry: str = ""
    force_bad_copy: bool = False
    workflow_template: str = "default-closed-loop"


class ResearchOpportunityRequest(BaseModel):
    """中国本土商机真实研究请求。"""

    topic: str = ""
    theme_pack: str = "local-service-leadgen"
    industry: str = ""
    industry_name: str = ""


class HuntOpportunityRequest(BaseModel):
    """四阶段商机狩猎请求（可空主题）。"""

    direction: str = ""
    theme_pack: str = "local-service-leadgen"
    industry: str = ""
    industry_name: str = ""


class ProfileSwitchRequest(BaseModel):
    """模型档案切换请求。"""

    profile_id: str
    model: str = ""


class HitlDecisionRequest(BaseModel):
    """人工审批请求。"""

    approve: bool = True
    note: str = ""
    force_bad_copy: bool = False
    spend_cny: float = Field(default=50.0, ge=0, le=10000)
    budget_cap_test_cny: Optional[float] = Field(default=None, ge=0, le=10000)
    business_model_patch: Optional[dict[str, Any]] = None


class UnitEconomicsCalcRequest(BaseModel):
    """单位经济动态测算请求。"""

    price_cny: Optional[float] = None
    ad_spend: Optional[float] = None
    sales_commission: Optional[float] = None
    channel_rebate: Optional[float] = None
    demo_labor_cost: Optional[float] = None
    delivery_labor_hours: Optional[float] = None
    labor_hourly_cny: Optional[float] = None
    bad_case_rate: Optional[float] = None
    monthly_fixed_cost: Optional[float] = None
    rd_amortization_cny: Optional[float] = None
    monthly_orders: Optional[float] = None
    funding_cost_per_order: Optional[float] = None
    bad_debt_reserve_rate: Optional[float] = None
    ltv_months: Optional[float] = None
    gross_margin: Optional[float] = None
    test_budget_cny: Optional[float] = None


class DecisionBriefRefreshRequest(BaseModel):
    """刷新 HITL 决策穿透简报。"""

    opportunity: Optional[dict[str, Any]] = None
    business_model: Optional[dict[str, Any]] = None
    redteam: Optional[dict[str, Any]] = None
    persist: bool = True


class NodeConfigUpdate(BaseModel):
    """节点级配置：模型 / Skill / 提示词 / 替换 agent。"""

    agent_key: Optional[str] = None
    label: Optional[str] = None
    model: Optional[dict[str, Any]] = None
    skills: Optional[list[str]] = None
    rules: Optional[list[str]] = None
    mcp: Optional[list[Any]] = None
    prompt: Optional[str] = None
    extra_system: Optional[str] = None


class CustomProviderRequest(BaseModel):
    """可视化接入自定义模型。"""

    label: str
    api_base: str
    api_key: str
    model: str
    description: str = ""
    id: str = ""


class NodeInputUpdate(BaseModel):
    """更新节点多模态输入。"""

    inputs: list[dict[str, Any]]


class NodeRerunRequest(BaseModel):
    """节点重跑请求。"""

    cascade: bool = False
    generate_visuals: bool = True


class AgentBindingPatch(BaseModel):
    """Agent 动态绑定补丁。"""

    label: Optional[str] = None
    model: Optional[dict[str, Any]] = None
    skills: Optional[list[str]] = None
    rules: Optional[list[str]] = None
    mcp: Optional[list[Any]] = None
    prompt: Optional[str] = None
    extra_system: Optional[str] = None


@app.get("/")
async def index() -> FileResponse:
    """可视化控制台首页。"""

    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(404, "控制台静态文件缺失")
    return FileResponse(index_path)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    """健康检查。"""

    return {
        "ok": True,
        "service": "leadforge-api",
        "profile": get_active_profile_id(),
        "has_llm_key": any_llm_key_present(),
    }


@app.get("/api/system/status")
async def system_status() -> dict[str, Any]:
    """聚合依赖服务状态。"""

    async def ping(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                r = await client.get(url)
                return r.status_code < 500
        except Exception:  # noqa: BLE001
            return False

    litellm_ok = await ping(f"{settings.litellm_base_url.rstrip('/')}/health/liveliness")
    if not litellm_ok:
        litellm_ok = await ping(f"{settings.litellm_base_url.rstrip('/')}/health")
    qdrant_ok = await ping(f"{settings.qdrant_url.rstrip('/')}/readyz")
    agnes_ready = bool(os.getenv("AGNES_API_KEY") or os.getenv("AGNES_TOKEN"))

    return {
        "api": True,
        "litellm": litellm_ok,
        "qdrant": qdrant_ok,
        "agnes": agnes_ready,
        "active_profile": get_active_profile_id(),
        "routes": get_active_routes(),
        "theme_packs": list_theme_packs(),
        "mock_llm": settings.mock_llm or not any_llm_key_present(),
        "has_llm_key": any_llm_key_present(),
        "china_search": {
            "bocha": bool(os.getenv("BOCHA_API_KEY") or os.getenv("BOCHAAI_API_KEY")),
            "serper": bool(os.getenv("SERPER_API_KEY")),
            "bing": bool(os.getenv("BING_SEARCH_API_KEY") or os.getenv("AZURE_BING_KEY")),
            "ready": has_commercial_search_key(),
            "free_hotspots": True,
            "free_sources": [
                "github_trending",
                "github_search",
                "hackernews",
                "36kr_rss",
                "cyzone",
            ],
            "cyzone": True,
        },
    }


@app.get("/api/models/profiles")
async def list_profiles() -> dict[str, Any]:
    """列出可一键切换的模型档案（含免费目录 / 自定义）。"""

    active = get_active_profile_id()
    items = []
    for profile in load_all_profiles():
        required = profile.get("required_env") or []
        presence = env_present(required) if required else {}
        env_ready = all(presence.values()) if required else True
        if profile.get("custom"):
            env_ready = bool(profile.get("has_api_key"))
        items.append(
            {
                **{k: v for k, v in profile.items() if k != "api_key"},
                "active": profile["id"] == active,
                "env_ready": env_ready,
                "env_presence": presence,
            }
        )
    catalog = load_free_llm_catalog()
    return {
        "active_profile": active,
        "profiles": items,
        "routes": get_active_routes(),
        "custom_providers": [mask_provider(p) for p in load_custom_providers()],
        "free_catalog_source": catalog.get("source") or "",
        "free_catalog_note": catalog.get("source_note") or "",
    }


@app.get("/api/models/free-catalog")
async def free_catalog() -> dict[str, Any]:
    """
    返回 cheahjs/free-llm-api-resources 精选免费/试用 OpenAI 兼容资源。

    控制台用于按厂商选模型并一键启用。
    """

    catalog = load_free_llm_catalog()
    items = []
    for raw in catalog.get("providers") or []:
        required = list(raw.get("required_env") or [])
        presence = env_present(required) if required else {}
        items.append(
            {
                **raw,
                "env_ready": all(presence.values()) if required else False,
                "env_presence": presence,
            }
        )
    return {
        "source": catalog.get("source"),
        "source_note": catalog.get("source_note"),
        "updated_note": catalog.get("updated_note"),
        "providers": items,
        "count": len(items),
    }


@app.post("/api/models/free-catalog/reload")
async def free_catalog_reload() -> dict[str, Any]:
    """
    重新加载 config/free_llm_catalog.json（清除进程内缓存）。

    编辑目录文件后无需重启 API。
    """

    catalog = reload_free_llm_catalog()
    return {
        "ok": True,
        "source": catalog.get("source"),
        "count": len(catalog.get("providers") or []),
        "provider_ids": [p.get("id") for p in (catalog.get("providers") or [])],
    }


@app.post("/api/models/profiles/switch")
async def switch_profile(body: ProfileSwitchRequest) -> dict[str, Any]:
    """一键切换大模型档案（免费目录可附带具体 model）。"""

    try:
        payload = set_active_profile_id(body.profile_id, model_override=body.model or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "active": payload}


@app.post("/api/models/custom")
async def create_custom_model(body: CustomProviderRequest) -> dict[str, Any]:
    """可视化接入其他 OpenAI 兼容模型。"""

    try:
        item = add_custom_provider(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # 自动切换到新接入模型
    set_active_profile_id(item["id"])
    return {"ok": True, "provider": item, "active_profile": item["id"]}


@app.delete("/api/models/custom/{provider_id}")
async def remove_custom_model(provider_id: str) -> dict[str, Any]:
    """删除自定义模型。"""

    ok = delete_custom_provider(provider_id)
    if not ok:
        raise HTTPException(404, "自定义模型不存在")
    if get_active_profile_id() == provider_id:
        set_active_profile_id("agnes-free")
    return {"ok": True}


@app.post("/api/models/test")
async def test_active_model() -> dict[str, Any]:
    """对当前激活模型做一次连通性探测。"""

    llm = LLMClient()
    payload, model, used_mock = await llm.complete_json(
        route=ModelRoute.TIER_XS,
        system="你是连通性测试助手。",
        user='请返回 JSON：{"ok":true,"echo":"leadforge"}',
        mock_payload={"ok": True, "echo": "mock"},
    )
    return {"ok": True, "model": model, "used_mock": used_mock, "payload": payload}


@app.get("/api/skills")
async def skills() -> dict[str, Any]:
    """Skill 白名单。"""

    return load_skills_allowlist()


@app.get("/api/themes")
async def themes() -> dict[str, Any]:
    """主题包列表。"""

    return {"items": list_theme_packs()}


@app.get("/api/themes/industries")
async def themes_industries(
    theme_pack: str = "local-service-leadgen",
    include_all: bool = True,
    catalog: str = "",
) -> dict[str, Any]:
    """
    列出行业下拉选项。

    catalog=cn_vc：创投标准所属行业（看热点默认）；
    否则按 Theme Pack / 全行业汇总。
    """

    pack = theme_pack or settings.default_theme_pack
    cat = (catalog or "").strip().lower()
    if cat in ("cn_vc", "cn-market", "market", "hotspot"):
        items = list_cn_vc_market_industries()
        return {
            "theme_pack": pack,
            "catalog": "cn_vc",
            "items": items,
            "allow_custom": True,
            "all": False,
            "preset": [{"id": "", "name": "不限", "hint": "全网，不限定所属行业"}],
        }
    items = list_all_industries(pack) if include_all else list_industries(pack)
    return {
        "theme_pack": pack,
        "catalog": "theme_pack",
        "items": items,
        "allow_custom": True,
        "all": include_all,
        "preset": [{"id": "", "name": "全行业", "hint": "不限定行业，跨赛道狩猎"}],
    }


class CustomIndustryRequest(BaseModel):
    """自定义行业。"""

    theme_pack: str = "local-service-leadgen"
    name: str
    hint: str = ""


@app.post("/api/themes/industries/custom")
async def themes_industries_custom(body: CustomIndustryRequest) -> dict[str, Any]:
    """保存用户自定义行业，供后续建议列表使用。"""

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "行业名必填")
    try:
        item = save_custom_industry(body.theme_pack or settings.default_theme_pack, name, body.hint or "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "item": item, "items": list_industries(body.theme_pack or settings.default_theme_pack)}


@app.get("/api/research/hotspots")
async def research_hotspots(
    topic: str = "",
    industry: str = "",
    channel: str = "",
    source: str = "auto",
    region: str = "",
    heat_bucket: str = "",
    heat_min: float = 0.0,
    limit: int = 20,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    热点列表：默认读预热仓库（秒级）；refresh=true 时实时采集并回写。

    仍为真实公开数据，不做 mock。source 兼容旧参数 auto|trendradar|free（仅影响强制刷新路径）。
    """

    try:
        return await fetch_hotspots_cached(
            topic=topic,
            industry=industry,
            channel=channel,
            source="" if (source or "").strip().lower() in ("auto", "free", "trendradar", "") else source,
            region=region,
            heat_bucket=heat_bucket,
            heat_min=heat_min,
            limit=limit,
            use_cache=bool(use_cache) and not bool(refresh),
            refresh=bool(refresh),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"热点采集失败: {exc}") from exc


@app.get("/api/research/hotspots/library")
async def research_hotspots_library() -> dict[str, Any]:
    """热点库聚合：按行业/渠道/来源/地区/热度分桶。"""

    return warehouse_stats()


@app.post("/api/research/hotspots/warm")
async def research_hotspots_warm(limit_per_source: int = 12) -> dict[str, Any]:
    """手动预热一批多行业热点进入仓库。"""

    try:
        return await warm_hotspot_warehouse(
            limit_per_source=max(6, min(int(limit_per_source or 12), 24)),
            include_trendradar=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"热点预热失败: {exc}") from exc


@app.get("/api/research/sync/status")
async def research_sync_status() -> dict[str, Any]:
    """定时同步调度状态 + GitHub Token 是否已配置。"""

    return scheduler_status()


@app.get("/api/research/sync/logs")
async def research_sync_logs(limit: int = 40, kind: str = "") -> dict[str, Any]:
    """查看抓取同步日志（耗时、数量、错误）。"""

    return list_sync_logs(limit=max(1, min(int(limit or 40), 100)), kind=kind or "")


class SyncConfigRequest(BaseModel):
    """定时同步配置。"""

    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    sources: Optional[list[str]] = None


@app.get("/api/research/sync/config")
async def research_sync_config_get() -> dict[str, Any]:
    """读取定时同步配置。"""

    return {"ok": True, "config": load_scheduler_config(), "github": github_token_status()}


@app.put("/api/research/sync/config")
async def research_sync_config_put(body: SyncConfigRequest) -> dict[str, Any]:
    """更新定时同步开关、间隔与数据源。"""

    cfg = save_scheduler_config(
        enabled=body.enabled,
        interval_minutes=body.interval_minutes,
        sources=body.sources,
    )
    return {"ok": True, "config": cfg}


class SyncRunRequest(BaseModel):
    """手动触发增量同步。"""

    sources: Optional[list[str]] = None
    include_github: bool = True
    include_newsnow: bool = True


@app.post("/api/research/sync/run")
async def research_sync_run(body: SyncRunRequest | None = None) -> dict[str, Any]:
    """
    手动触发一轮增量抓取（36氪/创业邦/独立开发/GitHub/热搜）。
    """

    req = body or SyncRunRequest()
    try:
        return await run_incremental_sync(
            trigger="manual",
            sources=req.sources,
            include_github=bool(req.include_github),
            include_newsnow=bool(req.include_newsnow),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"同步失败: {exc}") from exc


@app.get("/api/research/github/status")
async def research_github_status() -> dict[str, Any]:
    """GitHub Token 配置状态。"""

    return {"ok": True, **github_token_status()}


@app.get("/api/research/hotspots/by-platform")
async def research_hotspots_by_platform(
    per_platform: int = 30,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    按平台分页签返回热点（微博/知乎/百度等）。

    默认读热点库中的全网热榜；refresh=true 时先同步最新再分组。
    """

    try:
        if refresh or not warehouse_ready(min_count=8):
            await fetch_hotspots_cached(
                topic="创业",
                channel="trendradar",
                region="cn",
                limit=40,
                use_cache=False,
                refresh=True,
            )
        # 从仓库取全网热榜 + 创投，再按平台分组
        rows = query_hotspots(channel="trendradar", region="cn", limit=120)
        if len(rows) < 8:
            rows = query_hotspots(region="cn", limit=120)
            rows = [
                r for r in rows
                if str(r.get("channel") or "") not in ("github", "hackernews")
            ]
        grouped = group_hotspots_by_platform(rows, per_platform=max(10, min(per_platform, 50)))
        return {
            "ok": True,
            "from_cache": not refresh,
            **grouped,
            "stats": warehouse_stats(),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"平台热点加载失败: {exc}") from exc


@app.get("/api/research/hotspots/lanes")
async def research_hotspots_lanes(
    industry: str = "",
    per_platform: int = 30,
    refresh: bool = False,
    include_ai: bool = False,
) -> dict[str, Any]:
    """
    多维热点通道：平台热搜 / SeekMoney 商机线索 / 创投 / 行业痛点 / AI 重构。

    默认 include_ai=false：用中国本土热搜/创投 + GitHub 全网按规则填满三通道；
    include_ai=true 时再用 LLM 加深（可选）。
    平台热搜默认每平台前 30 名，按名次排序。
    """

    try:
        return await build_hotspot_lanes(
            industry=industry,
            per_platform=max(10, min(int(per_platform or 30), 50)),
            refresh=bool(refresh),
            include_ai_lanes=bool(include_ai),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"多维热点加载失败: {exc}") from exc


class HotspotOpportunityRequest(BaseModel):
    """热点一键生成商机与可落地项目。"""

    hotspots: list[dict[str, Any]] = Field(default_factory=list)
    industry: str = ""
    theme_pack: str = ""
    generate_plan: bool = True
    use_ai: bool = True
    opportunity_title: str = ""


class HotspotExtractRequest(BaseModel):
    """从多平台热点 AI 提炼可落地商机。"""

    hotspots: list[dict[str, Any]] = Field(default_factory=list)
    industry: str = ""
    theme_pack: str = ""
    limit: int = 5
    use_selected_only: bool = False
    platforms: list[str] = Field(default_factory=list)


@app.post("/api/research/hotspot-opportunities")
async def research_hotspot_opportunities(body: HotspotExtractRequest) -> dict[str, Any]:
    """AI：根据各平台热点提炼可靠可落地商机列表。"""

    try:
        rows = list(body.hotspots or [])
        if not rows:
            # 未传热点时，自动取各平台热榜
            pack = await research_hotspots_by_platform(per_platform=10, refresh=False)
            for plat in pack.get("platforms") or []:
                if body.platforms and plat.get("id") not in body.platforms:
                    continue
                rows.extend(plat.get("items") or [])
        return await extract_opportunities_from_hotspots(
            hotspots=rows,
            industry=body.industry,
            theme_pack=body.theme_pack or settings.default_theme_pack,
            limit=max(2, min(int(body.limit or 5), 8)),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"商机提炼失败: {exc}") from exc


@app.post("/api/research/hotspot-opportunity")
async def research_hotspot_opportunity(body: HotspotOpportunityRequest) -> dict[str, Any]:
    """热点 → 商机主题 → 仅可落地项目（+ 可选落地方案）。"""

    try:
        return await hotspot_to_opportunity_pack(
            hotspots=body.hotspots,
            industry=body.industry,
            theme_pack=body.theme_pack or settings.default_theme_pack,
            generate_plan=body.generate_plan,
            use_ai=body.use_ai,
            opportunity_title=body.opportunity_title or "",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"热点商机生成失败: {exc}") from exc


@app.get("/api/integrations/status")
async def integrations_status() -> dict[str, Any]:
    """热点服务 / 任务中心 连通性（面向客户状态，不暴露实现细节）。"""

    tr = await check_trendradar()
    pc = await check_paperclip()
    # 热点：TrendRadar 或免费源均可视为「可用」
    hot_ok = bool(tr.get("ok"))
    # 任务中心：Paperclip 或本地任务均可
    task_ok = True
    return {
        "hotspots": {
            "ok": True,
            "enhanced": hot_ok,
            "label": "增强源已连接" if hot_ok else "标准热点源",
        },
        "tasks": {
            "ok": task_ok,
            "enhanced": bool(pc.get("ok") and pc.get("configured")),
            "label": "协同任务已连接" if (pc.get("ok") and pc.get("configured")) else "本地任务中心",
        },
        "trendradar": tr,
        "paperclip": pc,
    }


@app.get("/api/paperclip/issues")
async def paperclip_issues(limit: int = 30, q: str = "", status: str = "") -> dict[str, Any]:
    """列出落地任务：默认本地任务中心（支持增删查改）；外部协同仅作增强。"""

    # 本地 CRUD 为产品主路径；关键词/状态筛选走本地存储
    local = list_landing_tasks(limit=limit, q=q, status=status)
    if local.get("items") or q or status:
        return local
    try:
        remote = await list_paperclip_issues(limit=limit)
        if remote.get("items"):
            return remote
    except PaperclipNotConfigured:
        pass
    except Exception:  # noqa: BLE001
        pass
    return local


class LandingTaskCreateRequest(BaseModel):
    """手动创建落地任务。"""

    topic: str = Field(..., min_length=1, max_length=200)
    title: str = ""
    industry: str = ""
    context: str = ""
    plan_markdown: str = ""
    children: list[dict[str, Any]] = Field(default_factory=list)
    reuse_same_title: bool = False


class LandingTaskUpdateRequest(BaseModel):
    """更新落地任务字段。"""

    title: Optional[str] = None
    topic: Optional[str] = None
    industry: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    context: Optional[str] = None
    plan_markdown: Optional[str] = None


class LandingChildCreateRequest(BaseModel):
    """新增子任务。"""

    title: str = Field(..., min_length=1, max_length=120)
    status: str = "todo"


class LandingChildUpdateRequest(BaseModel):
    """更新子任务。"""

    title: Optional[str] = None
    status: Optional[str] = None


@app.get("/api/landing-tasks")
async def api_list_landing_tasks(
    limit: int = 40,
    q: str = "",
    status: str = "",
) -> dict[str, Any]:
    """列出本地落地任务（查）。"""

    return list_landing_tasks(limit=limit, q=q, status=status)


@app.get("/api/landing-tasks/{task_id}")
async def api_get_landing_task(task_id: str) -> dict[str, Any]:
    """获取单条落地任务。"""

    task = get_landing_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": task, "source": "local"}


@app.post("/api/landing-tasks")
async def api_create_landing_task(body: LandingTaskCreateRequest) -> dict[str, Any]:
    """创建落地任务（增）。"""

    try:
        return create_landing_task(
            topic=body.topic,
            title=body.title,
            industry=body.industry,
            context=body.context,
            plan_markdown=body.plan_markdown,
            children=body.children or None,
            reuse_same_title=bool(body.reuse_same_title),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/landing-tasks/{task_id}")
async def api_update_landing_task(task_id: str, body: LandingTaskUpdateRequest) -> dict[str, Any]:
    """更新落地任务（改）。"""

    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有可更新字段")
    try:
        return update_landing_task(task_id, patch)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/landing-tasks/{task_id}")
async def api_delete_landing_task(task_id: str) -> dict[str, Any]:
    """删除落地任务（删）。"""

    try:
        return delete_landing_task(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/landing-tasks/{task_id}/children")
async def api_add_landing_child(task_id: str, body: LandingChildCreateRequest) -> dict[str, Any]:
    """新增子任务。"""

    try:
        return add_landing_child(task_id, title=body.title, status=body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.patch("/api/landing-tasks/{task_id}/children/{child_id}")
async def api_update_landing_child(
    task_id: str,
    child_id: str,
    body: LandingChildUpdateRequest,
) -> dict[str, Any]:
    """更新子任务标题/状态。"""

    try:
        return update_landing_child(
            task_id,
            child_id,
            title=body.title,
            status=body.status,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/landing-tasks/{task_id}/children/{child_id}")
async def api_delete_landing_child(task_id: str, child_id: str) -> dict[str, Any]:
    """删除子任务。"""

    try:
        return delete_landing_child(task_id, child_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.api_route("/embed/trendradar", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.api_route("/embed/trendradar/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def embed_trendradar(request: Request, path: str = "") -> Any:
    """同域嵌入 TrendRadar Web（可选增强源）。"""

    return await proxy_embed(request, upstream_base=trendradar_web_base(), path=path)


@app.api_route("/embed/paperclip", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.api_route("/embed/paperclip/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def embed_paperclip(request: Request, path: str = "") -> Any:
    """同域嵌入 Paperclip Web（可选协同）。"""

    return await proxy_embed(request, upstream_base=paperclip_web_base(), path=path)


class PaperclipHandoffRequest(BaseModel):
    """交接落地方案到任务中心。"""

    topic: str = Field(..., min_length=1)
    industry: str = ""
    opportunity_context: str = ""
    landing_plan_markdown: str = ""
    projects: list[dict[str, Any]] = Field(default_factory=list)
    decision_brief_summary: str = ""
    trace_id: str = ""
    hitl_task_id: str = ""


@app.post("/api/paperclip/handoff")
async def paperclip_handoff(body: PaperclipHandoffRequest) -> dict[str, Any]:
    """将商机/落地方案交接为可执行任务（始终写入本地任务中心，外部协同可选增强）。"""

    local = create_landing_task(
        topic=body.topic,
        industry=body.industry,
        plan_markdown=body.landing_plan_markdown,
        projects=body.projects,
        context=body.opportunity_context or body.decision_brief_summary,
        reuse_same_title=True,
    )
    task = local.get("task") or {}
    result: dict[str, Any] = {
        "ok": True,
        "reused": bool(local.get("reused")),
        "source": "local",
        "issue": task,
        "issue_id": task.get("id"),
        "url": "",
        "children": task.get("children") or [],
        "message": "已写入本地任务中心",
    }
    try:
        remote = await handoff_landing(
            topic=body.topic,
            industry=body.industry,
            opportunity_context=body.opportunity_context,
            landing_plan_markdown=body.landing_plan_markdown,
            projects=body.projects,
            decision_brief_summary=body.decision_brief_summary,
            trace_id=body.trace_id,
            hitl_task_id=body.hitl_task_id,
        )
        if isinstance(remote, dict) and remote.get("ok"):
            result["remote"] = remote
            result["url"] = remote.get("url") or ""
            result["message"] = "已写入本地任务中心，并同步外部协同"
    except (PaperclipNotConfigured, Exception):  # noqa: BLE001
        pass
    return result


@app.get("/api/research/cid-indie")
async def research_cid_indie(
    topic: str = "",
    industry: str = "",
    limit: int = 20,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    中国独立开发者项目线索（1c7/chinese-independent-developer）。

    按痛点/人群/行业细分标签过滤，用作商机对标灵感。
    """

    try:
        return await collect_cid_indie_clues(
            keyword=topic,
            industry=industry,
            limit=max(1, min(limit, 50)),
            force_refresh=refresh,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"独立开发者线索采集失败: {exc}") from exc


@app.get("/api/research/projects")
async def research_projects(
    topic: str = "",
    industry: str = "",
    limit: int = 24,
    mode: str = "projects",
    use_cache: bool = True,
    refresh: bool = False,
    track: str = "",
    difficulty: str = "",
    source: str = "",
    region: str = "",
    funding_stage: str = "",
    funding_band: str = "",
    company_nature: str = "",
) -> dict[str, Any]:
    """
    推荐落地/创投项目。

    默认读本地项目库（快）；refresh=true 全网抓取并增量去重写入。
    source: github|36kr|cyzone|cid_indie|all
    """

    try:
        return await recommend_landing_projects(
            topic=topic,
            industry=industry,
            limit=max(8, min(limit, 40)),
            mode=mode or "projects",
            use_cache=use_cache,
            force_refresh=refresh,
            track=track,
            difficulty=difficulty,
            source=source,
            region=region,
            funding_stage=funding_stage,
            funding_band=funding_band,
            company_nature=company_nature,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"落地项目推荐失败: {exc}") from exc


@app.get("/api/research/projects/library")
async def research_projects_library() -> dict[str, Any]:
    """项目库统计（行业/赛道/难易度分布）。"""

    return library_stats()


@app.post("/api/research/projects/warm")
async def research_projects_warm(
    topic: str = "SaaS AI 创业",
    industry: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    """
    预热项目库：全网抓取一批并增量写入（去重）。
    """

    try:
        pack = await recommend_landing_projects(
            topic=topic,
            industry=industry,
            limit=max(12, min(limit, 40)),
            mode="projects",
            use_cache=False,
            force_refresh=True,
        )
        return {
            "ok": True,
            "upsert": pack.get("library_upsert"),
            "count": pack.get("count"),
            "total_found": pack.get("total_found"),
            "stats": library_stats(),
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"预热失败: {exc}") from exc


@app.get("/api/research/pitchhub")
async def research_pitchhub(
    keyword: str = "",
    limit: int = 40,
    sort: str = "3",
) -> dict[str, Any]:
    """拉取 36氪 PitchHub 项目列表（单次，非全库）。"""

    try:
        return await fetch_pitchhub_projects(keyword=keyword, limit=max(8, min(limit, 200)), sort=sort)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"PitchHub 拉取失败: {exc}") from exc


@app.post("/api/research/pitchhub/crawl")
async def research_pitchhub_crawl(
    max_items: int = 2000,
    reset: bool = False,
    background: bool = True,
) -> dict[str, Any]:
    """
    分片爬取 PitchHub 企业项目库并入库。

    background=true 时走 Harness 长任务（推荐）；false 则同步跑完本次批次。
    """

    max_items = max(20, min(int(max_items), 20000))
    try:
        if background:
            job = start_harness_job(kind="pitchhub_crawl", max_items=max_items, reset=reset)
            return {"ok": True, "mode": "harness", "job": job}
        result = await crawl_pitchhub_all(max_items=max_items, reset=reset)
        return {"ok": True, "mode": "sync", "result": result, "stats": library_stats()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"PitchHub 爬取失败: {exc}") from exc


@app.get("/api/harness/jobs")
async def harness_jobs(limit: int = 20) -> dict[str, Any]:
    """Harness 长任务列表。"""

    return {"items": list_harness_jobs(limit=max(1, min(limit, 50)))}


@app.get("/api/harness/jobs/{job_id}")
async def harness_job_detail(job_id: str) -> dict[str, Any]:
    """Harness 任务详情。"""

    doc = get_harness_job(job_id)
    if not doc:
        raise HTTPException(404, "任务不存在")
    return doc


@app.post("/api/harness/jobs")
async def harness_job_start(
    kind: str = "pitchhub_crawl",
    max_items: int = 2000,
    reset: bool = False,
) -> dict[str, Any]:
    """启动 Harness 任务（plan → worker → judge）。"""

    try:
        job = start_harness_job(kind=kind, max_items=max_items, reset=reset)
        return {"ok": True, "job": job}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


class ProjectThemeRequest(BaseModel):
    """从项目识别商机主题并找组合。"""

    name: str = Field(..., min_length=1, description="项目名/标题")
    summary: str = Field(default="", description="摘要")
    url: str = Field(default="", description="项目 URL")
    source: str = Field(default="", description="来源标签")
    industry_niches: list[str] = Field(default_factory=list)
    pain_tags: list[str] = Field(default_factory=list)
    audience_tags: list[str] = Field(default_factory=list)
    industry: str = Field(default="", description="行业提示")
    track: str = Field(default="", description="赛道")
    difficulty: str = Field(default="", description="难易度 easy|mid|hard")
    portfolio_limit: int = Field(default=12, ge=4, le=30)
    with_portfolio: bool = Field(default=True, description="是否同时检索项目组合")
    use_ai: bool = Field(default=True, description="是否用 AI 读描述/README 提炼主题")


@app.post("/api/research/project-theme")
async def research_project_theme(body: ProjectThemeRequest) -> dict[str, Any]:
    """
    一键：项目 → 商机主题（+ 可选项目组合）。优先 AI + README。
    """

    try:
        if body.with_portfolio:
            return await theme_and_portfolio_from_project(
                name=body.name,
                summary=body.summary,
                url=body.url,
                source=body.source,
                industry_niches=body.industry_niches,
                pain_tags=body.pain_tags,
                audience_tags=body.audience_tags,
                industry=body.industry,
                track=body.track,
                difficulty=body.difficulty,
                portfolio_limit=body.portfolio_limit,
                use_ai=body.use_ai,
            )
        readme = ""
        if body.url and "github.com" in body.url.lower():
            readme = await fetch_github_readme(body.url)
        if body.use_ai:
            theme = await ai_infer_theme_from_project(
                name=body.name,
                summary=body.summary,
                url=body.url,
                source=body.source,
                industry_niches=body.industry_niches,
                pain_tags=body.pain_tags,
                audience_tags=body.audience_tags,
                industry=body.industry,
                track=body.track,
                difficulty=body.difficulty,
                readme=readme,
            )
        else:
            theme = infer_theme_from_project(
                name=body.name,
                summary=body.summary,
                source=body.source,
                industry_niches=body.industry_niches,
                pain_tags=body.pain_tags,
                audience_tags=body.audience_tags,
                industry=body.industry,
                track=body.track,
                difficulty=body.difficulty,
                readme=readme,
            )
        return {"theme": theme, "portfolio": None, "source_url": body.url, "readme_chars": len(readme)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"主题识别失败: {exc}") from exc


class OpportunityPortfolioRequest(BaseModel):
    """按商机匹配单项目/组合，并可生成落地方案。"""

    topic: str = Field(..., min_length=2, description="商机主题")
    industry: str = Field(default="")
    opportunity_context: str = Field(default="", description="痛点/定位等上下文")
    generate_plan: bool = Field(default=True)
    projects: list[dict[str, Any]] = Field(default_factory=list, description="可选：已选项目列表")


@app.post("/api/research/opportunity-portfolio")
async def research_opportunity_portfolio(body: OpportunityPortfolioRequest) -> dict[str, Any]:
    """商机 → 仅可落地单项目/组合（+ 可选落地方案）。"""

    try:
        return await landable_portfolio_for_opportunity(
            topic=body.topic,
            industry=body.industry,
            opportunity_context=body.opportunity_context,
            candidate_projects=body.projects or None,
            generate_plan=body.generate_plan,
            use_ai=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"项目组合匹配失败: {exc}") from exc


class LandingPlanRequest(BaseModel):
    """从选定项目一键生成落地方案。"""

    topic: str = Field(..., min_length=2)
    industry: str = Field(default="")
    opportunity_context: str = Field(default="")
    combo_title: str = Field(default="")
    projects: list[dict[str, Any]] = Field(default_factory=list)
    use_ai: bool = True


@app.post("/api/research/landing-plan")
async def research_landing_plan(body: LandingPlanRequest) -> dict[str, Any]:
    """根据推荐项目或组合生成可执行落地方案。"""

    if not body.projects:
        raise HTTPException(400, "请至少选择一个项目")
    try:
        return await generate_landing_plan(
            topic=body.topic,
            industry=body.industry,
            opportunity_context=body.opportunity_context,
            projects=body.projects,
            combo_title=body.combo_title,
            use_ai=body.use_ai,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"落地方案生成失败: {exc}") from exc


class SaveKeyRequest(BaseModel):
    """保存厂商 API Key。"""

    provider_id: str = Field(default="", description="免费目录或档案 id")
    env_name: str = Field(default="", description="直接指定环境变量名")
    api_key: str = Field(..., min_length=8)
    write_dotenv: bool = Field(default=True, description="是否写入仓库 .env")
    activate: bool = Field(default=True, description="保存后切换到该档案")


@app.post("/api/models/keys/save")
async def save_model_key(body: SaveKeyRequest) -> dict[str, Any]:
    """
    控制台维护 Key：写入 runtime_keys + 可选 .env，并注入当前进程。
    """

    env_name = (body.env_name or "").strip().upper()
    profile_id = (body.provider_id or "").strip()
    if not env_name and profile_id:
        item = get_free_catalog_item(profile_id)
        if not item:
            try:
                item = get_profile(profile_id)
            except Exception:  # noqa: BLE001
                item = None
        if not item:
            raise HTTPException(404, f"未知 provider: {profile_id}")
        required = list(item.get("required_env") or [])
        env_name = str(item.get("api_key_env") or (required[0] if required else "")).strip()
    if not env_name:
        raise HTTPException(400, "请提供 env_name 或有效的 provider_id")
    try:
        result = save_runtime_key(env_name, body.api_key, write_dotenv=body.write_dotenv)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    activated = None
    if body.activate and profile_id:
        try:
            activated = set_active_profile_id(profile_id)
        except ValueError:
            activated = None
    return {"ok": True, **result, "active_profile": activated or get_active_profile_id()}


@app.get("/api/research/cyzone")
async def research_cyzone(
    topic: str = "",
    industry: str = "",
    limit: int = 12,
    enrich: bool = True,
) -> dict[str, Any]:
    """
    创业邦（cyzone.cn）公开频道项目/融资资讯采集（真实抓取，禁止 mock）。
    """

    try:
        return await fetch_cyzone_projects(
            keyword=topic or industry or "",
            limit=max(1, min(limit, 40)),
            enrich=enrich,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"创业邦采集失败: {exc}") from exc


class CyzoneLeadsRequest(BaseModel):
    """创业邦商机线索分析请求。"""

    topic: str = Field(default="", description="主题关键词")
    industry: str = Field(default="", description="行业名称")
    limit: int = Field(default=12, ge=1, le=30)


@app.post("/api/research/cyzone/leads")
async def research_cyzone_leads(body: CyzoneLeadsRequest) -> dict[str, Any]:
    """
    基于创业邦真实文章抽取商机线索（需大模型 Key，禁止 mock）。
    """

    if settings.mock_llm:
        raise HTTPException(400, "MOCK_LLM=true，已禁止伪造线索。请关闭 MOCK_LLM。")
    topic = (body.topic or "").strip() or "中国创业项目"
    try:
        pack = await fetch_cyzone_projects(
            keyword=topic or body.industry,
            limit=body.limit,
            enrich=True,
        )
        return await analyze_cyzone_lead_clues(
            topic=topic,
            industry_name=(body.industry or "").strip(),
            cyzone_pack=pack,
            limit=body.limit,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"创业邦商机线索分析失败: {exc}") from exc


@app.post("/api/research/opportunity")
async def research_opportunity(body: ResearchOpportunityRequest) -> dict[str, Any]:
    """
    中国本土商机真实研究（含创业邦商机线索）。

    无商业搜索 Key 时自动走 GitHub/HN/36氪/创业邦免 Key 源；分析仍需大模型 Key，禁止 mock。
    """

    if settings.mock_llm:
        raise HTTPException(400, "MOCK_LLM=true，已禁止伪造研究。请关闭 MOCK_LLM。")
    topic = (body.topic or "").strip()
    if not topic:
        raise HTTPException(400, "topic 必填；若要空主题狩猎请用 POST /api/research/hunt")
    industry_name = (body.industry_name or "").strip()
    industry = (body.industry or "").strip()
    if not industry_name and industry:
        for row in list_industries(body.theme_pack or settings.default_theme_pack):
            if row.get("id") == industry or row.get("name") == industry:
                industry = str(row.get("id") or industry)
                industry_name = str(row.get("name") or industry)
                break
    try:
        return await research_china_opportunity(
            topic=topic,
            industry_id=industry,
            industry_name=industry_name,
            theme_pack=body.theme_pack or settings.default_theme_pack,
            prefer_free_hotspots=True,
        )
    except SearchConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"真实商机研究失败: {exc}") from exc


@app.post("/api/research/hunt")
async def research_hunt(body: HuntOpportunityRequest) -> dict[str, Any]:
    """
    四阶段商机狩猎与重构（可空方向）：宏观雷达→痛点探针→模式重构→专家裁决。

    输出《通用商机挖掘与深度验证报告》结构化 JSON。
    """

    if settings.mock_llm:
        raise HTTPException(400, "MOCK_LLM=true，已禁止伪造狩猎。请关闭 MOCK_LLM。")
    industry_name = (body.industry_name or "").strip()
    industry = (body.industry or "").strip()
    if not industry_name and industry:
        for row in list_industries(body.theme_pack or settings.default_theme_pack):
            if row.get("id") == industry or row.get("name") == industry:
                industry = str(row.get("id") or industry)
                industry_name = str(row.get("name") or industry)
                break
    try:
        report = await hunt_opportunities(
            direction=(body.direction or "").strip(),
            industry=industry,
            industry_name=industry_name,
            theme_pack=body.theme_pack or settings.default_theme_pack,
            llm=orchestrator.llm,
        )
        return {"ok": True, "report": report}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"商机狩猎失败: {exc}") from exc


@app.get("/api/themes/recommend")
async def themes_recommend(
    theme_pack: str = "local-service-leadgen",
    hint: str = "",
    industry: str = "",
    exclude: str = "",
    rotate_industry: bool = False,
    previous_industry: str = "",
    use_llm: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    """
    按行业实时推荐商业主题（真实模型，禁止 mock）。

    - industry: 行业 id 或名称
    - exclude: 逗号分隔的旧主题，换批去重
    - rotate_industry: 刷新时换一个不同行业
    """

    exclude_topics = [part.strip() for part in exclude.split(",") if part.strip()]
    try:
        return await recommend_topics(
            theme_pack=theme_pack or settings.default_theme_pack,
            hint=hint,
            industry=industry,
            exclude_topics=exclude_topics,
            rotate_industry=rotate_industry,
            previous_industry=previous_industry,
            use_llm=use_llm,
            limit=max(1, min(limit, 10)),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"真实主题推荐失败: {exc}") from exc

@app.post("/api/runs")
async def start_run(body: StartRunRequest) -> dict[str, Any]:
    """同步启动（兼容旧客户端）。"""

    return await orchestrator.start_run(
        topic=body.topic,
        theme_pack=body.theme_pack or settings.default_theme_pack,
        industry=body.industry or "",
        force_bad_copy=body.force_bad_copy,
        workflow_template=body.workflow_template or "default-closed-loop",
    )


@app.post("/api/runs/async")
async def start_run_async(body: StartRunRequest) -> dict[str, Any]:
    """异步启动：立即返回 trace_id，过程经 SSE/轮询可视化。"""

    trace_id = new_trace_id()

    async def _job() -> None:
        try:
            await orchestrator.start_run(
                topic=body.topic,
                theme_pack=body.theme_pack or settings.default_theme_pack,
                industry=body.industry or "",
                force_bad_copy=body.force_bad_copy,
                trace_id=trace_id,
                workflow_template=body.workflow_template or "default-closed-loop",
            )
        except RunCancelled as exc:
            await progress_bus.emit(
                trace_id,
                {
                    "type": "run_finished",
                    "status": "cancelled",
                    "error": str(exc),
                    "title": "已停止",
                    "summary": str(exc),
                },
            )
        except Exception as exc:  # noqa: BLE001
            try:
                set_node(trace_id, "opportunity", status="failed", error=str(exc))
            except Exception:  # noqa: BLE001
                pass
            await progress_bus.emit(
                trace_id,
                {
                    "type": "run_finished",
                    "status": "failed",
                    "error": str(exc),
                    "agent": "opportunity",
                    "node_id": "opportunity",
                    "title": "运行失败",
                    "summary": str(exc),
                },
            )

    run_control.ensure(trace_id)
    asyncio.create_task(_job())
    return {
        "trace_id": trace_id,
        "status": "running",
        "events_url": f"/api/runs/{trace_id}/events",
        "stream_url": f"/api/runs/{trace_id}/stream",
    }


class NodeStatusUpdate(BaseModel):
    """人工干预节点状态。"""

    status: str = Field(..., description="idle|waiting|running|success|failed|skipped")
    note: str = ""


class RunControlRequest(BaseModel):
    """运行控制备注。"""

    note: str = ""


@app.get("/api/runs/{trace_id}/control")
async def get_run_control(trace_id: str) -> dict[str, Any]:
    """读取运行控制状态。"""

    return run_control.snapshot(trace_id)


@app.post("/api/runs/{trace_id}/stop")
async def stop_run(trace_id: str, body: RunControlRequest | None = None) -> dict[str, Any]:
    """停止运行（下一检查点生效）。"""

    snap = await run_control.request_stop(trace_id, note=(body.note if body else "") or "ui_stop")
    await progress_bus.emit(
        trace_id,
        {"type": "control", "action": "stop", "status": "cancelled", "title": "请求停止", "summary": snap.get("note")},
    )
    # 把仍在 running 的节点标为 failed/cancelled 观感
    try:
        graph = load_graph(trace_id)
        if graph:
            for nid, node in (graph.get("nodes") or {}).items():
                if node.get("status") == "running":
                    set_node(trace_id, nid, status="failed", error="user_stopped")
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "control": snap}


@app.post("/api/runs/{trace_id}/pause")
async def pause_run(trace_id: str, body: RunControlRequest | None = None) -> dict[str, Any]:
    """暂停运行（下一检查点阻塞直至 resume）。"""

    snap = await run_control.request_pause(trace_id, note=(body.note if body else "") or "ui_pause")
    await progress_bus.emit(
        trace_id,
        {"type": "control", "action": "pause", "status": "paused", "title": "已暂停", "summary": snap.get("note")},
    )
    return {"ok": True, "control": snap}


@app.post("/api/runs/{trace_id}/resume")
async def resume_run(trace_id: str, body: RunControlRequest | None = None) -> dict[str, Any]:
    """恢复暂停中的运行。"""

    snap = await run_control.request_resume(trace_id, note=(body.note if body else "") or "ui_resume")
    await progress_bus.emit(
        trace_id,
        {"type": "control", "action": "resume", "status": "running", "title": "已恢复", "summary": snap.get("note")},
    )
    return {"ok": True, "control": snap}


@app.post("/api/runs/{trace_id}/restart")
async def restart_run(trace_id: str) -> dict[str, Any]:
    """用原图参数重新开一条运行（新 trace）。"""

    graph = load_graph(trace_id)
    if not graph:
        raise HTTPException(404, f"graph not found: {trace_id}")
    await run_control.request_stop(trace_id, note="restart_supersede")
    body = StartRunRequest(
        topic=str(graph.get("topic") or ""),
        theme_pack=str(graph.get("theme_pack") or settings.default_theme_pack),
        industry=str(graph.get("industry") or ""),
        workflow_template=str(graph.get("template_id") or "default-closed-loop"),
    )
    return await start_run_async(body)


@app.put("/api/runs/{trace_id}/nodes/{node_id}/status")
async def update_node_status(trace_id: str, node_id: str, body: NodeStatusUpdate) -> dict[str, Any]:
    """人工干预：直接改节点状态（idle/waiting/success/failed/skipped）。"""

    allowed = {"idle", "waiting", "running", "success", "failed", "skipped"}
    status = (body.status or "").strip().lower()
    if status not in allowed:
        raise HTTPException(400, f"status 必须是 {sorted(allowed)}")
    try:
        graph = set_node(
            trace_id,
            node_id,
            status=status,
            error=body.note or ("" if status != "failed" else "manual_fail"),
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    await progress_bus.emit(
        trace_id,
        {
            "type": "node_status",
            "node_id": node_id,
            "status": status,
            "title": f"节点状态 → {status}",
            "summary": body.note or "",
        },
    )
    return {"ok": True, "graph": graph, "node": graph["nodes"].get(node_id)}


@app.get("/api/runs/{trace_id}/events")
async def run_events(trace_id: str) -> dict[str, Any]:
    """轮询运行过程事件。"""

    return {"trace_id": trace_id, "events": progress_bus.list_events(trace_id)}


@app.get("/api/runs/{trace_id}/stream")
async def run_stream(trace_id: str) -> StreamingResponse:
    """SSE：实时推送可视化过程。"""

    async def event_generator() -> AsyncIterator[str]:
        queue = await progress_bus.subscribe(trace_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                except asyncio.TimeoutError:
                    yield "event: ping\ndata: {}\n\n"
                    continue
                yield f"event: step\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("type") in {"run_finished", "run_paused"}:
                    break
        finally:
            await progress_bus.unsubscribe(trace_id, queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/traces")
async def traces(limit: int = 50) -> dict[str, Any]:
    """Trace 列表。"""

    return {"items": store.list_traces(limit=limit)}


@app.get("/api/traces/{trace_id}")
async def trace_detail(trace_id: str) -> dict[str, Any]:
    """单条 Trace 全链路信封 + 过程事件。"""

    items = store.get_trace_envelopes(trace_id)
    return {
        "trace_id": trace_id,
        "envelopes": items,
        "events": progress_bus.list_events(trace_id),
    }


@app.get("/api/hitl")
async def hitl_list(status: str = "pending") -> dict[str, Any]:
    """人工审批队列。"""

    return {"items": store.list_hitl(status=status)}


@app.get("/api/hitl/{task_id}")
async def hitl_detail(task_id: str) -> dict[str, Any]:
    """单条 HITL 任务详情（含完整决策 body）。"""

    try:
        return store.get_hitl(task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/decision/unit-economics/calc")
async def calc_unit_economics_api(body: UnitEconomicsCalcRequest) -> dict[str, Any]:
    """
    单位经济动态测算（纯计算，无 LLM）。

    供决策台实时调整 CAC / 交付兜底 / 回本周期。
    """

    inputs = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"ok": True, "result": calc_unit_economics(inputs)}


@app.post("/api/hitl/{task_id}/decision-brief/refresh")
async def refresh_decision_brief(task_id: str, body: DecisionBriefRefreshRequest) -> dict[str, Any]:
    """
    重新生成决策穿透简报并可选写回 HITL body。

    用于旧任务补齐，或人工调整商业模式后刷新风险字段。
    """

    try:
        task = store.get_hitl(task_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    hitl_body = dict(task.get("body") or {})
    opportunity = body.opportunity if isinstance(body.opportunity, dict) else (hitl_body.get("opportunity") or {})
    business_model = (
        body.business_model if isinstance(body.business_model, dict) else (hitl_body.get("business_model") or {})
    )
    redteam = body.redteam if isinstance(body.redteam, dict) else (hitl_body.get("redteam") or {})
    brief = await build_decision_brief(
        opportunity=opportunity if isinstance(opportunity, dict) else {},
        business_model=business_model if isinstance(business_model, dict) else {},
        redteam=redteam if isinstance(redteam, dict) else {},
        llm=orchestrator.llm,
    )
    if body.persist:
        task = store.update_hitl_body(task_id, {"decision_brief": brief})
    return {"ok": True, "decision_brief": brief, "task": task}


@app.post("/api/hitl/{task_id}/decide")
async def hitl_decide(task_id: str, body: HitlDecisionRequest) -> dict[str, Any]:
    """审批门禁并继续可视化链路。"""

    try:
        task = store.decide_hitl(task_id, approve=body.approve, note=body.note)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    if not body.approve:
        await progress_bus.emit(
            task["trace_id"],
            {"type": "run_finished", "status": "rejected", "gate": task["gate"]},
        )
        return {"ok": True, "task": task, "continued": None}

    gate = task["gate"]
    trace_id = task["trace_id"]
    if gate == "model_confirm":
        force = body.force_bad_copy or bool((task.get("body") or {}).get("force_bad_copy"))
        continued = await orchestrator.continue_after_model_hitl(
            trace_id,
            force_bad_copy=force,
            budget_cap_test_cny=body.budget_cap_test_cny,
            business_model_patch=body.business_model_patch,
        )
        return {"ok": True, "task": task, "continued": continued}
    if gate == "paid_ads":
        continued = await orchestrator.finalize_paid_ads(trace_id, spend_cny=body.spend_cny)
        return {"ok": True, "task": task, "continued": continued}
    return {"ok": True, "task": task, "continued": None}


@app.get("/api/workflows/templates")
async def workflow_templates() -> dict[str, Any]:
    """列出可切换的工作流模板（含用户自定义）。"""

    return {"items": list_workflow_templates()}


@app.get("/api/workflows/templates/{template_id}")
async def workflow_template_detail(template_id: str) -> dict[str, Any]:
    """模板详情。"""

    return load_workflow_template(template_id)


class WorkflowTemplateSaveRequest(BaseModel):
    """另存/新建工作流模板。"""

    id: str = Field(..., min_length=2, max_length=48)
    name: str = Field(default="")
    description: str = Field(default="")
    clone_from: str = Field(default="default-closed-loop", description="从已有模板克隆拓扑")
    overwrite: bool = True


@app.post("/api/workflows/templates")
async def workflow_template_save(body: WorkflowTemplateSaveRequest) -> dict[str, Any]:
    """
    自定义维护工作流模板：克隆已有拓扑另存到 data/workflow_templates。
    """

    try:
        item = save_workflow_template(
            {
                "id": body.id,
                "name": body.name or body.id,
                "description": body.description,
                "clone_from": body.clone_from,
            },
            overwrite=body.overwrite,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "item": item, "items": list_workflow_templates()}


@app.delete("/api/workflows/templates/{template_id}")
async def workflow_template_delete(template_id: str) -> dict[str, Any]:
    """删除用户自定义模板。"""

    try:
        ok = delete_workflow_template(template_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not ok:
        raise HTTPException(404, "自定义模板不存在")
    return {"ok": True, "items": list_workflow_templates()}


@app.put("/api/runs/{trace_id}/nodes/{node_id}/config")
async def update_run_node_config(trace_id: str, node_id: str, body: NodeConfigUpdate) -> dict[str, Any]:
    """更新运行中图的节点配置（模型/Skill/提示词/替换 Agent）。"""

    patch = body.model_dump(exclude_none=True)
    try:
        graph = update_node_config(trace_id, node_id, patch)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "graph": graph, "node": graph["nodes"].get(node_id)}


@app.get("/api/bindings")
async def get_all_bindings() -> dict[str, Any]:
    """获取全部 Agent 绑定 + 可选目录。"""

    return {
        "bindings": load_bindings(),
        "catalogs": {
            "skills": list_skill_catalog(),
            "rules": list_rule_catalog(),
            "mcp": list_mcp_catalog(),
            "model_modes": ["route", "profile", "explicit"],
            "routes": ["tier_s", "tier_m", "tier_xs"],
        },
    }


@app.get("/api/bindings/{agent_key}")
async def get_one_binding(agent_key: str) -> dict[str, Any]:
    """获取单个 Agent（或节点）绑定。"""

    try:
        return get_agent_binding(agent_key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/bindings/{agent_key}")
async def put_binding(agent_key: str, body: AgentBindingPatch) -> dict[str, Any]:
    """动态绑定/替换 Skill、Rule、MCP、Model。"""

    try:
        updated = update_agent_binding(agent_key, body.model_dump(exclude_none=True))
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "binding": updated}


@app.post("/api/bindings/{agent_key}/reset")
async def reset_binding(agent_key: str) -> dict[str, Any]:
    """重置为默认 YAML 绑定。"""

    try:
        updated = reset_agent_binding(agent_key)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True, "binding": updated}


@app.get("/api/memory/snapshot")
async def memory_snapshot(theme_pack: str = "local-service-leadgen", topic: str = "") -> dict[str, Any]:
    """预览冻结记忆快照。"""

    snap = store.build_snapshot(theme_pack, topic)
    return {
        "theme_pack": snap.theme_pack,
        "preferences": snap.preferences,
        "lessons": snap.lessons,
        "procedural_skills": snap.procedural_skills,
        "procedural_rules": snap.procedural_rules,
        "prompt_block": snap.to_prompt_block(),
        "generated_at": snap.generated_at,
    }


@app.get("/api/runs/{trace_id}/graph")
async def get_run_graph(trace_id: str) -> dict[str, Any]:
    """获取 n8n/Dify 风格工作流图（含每节点多模态 I/O）。"""

    graph = load_graph(trace_id)
    if not graph:
        raise HTTPException(404, "工作流图不存在")
    return graph


@app.put("/api/runs/{trace_id}/nodes/{node_id}/inputs")
async def put_node_inputs(trace_id: str, node_id: str, body: NodeInputUpdate) -> dict[str, Any]:
    """人工干预：修改节点输入（文字/图片/视频）。"""

    try:
        graph = update_node_inputs(trace_id, node_id, body.inputs)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "graph": graph}


@app.post("/api/runs/{trace_id}/nodes/{node_id}/rerun")
async def rerun_node(trace_id: str, node_id: str, body: NodeRerunRequest) -> dict[str, Any]:
    """按修改后的输入重跑节点，可选级联下游。"""

    try:
        graph = await node_runner.run_node(
            trace_id,
            node_id,
            cascade=body.cascade,
            generate_visuals=body.generate_visuals,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "graph": graph}


@app.post("/api/media/upload")
async def upload_media(file: UploadFile = File(...)) -> dict[str, Any]:
    """
    上传图片或视频，供节点输入引用。

    Returns:
        url: /media/<filename> 可直接用于 MediaBlock.url
    """

    filename = file.filename or "upload.bin"
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in filename)
    from uuid6 import uuid7

    out_name = f"{uuid7().hex[:10]}_{safe}"
    dest = MEDIA_DIR / out_name
    content = await file.read()
    if len(content) > 80 * 1024 * 1024:
        raise HTTPException(400, "文件过大（上限 80MB）")
    dest.write_bytes(content)
    mime = file.content_type or "application/octet-stream"
    media_type = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "text"
    return {
        "ok": True,
        "url": f"/media/{out_name}",
        "type": media_type,
        "mime": mime,
        "name": filename,
    }


@app.get("/preview/{trace_id}")
async def preview_page(trace_id: str) -> dict[str, Any]:
    """部署预览桩。"""

    return {
        "trace_id": trace_id,
        "message": "本地预览桩 — 生产环境由 scaffolds/next-landing 参数化生成后替换",
    }
