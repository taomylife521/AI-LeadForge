# -*- coding: utf-8 -*-
"""
AI 重构通道：一痛点一条、讲清为何选该项目、组合更优解与成功概率。

作用:
  - 禁止「多个仓库套同一句痛点」；
  - 过滤与作业无关的热榜噪声（汇编示例、纯书单等）；
  - 每条输出：真实痛点、为何此项目、组合建议、成功概率。
作者: LeadForge
创建时间: 2026-07-27
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote


def _item(
    *,
    title: str,
    url: str,
    snippet: str = "",
    provider: str,
    heat: float = 0.0,
    meta: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构造热点条目。"""

    return {
        "title": (title or "").strip() or url,
        "url": (url or "").strip(),
        "snippet": (snippet or "").strip(),
        "provider": provider,
        "heat": float(heat),
        "meta": meta or {},
        "lane": "ai_rebuild",
    }


# 明显不能当「AI 重构产品」主仓库的噪声
_JUNK_REPO_KEYS: tuple[str, ...] = (
    "bare-metal",
    "bare metal",
    "awesome-",
    "free-programming",
    "interview",
    "leetcode",
    "javaguide",
    "hello-algo",
    "cs-self-learning",
    "system-prompts",
    "some-many-book",
    "ai-agent-book",
    "books",
    "cookbook",
    "corebunch",
)

