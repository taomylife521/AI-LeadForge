# -*- coding: utf-8 -*-
"""
从真实项目条目推断商机主题，并检索关联项目组合。

作用: 支持「项目 → 商机主题 → 工作流 / 项目组合」一键链路；优先用 AI 读描述/README 提炼（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import re
from typing import Any, Optional

import httpx

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.theme_recommend import list_all_industries
from app.tools.project_library import classify_project, score_industry_match
from app.tools.project_recommend import recommend_landing_projects


_FUNDING_STRIP = re.compile(
    r"(完成|获|宣布)?\s*(种子轮|天使轮|Pre-A|PreA|A轮|B轮|C轮|D轮|战略融资|融资|获投).*$"
)
_QUOTE_NAME = re.compile(r"[「“\"]([^」”\"]{2,40})[」”\"]")
_COMPANY_PREFIX = re.compile(
    r"^(?P<name>[\u4e00-\u9fffA-Za-z0-9·\-]{2,28}?)(公司|科技|智能|网络|信息|生物|医疗|集团)?"
)
_GH_REPO = re.compile(r"github\.com/([^/\s]+)/([^/\s?#]+)", re.I)


def _match_industry_catalog(blob: str) -> dict[str, str]:
    """
    对照系统行业目录做精确匹配。

    Returns:
        {id, name, hint}；未命中则空 dict。
    """

    blob_l = (blob or "").lower()
    best: dict[str, str] = {}
    best_score = 0
    best_len = 0
    for row in list_all_industries():
        name = str(row.get("name") or "")
        hint = str(row.get("hint") or "")
        score = score_industry_match(name, hint, blob_l)
        if score > best_score or (score == best_score and score > 0 and len(name) > best_len):
            best_score = score
            best_len = len(name)
            best = {"id": str(row.get("id") or ""), "name": name, "hint": hint}
    return best if best_score >= 6 else {}


def _industry_catalog_lines() -> str:
    """供 LLM 选择的行业清单。"""

    lines: list[str] = []
    for row in list_all_industries()[:80]:
        lines.append(f"- id={row.get('id')} | name={row.get('name')} | hint={row.get('hint')}")
    return "\n".join(lines)


async def fetch_github_readme(url: str, *, timeout: float = 25.0) -> str:
    """
    拉取 GitHub 仓库 README 原文（jsDelivr / raw 回退）。

    Args:
        url: 仓库或 README URL。
        timeout: 请求超时秒数。

    Returns:
        README 文本（截断）；失败返回空串。
    """

    m = _GH_REPO.search(url or "")
    if not m:
        return ""
    owner, repo = m.group(1), m.group(2).removesuffix(".git")
    candidates = [
        f"https://cdn.jsdelivr.net/gh/{owner}/{repo}@HEAD/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
    ]
    headers = {"User-Agent": "LeadForgeProjectTheme/1.0", "Accept": "text/plain,*/*"}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for link in candidates:
            try:
                resp = await client.get(link, headers=headers)
                if resp.status_code >= 400:
                    continue
                text = (resp.text or "").strip()
                if len(text) < 40:
                    continue
                # 去掉过大内容
                return text[:8000]
            except Exception:  # noqa: BLE001
                continue
    return ""


def infer_theme_from_project(
    *,
    name: str = "",
    summary: str = "",
    source: str = "",
    industry_niches: Optional[list[str]] = None,
    pain_tags: Optional[list[str]] = None,
    audience_tags: Optional[list[str]] = None,
    industry: str = "",
    track: str = "",
    difficulty: str = "",
    readme: str = "",
) -> dict[str, Any]:
    """
    规则兜底：从项目名/摘要/README 抽取商机主题（对齐行业目录 + 赛道/难易度）。

    Returns:
        含 topic / industry_* / track / difficulty / audience_segment / pain_core 等。
    """

    name = (name or "").strip()
    summary = (summary or "").strip()
    readme = (readme or "").strip()
    niches = [str(x) for x in (industry_niches or []) if x]
    pains = [str(x) for x in (pain_tags or []) if x]
    audience = [str(x) for x in (audience_tags or []) if x]
    readme_head = readme[:1200]

    classified = classify_project(
        {
            "name": name,
            "summary": f"{summary}\n{readme_head}",
            "industry_niches": niches,
            "pain_tags": pains,
            "kind": "",
            "source": source,
            "one_click_ready": False,
            "stars": 0,
            "industry": industry,
        }
    )
    track = track or classified.get("track") or "other"
    difficulty = difficulty or classified.get("difficulty") or "mid"

    quoted = _QUOTE_NAME.search(f"{name} {summary}")
    product = quoted.group(1).strip() if quoted else ""
    cleaned = _FUNDING_STRIP.sub("", name).strip(" ，,：:-")
    cleaned = re.sub(r"^(独家|快讯|重磅)[丨|｜:\s]*", "", cleaned)
    if not product:
        m = _COMPANY_PREFIX.match(cleaned)
        product = (m.group("name") if m else cleaned)[:28] or name[:28]
    product = product.strip("「」\"'·- ") or "未命名项目"

    pain_core = pains[0] if pains else ""
    if not pain_core and summary:
        pain_core = re.split(r"[。；;，,]", summary)[0][:48]
    if not pain_core and readme_head:
        # README 首段非标题行
        for line in readme_head.splitlines():
            line = line.strip().lstrip("#").strip()
            if len(line) >= 12 and not line.lower().startswith("http"):
                pain_core = line[:48]
                break
    if not pain_core:
        pain_core = f"对标「{product}」尚未被服务好的细分痛点"

    catalog = _match_industry_catalog(
        f"{name} {summary} {readme_head} {' '.join(niches)} {industry} {classified.get('industry') or ''}"
    )
    if industry and not catalog:
        catalog = _match_industry_catalog(industry)
    if catalog:
        industry_hint = catalog["name"]
        industry_id = catalog["id"]
        industry_focus = catalog.get("hint") or catalog["name"]
    else:
        industry_hint = (
            industry
            or classified.get("industry")
            or (niches[0] if niches else "")
            or "垂直工具"
        )
        industry_id = str(classified.get("industry_id") or "")
        industry_focus = industry_hint

    if audience:
        audience_segment = audience[0]
    else:
        audience_map = {
            "local": "到店服务经营者/店长",
            "health": "有康复/健康付费意愿的企业或家庭",
            "content": "内容创作者/运营",
            "devtools": "独立开发者/技术团队",
            "saas": "中小微企业决策人",
            "ai_tool": "需要提效的知识工作者",
            "fintech": "有合规诉求的商户/机构",
        }
        audience_segment = audience_map.get(track, "清晰付费方（待验证）")

    topic = f"{audience_segment} · {pain_core} · 对标{product}"
    topic = re.sub(r"\s+", " ", topic).strip()[:90]
    portfolio_query = " ".join(x for x in [product, industry_hint, track] if x)[:60]

    return {
        "topic": topic,
        "product_name": product,
        "industry_id": industry_id,
        "industry_hint": industry_hint,
        "industry_focus": industry_focus,
        "track": track,
        "difficulty": difficulty,
        "audience_segment": audience_segment,
        "pain_core": pain_core,
        "rationale": (
            f"由{source or '项目'}「{name[:36]}」识别："
            f"行业={industry_hint}，赛道={track}，落地难度={difficulty}；"
            "主题用于工作流，非融资事实。"
        ),
        "portfolio_query": portfolio_query or topic,
        "method": "rules",
        "readme_used": bool(readme_head),
    }


async def ai_infer_theme_from_project(
    *,
    name: str,
    summary: str = "",
    url: str = "",
    source: str = "",
    industry_niches: Optional[list[str]] = None,
    pain_tags: Optional[list[str]] = None,
    audience_tags: Optional[list[str]] = None,
    industry: str = "",
    track: str = "",
    difficulty: str = "",
    readme: str = "",
) -> dict[str, Any]:
    """
    用真实 LLM 根据描述 + README 提炼商机主题与行业（失败则回退规则）。

    Returns:
        theme dict；含 method=ai|rules_fallback。
    """

    fallback = infer_theme_from_project(
        name=name,
        summary=summary,
        source=source,
        industry_niches=industry_niches,
        pain_tags=pain_tags,
        audience_tags=audience_tags,
        industry=industry,
        track=track,
        difficulty=difficulty,
        readme=readme,
    )
    catalog = _industry_catalog_lines()
    system = (
        "你是中国商机分析助手。只依据给定项目描述与 README 提炼商机主题。"
        "禁止编造融资额、用户量、未出现的事实。未知写 unknown。"
        "行业必须尽量选自给定目录（返回 industry_id 与 industry_name）；"
        "若都不匹配可给最接近的 name，industry_id 置空。"
        "track 只能是: saas|local|ai_tool|health|content|devtools|fintech|other。"
        "difficulty 只能是: easy|mid|hard（easy=可一键试用/已上线，hard=重研发或重运营）。"
        "输出 JSON："
        '{"topic":"人群 · 痛点 · 对标产品","product_name":"...","industry_id":"...",'
        '"industry_name":"...","industry_focus":"...","track":"...","difficulty":"...",'
        '"audience_segment":"...","pain_core":"...","rationale":"一句话依据"}'
    )
    user = (
        f"项目名: {name}\n来源: {source}\nURL: {url}\n"
        f"已有行业提示: {industry}\n已有赛道: {track}\n已有难度: {difficulty}\n"
        f"细分 niches: {industry_niches or []}\n痛点标签: {pain_tags or []}\n人群标签: {audience_tags or []}\n"
        f"摘要:\n{(summary or '')[:800]}\n\n"
        f"README(截断):\n{(readme or '')[:3500]}\n\n"
        f"可选行业目录:\n{catalog}\n"
    )
    try:
        llm = LLMClient()
        payload, model_name, used_mock = await llm.complete_json(
            route=ModelRoute.TIER_M,
            system=system,
            user=user,
            allow_mock=False,
            temperature=0.2,
        )
        if used_mock or not isinstance(payload, dict):
            fallback["method"] = "rules_fallback"
            fallback["ai_error"] = "mock_or_empty"
            return fallback

        industry_name = str(payload.get("industry_name") or payload.get("industry_hint") or "").strip()
        industry_id = str(payload.get("industry_id") or "").strip()
        # 校验/纠偏行业目录
        catalog_hit = {}
        if industry_id:
            for row in list_all_industries():
                if str(row.get("id")) == industry_id:
                    catalog_hit = {"id": industry_id, "name": row.get("name"), "hint": row.get("hint")}
                    break
        if not catalog_hit and industry_name:
            catalog_hit = _match_industry_catalog(industry_name)
        if catalog_hit:
            industry_id = str(catalog_hit.get("id") or industry_id)
            industry_name = str(catalog_hit.get("name") or industry_name)
            industry_focus = str(catalog_hit.get("hint") or payload.get("industry_focus") or industry_name)
        else:
            industry_focus = str(payload.get("industry_focus") or industry_name or fallback["industry_focus"])

        track_v = str(payload.get("track") or fallback["track"]).strip().lower()
        if track_v not in {"saas", "local", "ai_tool", "health", "content", "devtools", "fintech", "other"}:
            track_v = fallback["track"]
        diff_v = str(payload.get("difficulty") or fallback["difficulty"]).strip().lower()
        if diff_v not in {"easy", "mid", "hard"}:
            diff_v = fallback["difficulty"]

        topic = str(payload.get("topic") or "").strip() or fallback["topic"]
        product = str(payload.get("product_name") or "").strip() or fallback["product_name"]
        audience_segment = str(payload.get("audience_segment") or "").strip() or fallback["audience_segment"]
        pain_core = str(payload.get("pain_core") or "").strip() or fallback["pain_core"]
        rationale = str(payload.get("rationale") or "").strip() or fallback["rationale"]

        return {
            "topic": topic[:90],
            "product_name": product[:40],
            "industry_id": industry_id,
            "industry_hint": industry_name or fallback["industry_hint"],
            "industry_focus": industry_focus,
            "track": track_v,
            "difficulty": diff_v,
            "audience_segment": audience_segment[:40],
            "pain_core": pain_core[:60],
            "rationale": f"[AI:{model_name}] {rationale}"[:220],
            "portfolio_query": " ".join(
                x for x in [product, industry_name or fallback["industry_hint"], track_v] if x
            )[:60],
            "method": "ai",
            "readme_used": bool(readme),
            "model": model_name,
        }
    except Exception as exc:  # noqa: BLE001
        fallback["method"] = "rules_fallback"
        fallback["ai_error"] = str(exc)[:180]
        return fallback


async def theme_and_portfolio_from_project(
    *,
    name: str,
    summary: str = "",
    url: str = "",
    source: str = "",
    industry_niches: Optional[list[str]] = None,
    pain_tags: Optional[list[str]] = None,
    audience_tags: Optional[list[str]] = None,
    industry: str = "",
    track: str = "",
    difficulty: str = "",
    portfolio_limit: int = 12,
    use_ai: bool = True,
) -> dict[str, Any]:
    """识别商机主题并检索关联项目组合（优先 AI+README，组合优先缓存库）。"""

    readme = ""
    if url and "github.com" in url.lower():
        readme = await fetch_github_readme(url)

    if use_ai:
        theme = await ai_infer_theme_from_project(
            name=name,
            summary=summary,
            url=url,
            source=source,
            industry_niches=industry_niches,
            pain_tags=pain_tags,
            audience_tags=audience_tags,
            industry=industry,
            track=track,
            difficulty=difficulty,
            readme=readme,
        )
    else:
        theme = infer_theme_from_project(
            name=name,
            summary=summary,
            source=source,
            industry_niches=industry_niches,
            pain_tags=pain_tags,
            audience_tags=audience_tags,
            industry=industry,
            track=track,
            difficulty=difficulty,
            readme=readme,
        )

    portfolio = await recommend_landing_projects(
        topic=theme.get("portfolio_query") or theme.get("topic") or name,
        industry=str(theme.get("industry_hint") or ""),
        limit=portfolio_limit,
        mode="projects",
        use_cache=True,
        track=str(theme.get("track") or ""),
    )
    if url:
        portfolio["items"] = [r for r in (portfolio.get("items") or []) if r.get("url") != url]
        portfolio["count"] = len(portfolio["items"])
    return {
        "theme": theme,
        "portfolio": portfolio,
        "source_url": url,
        "readme_chars": len(readme),
    }
