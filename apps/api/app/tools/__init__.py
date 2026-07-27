# -*- coding: utf-8 -*-
"""
LeadForge 真实工具包。

作用: 中国本土搜索、免 Key 热点、网页抓取、商机研究（禁止 mock）。
作者: LeadForge
创建时间: 2026-07-24
"""

from app.tools.cn_search import china_web_search
from app.tools.cyzone import analyze_cyzone_lead_clues, fetch_cyzone_projects
from app.tools.hotspot_sources import collect_free_hotspots
from app.tools.opportunity_research import research_china_opportunity
from app.tools.opportunity_score import enrich_opportunity_scores
from app.tools.web_fetch import fetch_url_text

__all__ = [
    "china_web_search",
    "fetch_url_text",
    "research_china_opportunity",
    "collect_free_hotspots",
    "fetch_cyzone_projects",
    "analyze_cyzone_lead_clues",
    "enrich_opportunity_scores",
]