# 能力原型：一原型最多一张卡（去重核心）
# match_keys 命中仓库名/描述；每条痛点/理由/组合/成功率各自不同
_ARCHETYPES: list[dict[str, Any]] = [
    {
        "id": "visual_rag_ops",
        "label": "可视化知识库/工作流",
        "match": ("dify", "ragflow", "fastgpt", "maxkb", "langchain-chatchat", "qanything"),
        "industry": "SaaS·知识库运营",
        "pain": "业务知识散落在网盘/群聊/口口相传，新人上手慢且回答口径不一致，客服/店长被重复提问拖死",
        "who": "业务负责人/店长/客服主管",
        "mvp": "导入本部门 50 份文档 → 搭问答页 → 5 名一线试用，统计「重复提问下降率」",
        "why": "该仓库提供可视化 RAG/工作流，非研发也能先跑通「内部知识问答」闭环，适合验证付费意愿",
        "combo": "更优组合：本仓库编排 + 企微/飞书文档作知识源。不必再叠 LangChain，除非要深度定制评测与权限",
        "success": 72,
        "success_note": "有现成 UI，两周可演示；风险在知识清洗与权限，不在框架选型",
    },
    {
        "id": "dev_rag_framework",
        "label": "研发级编排框架",
        "match": ("langchain", "llama-index", "llamaindex", "haystack", "langgraph"),
        "industry": "SaaS·AI工程",
        "pain": "要做强定制检索/多步 Agent（私有协议、评测集、复杂权限），成品编排平台扩展不够，必须自建流水线",
        "who": "有后端研发的技术负责人",
        "mvp": "用框架搭「单领域 FAQ」最小链 + 离线评测 30 题，看命中率是否 ≥70%",
        "why": "这是开发框架不是成品：适合有研发编制时控链路；把框架直接当产品卖给无技术门店，路径不通",
        "combo": "更优组合：框架作底层 + Dify/自研前端作交付。单独 fork 框架做「店长工具」成功概率显著更低",
        "success": 40,
        "success_note": "有研发可到中等；无研发团队单独用框架交付，成功概率低",
    },
    {
        "id": "flow_builder",
        "label": "低代码 AI 流程",
        "match": ("langflow", "flowise", "n8n"),
        "industry": "SaaS·流程自动化",
        "pain": "运营想把「线索清洗→跟进提醒→周报」串起来，但每次找研发改脚本，迭代周期以周计",
        "who": "增长/运营负责人",
        "mvp": "拖拽搭一条「表单线索→打分→企微提醒」流，跑 20 条真实线索看漏跟率",
        "why": "低代码画布降低改流程成本，适合验证自动化 SOP，而不是再写一套聊天框架",
        "combo": "更优组合：流程画布 + 轻量表格/CRM；若只要知识问答，优先可视化 RAG 而不是流程画布",
        "success": 58,
        "success_note": "流程清晰时中等偏上；画布过重会拖垮两周验证",
    },
    {
        "id": "agent_runtime",
        "label": "Agent/技能运行时",
        "match": ("hermes-agent", "superpowers", "agency-agent", "autogen", "crewai", "metagpt", "openclaw"),
        "industry": "SaaS·研发效能",
        "pain": "研发协作里重复的开 issue、写测试脚手架、盯流水线失败，靠人肉值班，质检口径飘",
        "who": "技术负责人/TL",
        "mvp": "选 1 条高频手工链（如失败日志摘要→建 issue），Agent 跑通 10 次，统计节省工时",
        "why": "Agent/技能运行时擅长「多步工具调用」，对研发效能场景比通用聊天框更贴",
        "combo": "更优组合：Agent 运行时 + 仓库 CI/Issue API；不要拿它硬套到店预约/美业获客",
        "success": 55,
        "success_note": "团队有 API/工程基础时中等；对纯本地生活门店场景成功率低",
    },
    {
        "id": "creator_content",
        "label": "创作者内容生产",
        "match": ("moneyprinter", "short-video", "xiaohongshu", "media-crawler", "copywrit", "script-generator", "content-agent"),
        "industry": "SaaS·创作者工具",
        "pain": "垂类内容（生活技巧/本地种草）选题与脚本产能不稳，博主只能靠灵感，难持续更新与变现",
        "who": "自媒体/知识博主",
        "mvp": "一周产出 10 条垂类脚本并发布，看完播/收藏是否高于基线",
        "why": "内容生成与采集类项目直接对应「产能」瓶颈，比通用 Agent 框架更贴近创作者付费点",
        "combo": "更优组合：内容生成 + 简单审核清单 + 发布日历；再外挂线索表单做变现",
        "success": 63,
        "success_note": "选题垂直、有分发渠道时较高；纯工具无流量则偏低",
    },
    {
        "id": "booking_local",
        "label": "预约/到店",
        "match": ("booking", "appointment", "reservation", "cal.com", "calendly"),
        "industry": "本地·到店预约",
        "pain": "到店预约改期/爽约多，前台反复电话确认，高峰空档与撞档并存",
        "who": "店长/前台主管",
        "mvp": "上线提醒+候补表，3 家店两周对比爽约率",
        "why": "预约域开源/SaaS 能力可迁移到到店场景，痛点可计量（爽约率）",
        "combo": "更优组合：预约提醒 + 候补队列 + 简易核销；不要一上来上复杂 Agent",
        "success": 68,
        "success_note": "指标清晰、决策人在店长，两周可证伪",
    },
    {
        "id": "crm_leads",
        "label": "线索/CRM",
        "match": ("crm", "lead", "sales", "outbound", "erpnext", "twenty"),
        "industry": "B2B·线索跟进",
        "pain": "咨询线索散落微信，高意向被淹没，跟进全靠个人记忆，漏单无法追责",
        "who": "销售负责人",
        "mvp": "线索表+超时催办+每日 Top10，测跟进闭环率",
        "why": "CRM/线索类项目解决的是「池子+SLA」，与「有没有大模型」无关的真付费痛点",
        "combo": "更优组合：轻量 CRM + 行为打分；大模型只做摘要/话术，不作主系统",
        "success": 70,
        "success_note": "销售团队有意愿改流程时高；只上 AI 聊天不上池子则低",
    },
    {
        "id": "doc_ocr",
        "label": "单据/OCR",
        "match": ("paddleocr", "invoice-ocr", "receipt ocr", "单据", "报销ocr", "发票识别"),
        "industry": "SaaS·单据智能化",
        "pain": "报销/进件材料堆积，财务月末加班录入，错票难拦",
        "who": "财务负责人",
        "mvp": "一个部门 OCR+规则清单试点，测一次通过率与人均单据量",
        "why": "OCR/单据项目对应明确降本指标，比空泛「AI 赋能办公」可验证",
        "combo": "更优组合：OCR + 规则引擎 + 人工抽检；不必上重型 Agent",
        "success": 66,
        "success_note": "单据格式稳定时较高；版式极度混乱会拖低",
    },
]

