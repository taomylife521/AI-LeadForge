# -*- coding: utf-8 -*-
"""
SeekMoney-ai 深度对齐的商机线索生成。

作用: 按 SeekMoney 痛点发现框架输出「可验证商机」，禁止把吃瓜新闻标题包装成线索。
参考: https://github.com/liangdabiao/SeekMoney-ai
  - 表面痛点 → 根本原因 → 用户场景 → 情感强度
  - 市场格局：现有方案 → 未满足需求 → 机会缺口
  - 优先级：需求强度 + 市场规模 + 竞争度
  - MVP：核心功能、验证假设、首批用户
  - 拒绝：纯娱乐吃瓜、无付费闭环、无法两周验证
作者: LeadForge
创建时间: 2026-07-26
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.theme_recommend import list_cn_vc_market_industries
from app.tools.hotspot_sources import _item


# 吃瓜/时政/体育等：几乎不可能直接变成付费商机
_NON_COMMERCIAL_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"恋情|离婚|官宣|分手|出轨|塌房|丑闻|吃瓜",
        r"演唱会|粉丝见面|应援|打榜|内娱|流量明星",
        r"进球|决赛|联赛|转会|世界杯|奥运会|夺冠",
        r"电视剧|综艺|大结局|追剧|预告片",
        r"台风登陆|暴雨预警|地震速报",
        r"乘组在轨|航天员|发射成功",
        r"回应起诉|索赔\d+万|法院判决",  # 纯案件吃瓜（无 B 端作业痛点时）
    )
)

# 有商业/产品化潜力的信号词
_COMMERCIAL_SIGNAL_KEYS: tuple[str, ...] = (
    "涨价", "投诉", "退款", "维权", "假货", "难买", "难约", "排队", "爆单", "滞销",
    "获客", "转化", "客单", "复购", "私域", "投放", "投放成本",
    "预约", "到店", "核销", "线索", "跟进", "漏单",
    "裁员", "招聘", "兼职", "副业", "创业", "融资", "亏损", "降本",
    "AI", "智能", "自动化", "SaaS", "小程序", "App",
    "发票", "报销", "合规", "开票", "代账",
    "库存", "物流", "配送", "供应链",
    "医美", "牙科", "体检", "问诊", "处方",
    "教培", "试听", "课程", "托管",
    "餐饮", "外卖", "团购", "本地生活",
)


# 行业 → SeekMoney 结构化商机模板（不是新闻，是可验证的付费痛点）
# (surface_pain, root_cause, user_scenario, who_pays, mvp_2w, demand, market, competition)
_INDUSTRY_OPP_TEMPLATES: dict[str, list[tuple[str, str, str, str, str, int, int, int]]] = {
    "医疗健康": [
        ("患者预约后爽约/改期多，前台反复打电话", "缺少自动提醒与候补队列，人力靠口传", "口腔/医美诊所高峰期", "诊所老板/运营", "企微提醒+候补表单+爽约标签，5 家诊所试用", 4, 4, 3),
        ("咨询线索散落微信，顾问跟进漏单", "无线索池与SLA，靠个人记忆", "医美/齿科咨询顾问", "销售负责人", "线索表+超时催办+话术卡，两周测咨询→到店率", 5, 4, 2),
        ("患者反复问同样术前须知，医生讲到嘴干", "知识未产品化，口头重复", "门诊术前沟通", "医生/护士长", "知识库问答页+扫码自助，测重复提问下降", 4, 3, 3),
    ],
    "本地生活": [
        ("团购核销慢、对账乱，店员不愿推", "核销与收银割裂", "餐饮/丽人门店", "店长", "扫码核销+日对账表，3 店试点", 4, 4, 3),
        ("新客到店转化靠感觉，复购无触达", "无回访节奏与权益设计", "美业/家政", "老板", "到店后24h回访脚本+次卡页", 4, 3, 3),
    ],
    "企业服务": [
        ("销售线索评分全靠感觉，高意向被淹没", "缺少行为分与催办", "ToB 销售团队", "销售负责人", "表单行为分+每日Top线索，测跟进效率", 5, 4, 2),
        ("报销单据堆积，财务月底加班", "人工录入与规则审核割裂", "中小企业财务", "财务负责人", "OCR+规则清单试点一个部门", 4, 4, 3),
    ],
    "教育": [
        ("试听课约了不来，老师空档浪费", "提醒弱、候补无", "兴趣班/教培", "校长/教务", "预约提醒+候补+空档填补统计", 4, 3, 3),
        ("家长反复问课表与进度，班主任被刷屏", "信息未自助化", "K12/兴趣班", "班主任", "课表进度小程序页，测咨询量下降", 3, 3, 3),
    ],
    "消费电商": [
        ("客服重复回答物流/尺码，人力被拖死", "FAQ 未产品化", "电商客服", "运营负责人", "FAQ机器人+人工转接规则，测人效", 4, 4, 2),
        ("滞销款靠网红压价，利润被抽干", "缺清仓与私域转化路径", "品牌/工厂店", "电商负责人", "滞销清单+私域清仓页两周测动销", 4, 3, 3),
    ],
    "金融": [
        ("小微客户材料反复补交，进件周期长", "清单不清、状态不可见", "信贷/助贷顾问", "团队长", "材料清单+状态页，测一次通过率", 4, 3, 3),
    ],
    "广告营销": [
        ("投放素材产出慢，爆款难复用", "无结构模板与审核流", "投放/内容运营", "增长负责人", "爆款结构模板+批量改写+小预算试投", 4, 4, 2),
    ],
    "工具软件": [
        ("开发者重复搭 Agent 脚手架，交付慢", "缺领域模板与评测集", "独立开发/小团队", "技术负责人", "选定垂直场景模板+5 人试用", 4, 4, 2),
    ],
    "前沿技术": [
        ("企业想上大模型但不知从哪条流程切", "缺可两周验证的作业切片", "传统企业数字化", "业务负责人", "选一条高频流程做 Agent 切片试点", 4, 5, 2),
    ],
    "跨境出海": [
        ("询盘来了响应慢，时差导致丢单", "无线索分级与自动回复", "跨境销售", "外贸负责人", "询盘自动分级+时区工作时间回复", 4, 4, 3),
    ],
    "物流": [
        ("异常件靠司机微信群报，客服对不上", "状态与工单割裂", "仓配/同城配送", "调度主管", "异常工单表+超时升级，测响应时长", 4, 3, 3),
    ],
    "房产地产": [
        ("带看后客户失联，经纪人跟进无节奏", "缺回访脚本与意向分层", "中介门店", "店长", "带看后分层回访+意向表，测成交跟进率", 4, 3, 3),
    ],
    "旅游": [
        ("旺季咨询爆炸，报价重复人工做", "报价未产品化", "旅行社/目的地商家", "计调/店长", "报价模板+库存日历两周试点", 3, 3, 3),
    ],
    "汽车出行": [
        ("到店保养预约与车间排程对不齐", "前台与车间系统割裂", "汽修连锁", "店长", "预约→工位看板最小闭环，3 店试用", 4, 3, 3),
    ],
    "产业升级": [
        ("车间报工靠纸单，质量追溯断档", "数据采集未产品化", "传统工厂班组", "生产主管", "扫码报工+异常清单两周试点", 4, 4, 3),
    ],
}

_DEFAULT_TEMPLATES = [
    ("线索跟进靠人肉，高意向被淹没", "无线索池与优先级", "中小商家销售", "老板/销售负责人", "线索表+超时提醒，测跟进完整率", 4, 3, 3),
    ("获客内容难复用，投放不稳定", "缺可复制的内容结构", "运营一人公司", "运营/创始人", "一套爆款结构+3条改写试投", 3, 3, 3),
    ("预约与回访割裂，到店后无触达", "流程断点", "到店服务商家", "店长", "预约确认+回访脚本两周试点", 4, 3, 3),
]


def _as_clue(
    *,
    title: str,
    url: str,
    snippet: str,
    heat: float,
    meta: dict[str, Any],
) -> dict[str, Any]:
    """构造带 lane=seekmoney 的线索条目。"""

    row = _item(
        title=title,
        url=url,
        snippet=snippet,
        provider="seekmoney:framework",
        heat=heat,
        meta={**meta, "lane": "seekmoney", "methodology": "seekmoney"},
    )
    row["lane"] = "seekmoney"
    return row


def is_non_commercial_noise(title: str) -> bool:
    """判断标题是否为吃瓜/体育/娱乐等无商机噪声。"""

    text = (title or "").strip()
    if len(text) < 4:
        return True
    return any(p.search(text) for p in _NON_COMMERCIAL_PATTERNS)


def has_commercial_signal(title: str, snippet: str = "") -> bool:
    """标题/摘要是否含可产品化商业信号。"""

    blob = f"{title} {snippet}".lower()
    if is_non_commercial_noise(title):
        return False
    return any(k.lower() in blob for k in _COMMERCIAL_SIGNAL_KEYS)


def _templates_for_industry(industry: str) -> list[tuple[str, str, str, str, str, int, int, int]]:
    """取行业模板；无则用默认。"""

    name = (industry or "").strip()
    if name in _INDUSTRY_OPP_TEMPLATES:
        return list(_INDUSTRY_OPP_TEMPLATES[name])
    for key, tpls in _INDUSTRY_OPP_TEMPLATES.items():
        if key in name or name in key:
            return list(tpls)
    return list(_DEFAULT_TEMPLATES)


def _priority(demand: int, market: int, competition: int) -> int:
    """SeekMoney 风格综合分：需求+市场+（6-竞争）便于排序。"""

    return int(demand) + int(market) + max(0, 6 - int(competition))


def build_seekmoney_clues(
    *,
    platform_rows: list[dict[str, Any]],
    vc_rows: list[dict[str, Any]],
    github_rows: list[dict[str, Any]],
    industry: str = "",
    limit: int = 12,
) -> list[dict[str, Any]]:
    """
    按 SeekMoney-ai 框架生成商机线索（非新闻复述）。

    策略：
    1) 以创投标准行业的结构化痛点模板为主（可验证付费闭环）
    2) 仅把「含商业信号」的热搜/创投/GitHub 作为 evidence，挂到线索上
    3) 标题写成「商机：…」，禁止「围绕「新闻标题」的可产品化痛点」

    Args:
        platform_rows: 平台热搜。
        vc_rows: 创投。
        github_rows: GitHub/本土项目。
        industry: 用户筛选行业（空=多赛道）。
        limit: 条数上限。

    Returns:
        seekmoney 通道条目列表。
    """

    preferred = (industry or "").strip()
    clues: list[dict[str, Any]] = []

    # 收集可用证据（过滤吃瓜）
    evidence: list[dict[str, Any]] = []
    for src in list(vc_rows) + list(github_rows) + list(platform_rows):
        title = str(src.get("title") or src.get("name") or "").strip()
        snip = str(src.get("snippet") or "")
        if not title or is_non_commercial_noise(title):
            continue
        # VC/GitHub 默认可作证据；热搜需商业信号
        provider = str(src.get("provider") or "")
        if provider.startswith(("newsnow", "trendradar")) and not has_commercial_signal(title, snip):
            continue
        evidence.append(src)

    # 目标行业列表
    if preferred:
        industries = [preferred]
    else:
        industries = [str(x.get("name") or "") for x in list_cn_vc_market_industries() if x.get("name")]
        # 优先覆盖有模板的赛道
        preferred_order = list(_INDUSTRY_OPP_TEMPLATES.keys())
        industries = [i for i in preferred_order if i in industries] + [
            i for i in industries if i not in preferred_order
        ]

    ev_i = 0
    for ind_name in industries:
        for tpl in _templates_for_industry(ind_name):
            surface, root, scenario, who_pays, mvp, d, m, c = tpl
            # 挂一条相关证据（轮换）
            ev_title = ""
            ev_url = f"leadforge://seekmoney/{ind_name}/{len(clues)}"
            if evidence:
                ev = evidence[ev_i % len(evidence)]
                ev_i += 1
                ev_title = str(ev.get("title") or "")[:60]
                ev_url = str(ev.get("url") or ev_url)
            priority = _priority(d, m, c)
            title = f"商机：{ind_name} · {surface[:28]}"
            snippet = (
                f"表面痛点:{surface[:40]} · 根因:{root[:36]} · 场景:{scenario} · "
                f"付费方:{who_pays} · 两周:{mvp[:40]} · "
                f"评分D{d}/M{m}/C{c}=P{priority}"
                + (f" · 证据:{ev_title}" if ev_title else "")
            )[:240]
            clues.append(
                _as_clue(
                    title=title[:140],
                    url=ev_url,
                    snippet=snippet,
                    heat=40 + priority * 4 + float(d),
                    meta={
                        "lane": "seekmoney",
                        "platform": "seekmoney",
                        "platform_label": "SeekMoney商机",
                        "industry": ind_name,
                        "surface_pain": surface,
                        "root_cause": root,
                        "user_scenario": scenario,
                        "who_pays": who_pays,
                        "mvp_2w": mvp,
                        "demand_score": d,
                        "market_score": m,
                        "competition_score": c,
                        "priority_score": priority,
                        "validation_hypothesis": f"若解决「{surface[:20]}」，付费方愿意在两周内试用 MVP",
                        "first_users": who_pays,
                        "data_quality": "exploratory",
                        "source_titles": [ev_title] if ev_title else [],
                        "industry_basis": "seekmoney_framework",
                        "confidence": min(0.85, 0.45 + priority * 0.03),
                        "opc_fit": "一人可两周验证：表单/脚本/小工具切片，非重资产",
                    },
                )
            )
            if len(clues) >= limit:
                return _cap_sorted(clues, limit)

    # 若仍不足：从强商业信号热搜提炼「信号驱动」商机（仍禁止新闻标题当标题）
    for h in platform_rows:
        if len(clues) >= limit:
            break
        title = str(h.get("title") or "").strip()
        if not has_commercial_signal(title, str(h.get("snippet") or "")):
            continue
        ind = preferred or "本地生活"
        surface = f"市场出现「{title[:18]}」相关焦虑/需求外溢"
        clues.append(
            _as_clue(
                title=f"商机：验证「{title[:16]}」背后的付费闭环",
                url=str(h.get("url") or f"leadforge://seekmoney/sig/{len(clues)}"),
                snippet=(
                    f"表面信号:{title[:40]} · 根因:待访谈确认 · "
                    f"SeekMoney下一步:聚类同类讨论→找付费方→两周落地页验证 · 质量:exploratory"
                )[:220],
                heat=float(h.get("heat") or 40) * 0.05 + 55,
                meta={
                    "lane": "seekmoney",
                    "platform_label": "SeekMoney×信号",
                    "industry": ind,
                    "surface_pain": surface,
                    "root_cause": "热搜仅为弱信号，需评论/访谈确认根因",
                    "user_scenario": "待补齐用户场景",
                    "who_pays": "待验证付费方",
                    "mvp_2w": "5 人访谈+一页方案+预约表，验证是否有人愿意付费咨询",
                    "demand_score": 2,
                    "market_score": 2,
                    "competition_score": 3,
                    "priority_score": 7,
                    "data_quality": "exploratory",
                    "source_titles": [title],
                    "industry_basis": "commercial_signal_only",
                    "confidence": 0.35,
                },
            )
        )

    return _cap_sorted(clues, limit)


def _cap_sorted(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """按 priority_score / heat 排序截断。"""

    def _key(r: dict[str, Any]) -> tuple[float, float]:
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        try:
            p = float(meta.get("priority_score") or 0)
        except (TypeError, ValueError):
            p = 0.0
        try:
            h = float(r.get("heat") or 0)
        except (TypeError, ValueError):
            h = 0.0
        return (p, h)

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in sorted(rows, key=_key, reverse=True):
        t = str(r.get("title") or "")
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(r)
        if len(out) >= limit:
            break
    return out
