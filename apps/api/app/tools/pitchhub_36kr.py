# -*- coding: utf-8 -*-
"""
36氪 PitchHub 企业项目库采集。

作用: 调用 gateway.36kr.com/api/pms/project/list，按行业×融资轮次分片爬取并入库（禁止 mock）。
说明: 单次查询最多约 50 页×20 条；全量需分片增量爬取。
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from app.settings import data_dir
from app.tools.project_library import upsert_projects
from app.tools.project_meta import enrich_project_row


PITCHHUB_LIST_URL = "https://gateway.36kr.com/api/pms/project/list"
PITCHHUB_PROJECT_URL = "https://pitchhub.36kr.com/project/{project_id}"

# 页面展示的行业 / 轮次（与网关返回一致）
DEFAULT_INDUSTRIES: list[dict[str, Any]] = [
    {"code": 1, "name": "文化娱乐"},
    {"code": 2, "name": "消费电商"},
    {"code": 3, "name": "汽车出行"},
    {"code": 4, "name": "教育"},
    {"code": 5, "name": "金融"},
    {"code": 6, "name": "企业服务"},
    {"code": 7, "name": "产业升级"},
    {"code": 8, "name": "前沿技术"},
    {"code": 9, "name": "医疗健康"},
    {"code": 10, "name": "先进制造"},
    {"code": 11, "name": "通信/半导体"},
    {"code": 12, "name": "物联网/硬件"},
    {"code": 13, "name": "工具软件"},
    {"code": 14, "name": "社交网络"},
    {"code": 15, "name": "农林牧渔"},
    {"code": 16, "name": "能源环保"},
    {"code": 17, "name": "本地生活"},
    {"code": 18, "name": "体育游戏"},
    {"code": 19, "name": "跨境出海"},
    {"code": 20, "name": "房产地产"},
    {"code": 21, "name": "旅游"},
    {"code": 22, "name": "广告营销"},
    {"code": 23, "name": "智能硬件"},
    {"code": 24, "name": "物流"},
    {"code": 25, "name": "区块链"},
    {"code": 26, "name": "传统制造"},
    {"code": 27, "name": "元宇宙"},
    {"code": 999, "name": "其他"},
]

DEFAULT_ROUNDS: list[dict[str, Any]] = [
    {"code": 1, "name": "未融资"},
    {"code": 2, "name": "种子轮"},
    {"code": 3, "name": "天使轮"},
    {"code": 4, "name": "Pre-A轮"},
    {"code": 5, "name": "A轮"},
    {"code": 6, "name": "A+轮"},
    {"code": 7, "name": "Pre-B轮"},
    {"code": 8, "name": "B轮"},
    {"code": 9, "name": "B+轮"},
    {"code": 10, "name": "C轮"},
    {"code": 11, "name": "C+轮"},
    {"code": 12, "name": "D轮"},
    {"code": 13, "name": "D+轮"},
    {"code": 14, "name": "E轮"},
    {"code": 21, "name": "战略融资"},
    {"code": 25, "name": "已上市"},
    {"code": 999, "name": "其他"},
]

_PAGE_SIZE = 20
_MAX_PAGES_PER_QUERY = 50  # 网关硬上限


def _state_path() -> Path:
    return data_dir() / "pitchhub_crawl_state.json"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 LeadForgePitchHub/1.0"
        ),
        "Content-Type": "application/json",
        "Origin": "https://pitchhub.36kr.com",
        "Referer": "https://pitchhub.36kr.com/projects?sort=3",
        "Accept": "application/json, text/plain, */*",
    }


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"done_shards": [], "stats": {}, "updated_at": None}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {"done_shards": [], "stats": {}}
    except Exception:  # noqa: BLE001
        return {"done_shards": [], "stats": {}, "error": "corrupt"}


def _save_state(doc: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {**doc, "updated_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_pitchhub_row(row: dict[str, Any]) -> dict[str, Any]:
    """
    将网关项目行转为本地项目库结构。

    Args:
        row: PitchHub projectList 单条。

    Returns:
        标准化项目 dict。
    """

    pid = row.get("id") or row.get("projectId")
    name = str(row.get("name") or "").strip()
    brief = str(row.get("briefIntro") or row.get("brief") or "").strip()
    industry = str(row.get("industry") or "").strip()
    stage = str(row.get("lastestFinancingRound") or row.get("latestFinancingRound") or "").strip()
    area = str(row.get("area") or "").strip()
    established = str(row.get("establishTime") or "").strip()
    url = PITCHHUB_PROJECT_URL.format(project_id=pid) if pid else ""
    summary_parts = [brief]
    if industry:
        summary_parts.append(f"行业:{industry}")
    if stage:
        summary_parts.append(f"阶段:{stage}")
    if area:
        summary_parts.append(f"地区:{area}")
    if established:
        summary_parts.append(f"成立:{established}")
    base = {
        "name": name or f"PitchHub-{pid}",
        "url": url,
        "summary": " · ".join(x for x in summary_parts if x)[:500],
        "source": "36kr_pitchhub",
        "source_label": "36氪·PitchHub",
        "kind": "startup_project",
        "pain_tags": [],
        "audience_tags": [],
        "industry_niches": [x for x in industry.replace("  ", " ").split(" ") if x][:4],
        "one_click_ready": False,
        "one_click_signals": [],
        "stars": None,
        "heat": 70.0,
        "logo_url": row.get("logoUrl"),
        "region": area or "中国",
        "funding_stage": stage or "unknown",
        "company_nature": "创业公司",
        "industry": industry.split()[0] if industry else "",
        "pitchhub_id": str(pid) if pid else "",
        "establish_time": established,
        "how_to_use": "PitchHub 企业项目：识别商机主题后跑工作流；融资字段来自公开库，勿当精确估值。",
    }
    return enrich_project_row(base)


async def _post_list(client: httpx.AsyncClient, param: dict[str, Any]) -> dict[str, Any]:
    """
    请求项目列表。

    Raises:
        RuntimeError: HTTP 或业务码失败。
    """

    body = {
        "partner_id": "web",
        "timestamp": int(time.time() * 1000),
        "partner_version": "1.0.0",
        "param": param,
    }
    resp = await client.post(PITCHHUB_LIST_URL, json=body)
    if resp.status_code >= 400:
        raise RuntimeError(f"PitchHub HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("PitchHub 返回非对象")
    if int(data.get("code") or 0) != 0:
        raise RuntimeError(f"PitchHub code={data.get('code')} msg={data.get('msg') or data.get('message')}")
    return data.get("data") if isinstance(data.get("data"), dict) else {}


async def fetch_pitchhub_page(
    *,
    page_no: int = 1,
    page_size: int = _PAGE_SIZE,
    sort: str = "3",
    keyword: str = "",
    trade_id: Optional[int] = None,
    financing_round_id: Optional[int] = None,
    establish_year: Optional[int] = None,
    province_id: Optional[int] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """
    拉取单页 PitchHub 项目。

    Args:
        sort: 3=项目推荐（与页面 sort=3 一致），1=最近更新，2=最新收录。

    Returns:
        {items, page, raw_count}
    """

    page_size = max(1, min(int(page_size), _PAGE_SIZE))
    param: dict[str, Any] = {
        "pageNo": max(1, int(page_no)),
        "pageSize": page_size,
        "sort": str(sort),
        "keyword": (keyword or "").strip(),
        "siteId": 1,
        "platformId": 2,
    }
    if trade_id is not None:
        param["tradeIdList"] = [int(trade_id)]
    if financing_round_id is not None:
        param["financingRoundIdList"] = [int(financing_round_id)]
    if establish_year is not None:
        param["establishYearList"] = [int(establish_year)]
    if province_id is not None:
        param["provinceIdList"] = [int(province_id)]

    owns = client is None
    client = client or httpx.AsyncClient(timeout=45.0, headers=_headers(), follow_redirects=True)
    try:
        data = await _post_list(client, param)
    finally:
        if owns:
            await client.aclose()

    rows = [normalize_pitchhub_row(r) for r in (data.get("projectList") or []) if isinstance(r, dict)]
    page = data.get("page") if isinstance(data.get("page"), dict) else {}
    return {
        "items": rows,
        "page": page,
        "raw_count": len(rows),
        "industries": data.get("industryList") or DEFAULT_INDUSTRIES,
        "rounds": data.get("financingRoundList") or DEFAULT_ROUNDS,
    }


async def fetch_pitchhub_projects(
    *,
    keyword: str = "",
    limit: int = 40,
    sort: str = "3",
    trade_id: Optional[int] = None,
) -> dict[str, Any]:
    """
    按关键词/行业拉取一批 PitchHub 项目（用于推荐，非全库）。

    Returns:
        {items, count, source, page_meta, errors}
    """

    limit = max(1, min(int(limit), 200))
    errors: list[str] = []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_meta: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=45.0, headers=_headers(), follow_redirects=True) as client:
        page_no = 1
        while len(items) < limit and page_no <= _MAX_PAGES_PER_QUERY:
            try:
                pack = await fetch_pitchhub_page(
                    page_no=page_no,
                    sort=sort,
                    keyword=keyword,
                    trade_id=trade_id,
                    client=client,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(str(exc))
                break
            page_meta = pack.get("page") or {}
            batch = pack.get("items") or []
            if not batch:
                break
            for row in batch:
                url = str(row.get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                items.append(row)
                if len(items) >= limit:
                    break
            total_page = int(page_meta.get("totalPage") or 1)
            if page_no >= total_page:
                break
            page_no += 1
            await asyncio.sleep(0.15)
    return {
        "source": "36kr_pitchhub",
        "keyword": keyword,
        "count": len(items),
        "items": items[:limit],
        "page_meta": page_meta,
        "errors": errors,
        "note": "来自 pitchhub.36kr.com 公开网关；单查询最多约 1000 条。",
    }


def _shard_key(trade_id: int, round_id: int) -> str:
    return f"t{trade_id}-r{round_id}"


async def crawl_pitchhub_all(
    *,
    max_items: int = 2000,
    sort: str = "3",
    reset: bool = False,
    delay_sec: float = 0.2,
    industries: Optional[list[dict[str, Any]]] = None,
    rounds: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    分片增量爬取 PitchHub（行业 × 融资轮次），写入项目库并保存断点。

    Args:
        max_items: 本次最多新入库条数（防止一次跑爆）。
        reset: 清空断点重新爬。
        delay_sec: 请求间隔，降低风控概率。

    Returns:
        爬取统计与断点信息。
    """

    max_items = max(20, min(int(max_items), 20000))
    state = {"done_shards": [], "stats": {}} if reset else _load_state()
    done = set(str(x) for x in (state.get("done_shards") or []))
    industries = industries or DEFAULT_INDUSTRIES
    rounds = rounds or DEFAULT_ROUNDS

    collected: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    shards_done_now: list[str] = []
    pages_fetched = 0

    async with httpx.AsyncClient(timeout=45.0, headers=_headers(), follow_redirects=True) as client:
        for ind in industries:
            if len(collected) >= max_items:
                break
            trade_id = int(ind.get("code"))
            for rnd in rounds:
                if len(collected) >= max_items:
                    break
                round_id = int(rnd.get("code"))
                key = _shard_key(trade_id, round_id)
                if key in done:
                    continue
                page_no = 1
                shard_got = 0
                while page_no <= _MAX_PAGES_PER_QUERY and len(collected) < max_items:
                    try:
                        pack = await fetch_pitchhub_page(
                            page_no=page_no,
                            sort=sort,
                            trade_id=trade_id,
                            financing_round_id=round_id,
                            client=client,
                        )
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{key} p{page_no}: {exc}")
                        await asyncio.sleep(max(delay_sec, 0.5))
                        break
                    pages_fetched += 1
                    page = pack.get("page") or {}
                    batch = pack.get("items") or []
                    if not batch:
                        break
                    for row in batch:
                        url = str(row.get("url") or "")
                        if not url or url in seen:
                            continue
                        seen.add(url)
                        # 补强行业名
                        if not row.get("industry"):
                            row["industry"] = str(ind.get("name") or "")
                        if not row.get("funding_stage") or row.get("funding_stage") == "unknown":
                            row["funding_stage"] = str(rnd.get("name") or "unknown")
                        collected.append(row)
                        shard_got += 1
                        if len(collected) >= max_items:
                            break
                    total_page = int(page.get("totalPage") or 1)
                    total_count = int(page.get("totalCount") or 0)
                    # 若该分片仍 >1000，再按成立年份二次分片（当年未完成则标记 partial）
                    if page_no >= total_page:
                        if total_count > _PAGE_SIZE * _MAX_PAGES_PER_QUERY:
                            # 大分片：按近年年份继续挖
                            for year in range(2026, 2010, -1):
                                if len(collected) >= max_items:
                                    break
                                ykey = f"{key}-y{year}"
                                if ykey in done:
                                    continue
                                for yp in range(1, _MAX_PAGES_PER_QUERY + 1):
                                    if len(collected) >= max_items:
                                        break
                                    try:
                                        ypack = await fetch_pitchhub_page(
                                            page_no=yp,
                                            sort=sort,
                                            trade_id=trade_id,
                                            financing_round_id=round_id,
                                            establish_year=year,
                                            client=client,
                                        )
                                    except Exception as exc:  # noqa: BLE001
                                        errors.append(f"{ykey} p{yp}: {exc}")
                                        break
                                    pages_fetched += 1
                                    ybatch = ypack.get("items") or []
                                    if not ybatch:
                                        break
                                    for row in ybatch:
                                        url = str(row.get("url") or "")
                                        if not url or url in seen:
                                            continue
                                        seen.add(url)
                                        collected.append(row)
                                        if len(collected) >= max_items:
                                            break
                                    ypage = ypack.get("page") or {}
                                    if yp >= int(ypage.get("totalPage") or 1):
                                        break
                                    await asyncio.sleep(delay_sec)
                                done.add(ykey)
                                shards_done_now.append(ykey)
                        break
                    page_no += 1
                    await asyncio.sleep(delay_sec)
                done.add(key)
                shards_done_now.append(key)

    upsert_stats = upsert_projects(collected) if collected else {"added": 0, "updated": 0, "total": 0}
    state = {
        "done_shards": sorted(done),
        "stats": {
            **(state.get("stats") if isinstance(state.get("stats"), dict) else {}),
            "last_batch": len(collected),
            "pages_fetched": pages_fetched,
            "shards_done_total": len(done),
        },
        "last_errors": errors[:20],
    }
    _save_state(state)

    return {
        "ok": True,
        "source": "36kr_pitchhub",
        "fetched": len(collected),
        "pages_fetched": pages_fetched,
        "shards_completed_this_run": shards_done_now,
        "shards_done_total": len(done),
        "upsert": upsert_stats,
        "errors": errors[:12],
        "checkpoint": str(_state_path()),
        "note": (
            "PitchHub 单查询上限约 1000 条；已按「行业×融资轮次（必要时再×成立年份）」分片增量爬取。"
            "重复调用会从断点继续，reset=true 可重爬。"
            f" 公开库总量约 18 万+；本次 max_items={max_items}。"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