# 行业场景（用户选了行业时）：同样一场景一卡
_INDUSTRY_REBUILD: dict[str, list[dict[str, Any]]] = {
    "生活技巧": [
        {
            "id": "life_script",
            "pain": "生活技巧短视频/图文选题与脚本产出慢，难周更，完播与收藏不稳",
            "who": "自媒体/知识博主",
            "mvp": "生成+人工审核一周 10 条并发布，对比完播/收藏",
            "match": ("content", "script", "video", "xiaohongshu", "life tips", "lifehack", "creator", "copy"),
            "why": "命中「产能」而不是「再做一个通用聊天框」",
            "combo": "内容生成仓库 + 审核清单 + 发布日历；变现再挂咨询表单",
            "success": 64,
            "success_note": "有账号与垂直定位时中高",
        },
        {
            "id": "life_kb",
            "pain": "居家/收纳/烹饪技巧散落收藏夹，用时找不到，重复搜索浪费时间",
            "who": "工具产品方/个人开发者",
            "mvp": "生活技巧知识库问答，5 人试用检索命中",
            "match": ("rag", "knowledge", "dify", "notes", "checklist", "habit", "bookmark", "howto"),
            "why": "个人知识检索是可感知体验，适合做小工具验证留存",
            "combo": "可视化 RAG + 用户自有收藏导入；别用裸框架硬做 App",
            "success": 57,
            "success_note": "留存难，需场景足够窄",
        },
        {
            "id": "life_monetize",
            "pain": "技巧内容有播放无线索，难向咨询/社群/小产品转化",
            "who": "创作者/运营",
            "mvp": "内容页+咨询表/社群入口，两周计咨询量",
            "match": ("crm", "lead", "landing", "newsletter", "audience", "form"),
            "why": "变现卡在「线索承接」而不是模型本身",
            "combo": "内容工具 + 轻量线索表；不要只堆大模型",
            "success": 60,
            "success_note": "有稳定流量时中高",
        },
    ],
}


def industry_github_queries(industry: str) -> list[str]:
    """按行业构造 GitHub 检索词。"""

    name = (industry or "").strip()
    if not name:
        return [
            "dify OR ragflow stars:>200",
            "appointment booking saas stars:>50",
            "crm lead scoring stars:>50",
        ]
    queries = [f"{name} AI OR chatbot OR agent", f"{name} saas OR tool"]
    if "生活" in name or "技巧" in name:
        queries.extend(
            [
                "life tips OR lifehack OR lifestyle stars:>30",
                "personal knowledge OR checklist OR habit tracker",
                "xiaohongshu OR short-video content tool",
            ]
        )
    for key, rows in _INDUSTRY_REBUILD.items():
        if key in name or name in key:
            for row in rows:
                mk = row.get("match") or ()
                if mk:
                    queries.append(" OR ".join(list(mk)[:4]) + " stars:>20")
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        qn = q.strip()
        if qn and qn not in seen:
            seen.add(qn)
            out.append(qn)
    return out[:8]


def scenarios_for_industry(industry: str) -> list[dict[str, Any]]:
    """返回行业场景字典列表。"""

    name = (industry or "").strip() or "综合"
    for key, rows in _INDUSTRY_REBUILD.items():
        if key == name or key in name or name in key:
            return list(rows)
    return [
        {
            "id": f"gen_content_{name}",
            "pain": f"「{name}」垂类内容/获客素材难持续产出，转化路径不清",
            "who": "运营/老板",
            "mvp": f"两周产出 {name} 垂类 8 条素材并测打开/咨询",
            "match": ("content", "creator", "marketing", "copy", "seo"),
            "why": "先验证内容-咨询漏斗，再谈模型",
            "combo": "内容生成 + 落地页/表单；避免只上聊天演示",
            "success": 52,
            "success_note": "行业词过宽时成功率下降",
        },
        {
            "id": f"gen_faq_{name}",
            "pain": f"「{name}」一线反复回答同类问题，培训靠口传、口径乱",
            "who": "客服/店长",
            "mvp": f"沉淀 {name} FAQ 知识库，5 人试用",
            "match": ("dify", "rag", "knowledge", "faq", "chat"),
            "why": "重复问答可计量，适合两周验证",
            "combo": "可视化 RAG 优先于裸框架",
            "success": 61,
            "success_note": "有现成文档语料时更高",
        },
        {
            "id": f"gen_lead_{name}",
            "pain": f"「{name}」线索跟进慢、漏单，过程不可见",
            "who": "销售/顾问",
            "mvp": f"{name} 线索表+超时催办，测闭环",
            "match": ("crm", "lead", "sales"),
            "why": "漏单是真金白银，不依赖炫技模型",
            "combo": "轻量 CRM 为主，模型只做摘要",
            "success": 65,
            "success_note": "团队肯改流程是前提",
        },
    ]


def _blob(row: dict[str, Any]) -> str:
    meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    return " ".join(
        str(x or "")
        for x in (
            row.get("title"),
            row.get("snippet"),
            row.get("name"),
            meta.get("query"),
        )
    ).lower()


def _is_junk(row: dict[str, Any]) -> bool:
    """与 AI 重构产品无关的热榜噪声。"""

    text = _blob(row)
    return any(k in text for k in _JUNK_REPO_KEYS)


def _score_keys(text: str, keys: tuple[str, ...] | list[str]) -> int:
    score = 0
    for k in keys:
        kl = str(k).lower()
        if kl and kl in text:
            score += 4 + min(len(kl), 8)
    return score


def _repo_name(row: dict[str, Any]) -> str:
    return str(row.get("title") or row.get("name") or "").strip()


def _repo_url(row: dict[str, Any]) -> str:
    url = str(row.get("url") or "").strip()
    name = _repo_name(row)
    if url.startswith("http"):
        return url
    if "/" in name and " " not in name:
        return f"https://github.com/{name}"
    return f"https://github.com/search?q={quote(name)}&type=repositories"


def _pick_best(
    pool: list[dict[str, Any]],
    match_keys: tuple[str, ...] | list[str],
    used: set[str],
) -> tuple[Optional[dict[str, Any]], int, list[dict[str, Any]]]:
    """
    在池中选最佳主项目，并返回同原型候选（供组合说明）。

    Returns:
        (best_row, score, runners_up)
    """

    ranked: list[tuple[int, dict[str, Any]]] = []
    for g in pool:
        uid = str(g.get("url") or g.get("title") or "")
        if not uid or uid in used or _is_junk(g):
            continue
        sc = _score_keys(_blob(g), match_keys)
        if sc > 0:
            ranked.append((sc + int(float(g.get("heat") or 0) / 50000), g))
    ranked.sort(key=lambda x: -x[0])
    if not ranked:
        return None, 0, []
    best_sc, best = ranked[0]
    runners = [g for _, g in ranked[1:4]]
    return best, best_sc, runners


def _success_clamp(base: int, fit: int) -> int:
    """结合匹配分微调成功概率。"""

    adj = max(-15, min(15, (fit - 8)))
    return int(max(15, min(88, base + adj)))


def _make_card(
    *,
    industry: str,
    pain: str,
    who: str,
    mvp: str,
    why: str,
    combo: str,
    success: int,
    success_note: str,
    primary: dict[str, Any],
    runners: list[dict[str, Any]],
    archetype_id: str,
    fit: int,
) -> dict[str, Any]:
    """组装无重复、可解释的 AI 重构卡。"""

    repo = _repo_name(primary)
    url = _repo_url(primary)
    short_pain = pain if len(pain) <= 42 else pain[:40] + "…"
    title = f"AI重构：{industry} · {short_pain}"

    runner_names = [_repo_name(r) for r in runners if _repo_name(r) and _repo_name(r) != repo][:2]
    alt = ("；备选 " + " / ".join(runner_names)) if runner_names else ""
    if "{repo}" in why:
        why_full = why.replace("{repo}", repo)
    else:
        why_full = f"「{repo}」：{why}"

    snippet = (
        f"痛点：{pain} · 为何此项目：{why_full} · "
        f"组合：{combo}{alt} · 成功概率约 {success}%（{success_note}）· 付费方:{who}"
    )[:320]

    return _item(
        title=title,
        url=url,
        snippet=snippet,
        provider="insight:ai_rebuild",
        heat=float(primary.get("heat") or 40) + success / 5,
        meta={
            "lane": "ai_rebuild",
            "platform_label": "AI重构",
            "industry": industry,
            "surface_pain": pain,
            "who_pays": who,
            "mvp_2w": mvp,
            "why_project": why_full,
            "combo_better": combo + alt,
            "success_prob": success,
            "success_note": success_note,
            "github": repo,
            "project_url": url,
            "alternatives": runner_names,
            "archetype_id": archetype_id,
            "fit_score": fit,
            "industry_basis": "unique_pain+project_fit",
            "openable": True,
            "confidence": round(success / 100.0, 2),
            "methodology": "deduped-archetype",
        },
    )


def build_ai_rebuild_cards(
    *,
    github_rows: list[dict[str, Any]],
    vc_rows: list[dict[str, Any]],
    industry: str = "",
    industry_keys: Optional[list[str]] = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    生成去重后的 AI 重构卡片。

    Args:
        github_rows / vc_rows: 候选。
        industry: 用户行业；空=按能力原型去重。
        industry_keys: 行业词（兼容参数）。
        limit: 上限。

    Returns:
        卡片列表（痛点不重复）。
    """

    _ = industry_keys  # 预留
    preferred = (industry or "").strip()
    pool = [
        r
        for r in (list(github_rows) + list(vc_rows))
        if isinstance(r, dict) and _repo_name(r) and not _is_junk(r)
    ]
    used: set[str] = set()
    cards: list[dict[str, Any]] = []
    used_pain: set[str] = set()

    if preferred:
        for sc in scenarios_for_industry(preferred):
            if len(cards) >= limit:
                break
            pain = str(sc["pain"])
            if pain in used_pain:
                continue
            best, fit, runners = _pick_best(pool, sc.get("match") or (), used)
            if not best:
                # 无匹配项目：仍给场景卡，链到搜索，避免假配对
                q = " OR ".join(list(sc.get("match") or [])[:3]) or preferred
                search = f"https://github.com/search?q={quote(q)}&type=repositories"
                fake = {"title": f"search:{q}", "url": search, "snippet": "待匹配相关开源", "heat": 30}
                card = _make_card(
                    industry=preferred,
                    pain=pain,
                    who=str(sc["who"]),
                    mvp=str(sc["mvp"]),
                    why="当前热榜/检索未命中足够相关的主仓库，请先点开搜索筛选后再立项",
                    combo=str(sc.get("combo") or ""),
                    success=max(20, int(sc.get("success") or 40) - 25),
                    success_note="缺主仓库时成功率下调",
                    primary=fake,
                    runners=[],
                    archetype_id=str(sc.get("id") or pain),
                    fit=0,
                )
                cards.append(card)
                used_pain.add(pain)
                continue
            used.add(str(best.get("url") or best.get("title") or ""))
            used_pain.add(pain)
            success = _success_clamp(int(sc.get("success") or 50), fit)
            cards.append(
                _make_card(
                    industry=preferred,
                    pain=pain,
                    who=str(sc["who"]),
                    mvp=str(sc["mvp"]),
                    why=str(sc.get("why") or ""),
                    combo=str(sc.get("combo") or ""),
                    success=success,
                    success_note=str(sc.get("success_note") or ""),
                    primary=best,
                    runners=runners,
                    archetype_id=str(sc.get("id") or pain),
                    fit=fit,
                )
            )
        return cards[:limit]

    # 全网：按能力原型去重，禁止多仓库共用一句痛点
    for arch in _ARCHETYPES:
        if len(cards) >= limit:
            break
        pain = str(arch["pain"])
        if pain in used_pain:
            continue
        best, fit, runners = _pick_best(pool, arch.get("match") or (), used)
        if not best:
            continue
        used.add(str(best.get("url") or best.get("title") or ""))
        used_pain.add(pain)
        success = _success_clamp(int(arch.get("success") or 50), fit)
        cards.append(
            _make_card(
                industry=str(arch["industry"]),
                pain=pain,
                who=str(arch["who"]),
                mvp=str(arch["mvp"]),
                why=str(arch.get("why") or ""),
                combo=str(arch.get("combo") or ""),
                success=success,
                success_note=str(arch.get("success_note") or ""),
                primary=best,
                runners=runners,
                archetype_id=str(arch["id"]),
                fit=fit,
            )
        )

    return cards[:limit]
