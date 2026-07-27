# -*- coding: utf-8 -*-
"""
LeadForge 主题实时推荐。

作用: 按 Theme Pack + 可选行业，调用真实模型生成行业内可选题（禁止 mock；支持换批去重）。
作者: LeadForge
创建时间: 2026-07-23
"""

from __future__ import annotations

import json
import random
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.envelope import ModelRoute
from app.llm import LLMClient
from app.settings import data_dir, list_theme_packs


# 创投/项目库常用「所属行业」目录（与创业邦等平台对齐；「看热点」默认用此列表）
CN_VC_MARKET_INDUSTRIES: list[dict[str, str]] = [
    {"id": "culture-entertainment", "name": "文化娱乐", "hint": "影视综、内容、娱乐消费"},
    {"id": "consumer-ecommerce", "name": "消费电商", "hint": "品牌、零售、电商与新消费"},
    {"id": "auto-mobility", "name": "汽车出行", "hint": "整车、出行、汽后与智能驾驶"},
    {"id": "education", "name": "教育", "hint": "教培、职业培训、教育科技"},
    {"id": "finance", "name": "金融", "hint": "支付、信贷、理财、保险科技"},
    {"id": "enterprise-service", "name": "企业服务", "hint": "SaaS、降本增效、ToB 工具"},
    {"id": "industry-upgrade", "name": "产业升级", "hint": "传统产业数字化与升级改造"},
    {"id": "frontier-tech", "name": "前沿技术", "hint": "AI、机器人、前沿科研转化"},
    {"id": "healthcare", "name": "医疗健康", "hint": "医疗、医药、数字健康、康养"},
    {"id": "advanced-manufacturing", "name": "先进制造", "hint": "高端装备、智能制造"},
    {"id": "semiconductor", "name": "通信/半导体", "hint": "芯片、通信设备与基础件"},
    {"id": "iot-hardware", "name": "物联网/硬件", "hint": "IoT、传感、联网设备"},
    {"id": "tool-software", "name": "工具软件", "hint": "效率工具、开发者与个人软件"},
    {"id": "social-network", "name": "社交网络", "hint": "社交、社区、兴趣网络"},
    {"id": "agriculture", "name": "农林牧渔", "hint": "农业、养殖、农产品流通"},
    {"id": "energy-env", "name": "能源环保", "hint": "新能源、节能、环保治理"},
    {"id": "local-life", "name": "本地生活", "hint": "到店、外卖、本地服务"},
    {"id": "sports-games", "name": "体育游戏", "hint": "体育、电竞、游戏"},
    {"id": "cross-border", "name": "跨境出海", "hint": "跨境电商、国际化与出海服务"},
    {"id": "real-estate", "name": "房产地产", "hint": "房产、物业、空间服务"},
    {"id": "tourism", "name": "旅游", "hint": "文旅、出行预订、目的地服务"},
    {"id": "ad-marketing", "name": "广告营销", "hint": "广告、营销技术、增长"},
    {"id": "smart-hardware", "name": "智能硬件", "hint": "消费电子、智能终端"},
    {"id": "logistics", "name": "物流", "hint": "仓储、快递、供应链"},
    {"id": "blockchain", "name": "区块链", "hint": "链上应用、基础设施"},
    {"id": "traditional-manufacturing", "name": "传统制造", "hint": "传统工厂与制造服务"},
    {"id": "metaverse", "name": "元宇宙", "hint": "XR、虚拟空间与数字人"},
    {"id": "other", "name": "其他", "hint": "未归入以上类目"},
]


# Theme Pack → 建议行业（更细颗粒度；用户仍可自由自定义）
INDUSTRIES_BY_PACK: dict[str, list[dict[str, str]]] = {
    "local-service-leadgen": [
        {"id": "dental-implant", "name": "牙科·种植/矫正", "hint": "种植牙/隐形矫正咨询预约，客单高"},
        {"id": "dental-kids", "name": "牙科·儿童齿科", "hint": "涂氟/窝沟封闭/儿童早期矫正"},
        {"id": "beauty-skin", "name": "美业·皮肤管理", "hint": "光子/水光体验课转化"},
        {"id": "beauty-nail", "name": "美业·美甲美睫", "hint": "新客体验卡与复购会员"},
        {"id": "edu-daycare", "name": "少儿·托管接送", "hint": "双职工放学托管/晚托"},
        {"id": "edu-interest", "name": "少儿·兴趣班试听", "hint": "编程/绘画/钢琴试听转化"},
        {"id": "fitness-pt", "name": "健身·私教体验", "hint": "减脂营/体测体验课"},
        {"id": "home-deepclean", "name": "家政·深度保洁", "hint": "开荒/油烟机/家电清洗"},
        {"id": "home-organize", "name": "家政·收纳整理", "hint": "搬家收纳/衣橱整理"},
        {"id": "catering-banquet", "name": "餐饮·宴会预订", "hint": "婚宴/生日宴桌数预订"},
        {"id": "pet-grooming", "name": "宠物·洗护寄养", "hint": "洗澡美容/寄养到店"},
        {"id": "pet-vet", "name": "宠物·到店医疗", "hint": "疫苗/绝育/体检预约"},
        {"id": "auto-film", "name": "汽后·贴膜保养", "hint": "贴膜/镀晶/小保养到店"},
        {"id": "legal-tax", "name": "本地·财税代理", "hint": "小微代账/开票咨询预约"},
        {"id": "elder-physio", "name": "康养·理疗体检", "hint": "中医理疗/体检套餐体验"},
    ],
    "_default": [
        {"id": "saas-doc-ai", "name": "SaaS·文档智能化", "hint": "PDF/OCR/发票报销自动化"},
        {"id": "saas-creator", "name": "SaaS·创作者工具", "hint": "小红书/短视频配图与选题"},
        {"id": "saas-devtool", "name": "SaaS·开发者工具", "hint": "API检测/模型质量/Agent工作台"},
        {"id": "saas-privacy", "name": "SaaS·本地隐私工具", "hint": "离线/端侧/不上传数据场景"},
        {"id": "b2b-geo", "name": "B2B·出海GEO", "hint": "跨境内容可见度与询盘"},
        {"id": "b2b-lead", "name": "B2B·垂直线索", "hint": "细分行业外联落地页"},
        {"id": "content-xhs", "name": "内容·小红书获客", "hint": "笔记爆款后到店/私域"},
        {"id": "local-appointment", "name": "本地·到店预约核销", "hint": "高客单到店体验课"},
        {"id": "fin-indie", "name": "金融·个人理财助手", "hint": "记账/报销/投研轻工具"},
        {"id": "edu-reading", "name": "教育·阅读学习工具", "hint": "注释/伴读/家长场景"},
    ],
}


def _custom_industries_path() -> Path:
    return data_dir() / "custom_industries.json"


def _slug_industry(name: str) -> str:
    """从行业名生成稳定 id。"""

    text = (name or "").strip().lower()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", text)
    return text[:48] or f"custom-{uuid.uuid4().hex[:8]}"


def load_custom_industries(theme_pack: str = "") -> list[dict[str, str]]:
    """读取用户自定义行业。"""

    path = _custom_industries_path()
    if not path.exists():
        return []
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    items = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        return []
    pack = (theme_pack or "").strip()
    out: list[dict[str, str]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        # 过滤历史脏数据（单字母等），避免污染行业识别
        if name.isascii() and len(name) < 3:
            continue
        if not name.isascii() and len(name) < 2:
            continue
        if pack and row.get("theme_pack") and row.get("theme_pack") != pack:
            continue
        out.append(
            {
                "id": str(row.get("id") or _slug_industry(name)),
                "name": name,
                "hint": str(row.get("hint") or name),
                "custom": "true",
            }
        )
    return out


def save_custom_industry(theme_pack: str, name: str, hint: str = "") -> dict[str, str]:
    """
    持久化一条自定义行业（去重）。

    Returns:
        保存后的行业对象。
    """

    name = (name or "").strip()
    if not name:
        raise ValueError("行业名不能为空")
    # 拒绝过短/噪音名，避免匹配时被英文字母误命中（如 "c"）
    if name.isascii() and len(name) < 3:
        raise ValueError("英文行业名至少 3 个字符")
    if not name.isascii() and len(name) < 2:
        raise ValueError("行业名至少 2 个字")
    pack = (theme_pack or "local-service-leadgen").strip()
    path = _custom_industries_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    doc: dict[str, Any] = {"items": []}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("items"), list):
                doc = loaded
        except Exception:  # noqa: BLE001
            doc = {"items": []}
    items: list[dict[str, Any]] = list(doc.get("items") or [])
    for row in items:
        if str(row.get("name") or "").strip() == name and str(row.get("theme_pack") or pack) == pack:
            return {
                "id": str(row.get("id") or _slug_industry(name)),
                "name": name,
                "hint": str(row.get("hint") or hint or name),
                "custom": "true",
            }
    entry = {
        "id": _slug_industry(name),
        "name": name,
        "hint": (hint or name).strip(),
        "theme_pack": pack,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    items.append(entry)
    doc["items"] = items[-200:]
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "id": entry["id"],
        "name": entry["name"],
        "hint": entry["hint"],
        "custom": "true",
    }


def _pack_meta(theme_pack: str) -> dict[str, Any]:
    """读取 Theme Pack 元数据。"""

    for pack in list_theme_packs():
        if pack.get("id") == theme_pack:
            return pack
    return {"id": theme_pack, "default_topic": "", "name": theme_pack}


def list_cn_vc_market_industries() -> list[dict[str, str]]:
    """
    返回创投标准「所属行业」列表（看热点默认目录）。

    Returns:
        [{id, name, hint}, ...]，不含「不限」（由前端空值表示）。
    """

    return [dict(x) for x in CN_VC_MARKET_INDUSTRIES]


def list_industries(theme_pack: str = "local-service-leadgen") -> list[dict[str, str]]:
    """
    列出 Theme Pack 下建议行业 + 用户自定义行业。

    Args:
        theme_pack: 主题包 ID。

    Returns:
        行业列表，每项含 id/name/hint。
    """

    base = list(INDUSTRIES_BY_PACK.get(theme_pack) or INDUSTRIES_BY_PACK["_default"])
    custom = load_custom_industries(theme_pack)
    seen = {str(r.get("name") or "").strip() for r in base}
    for row in custom:
        name = str(row.get("name") or "").strip()
        if name and name not in seen:
            base.append(row)
            seen.add(name)
    return base


def list_all_industries(theme_pack: str = "") -> list[dict[str, str]]:
    """
    汇总全行业选项（各 Theme Pack 建议项 + 全部自定义），供下拉选择。

    Args:
        theme_pack: 若指定，该包行业排在前面。

    Returns:
        去重后的行业列表（含 id/name/hint）。
    """

    pack_ids: list[str] = []
    preferred = (theme_pack or "").strip()
    if preferred:
        pack_ids.append(preferred)
    for p in INDUSTRIES_BY_PACK:
        if p != "_default" and p not in pack_ids:
            pack_ids.append(p)

    out: list[dict[str, str]] = []
    seen_name: set[str] = set()

    def _push(rows: list[dict[str, str]]) -> None:
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name or name in seen_name:
                continue
            seen_name.add(name)
            out.append(
                {
                    "id": str(row.get("id") or name),
                    "name": name,
                    "hint": str(row.get("hint") or name),
                }
            )

    for pack in pack_ids:
        _push(list_industries(pack))
    _push(list(INDUSTRIES_BY_PACK["_default"]))
    _push(load_custom_industries(""))
    return out


def resolve_industry(
    theme_pack: str,
    industry: str = "",
    *,
    rotate: bool = False,
    previous_industry: str = "",
) -> dict[str, str]:
    """
    解析当前行业；rotate=True 时换到不同行业。

    Args:
        theme_pack: 主题包。
        industry: 用户指定行业 id 或中文名。
        rotate: 是否强制换一个不同行业。
        previous_industry: 上一轮行业 id，用于避开重复。
    """

    industries = list_industries(theme_pack)
    by_id = {item["id"]: item for item in industries}
    by_name = {item["name"]: item for item in industries}

    selected: Optional[dict[str, str]] = None
    key = (industry or "").strip()
    if key in by_id:
        selected = by_id[key]
    elif key in by_name:
        selected = by_name[key]
    elif key:
        # 自由输入行业名（不限制下拉）
        selected = {
            "id": _slug_industry(key),
            "name": key,
            "hint": key,
            "custom": "true",
        }
        try:
            save_custom_industry(theme_pack, key)
        except Exception:  # noqa: BLE001
            pass

    if rotate:
        candidates = [item for item in industries if item["id"] != (previous_industry or (selected or {}).get("id"))]
        if not candidates:
            candidates = industries
        selected = random.choice(candidates)
    elif not selected:
        selected = industries[0]

    return selected


def _clean_items(raw_items: Any, *, limit: int, exclude: set[str]) -> list[dict[str, Any]]:
    """清洗模型返回的选题列表，并过滤已出现过的主题；按 A/B/C 等权综合分排序。"""

    from app.tools.opportunity_score import score_opportunity_abc

    if not isinstance(raw_items, list):
        return []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_items[: limit * 3]:
        if not isinstance(row, dict):
            continue
        topic = str(row.get("topic") or "").strip()
        if not topic:
            continue
        norm = topic.lower()
        if norm in seen or norm in exclude:
            continue
        seen.add(norm)
        scores = score_opportunity_abc(row)
        try:
            raw_score = float(row.get("score") or 0)
        except (TypeError, ValueError):
            raw_score = 0.0
        score = scores["composite"] if scores["composite"] > 0 else (raw_score or 0.7)
        industry_tag = str(row.get("industry") or "").strip()
        cleaned.append(
            {
                "topic": topic,
                "reason": str(row.get("reason") or "模型推荐"),
                "score": round(score, 4),
                "industry": industry_tag,
                "pain_core": str(row.get("pain_core") or "").strip(),
                "audience_segment": str(row.get("audience_segment") or "").strip(),
                "industry_niche": str(row.get("industry_niche") or "").strip(),
                "validate_ease": scores["a_validate"],
                "willingness_to_pay": scores["b_pay"],
                "competition_gap": scores["c_gap"],
                "scores": {
                    "A_validate_ease": scores["a_validate"],
                    "B_willingness_to_pay": scores["b_pay"],
                    "C_competition_gap": scores["c_gap"],
                    "composite": round(score, 4),
                },
            }
        )
        if len(cleaned) >= limit:
            break
    cleaned.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return cleaned


async def recommend_topics(
    *,
    theme_pack: str = "local-service-leadgen",
    hint: str = "",
    industry: str = "",
    exclude_topics: Optional[list[str]] = None,
    rotate_industry: bool = False,
    previous_industry: str = "",
    use_llm: bool = True,
    limit: int = 5,
) -> dict[str, Any]:
    """
    按行业实时推荐商业主题（强制真实模型）。

    Args:
        theme_pack: 主题包。
        hint: 用户模糊方向（可叠加）。
        industry: 行业 id 或名称；空则用默认或 rotate。
        exclude_topics: 换批时排除的旧主题，避免总刷出同一批。
        rotate_industry: 刷新时换一个不同行业。
        previous_industry: 上一行业 id。
        use_llm: 必须为 True。
        limit: 条数。
    """

    meta = _pack_meta(theme_pack)
    if not use_llm:
        raise RuntimeError("主题推荐已禁用 mock/离线列表，请保持 use_llm=true 并配置可用模型。")

    industry_meta = resolve_industry(
        theme_pack,
        industry,
        rotate=rotate_industry,
        previous_industry=previous_industry,
    )
    exclude = {str(t).strip().lower() for t in (exclude_topics or []) if str(t).strip()}
    nonce = uuid.uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    user_hint = (hint or "").strip()
    combined_hint = " / ".join(
        part for part in [industry_meta.get("hint") or "", user_hint] if part
    )

    llm = LLMClient()
    payload, model, used_mock = await llm.complete_json(
        route=ModelRoute.TIER_M,
        system=(
            "你是中国市场「可落地商机」选题顾问。"
            "必须严格围绕【指定行业】产出高潜力、可验证、可小步落地的商机主题。"
            "每条主题必须塞进更精确的细分："
            "pain_core=核心痛点一句话；audience_segment=具体付费人群；"
            "industry_niche=行业内更细赛道（比行业名再细一级）。"
            "选题衡量：真实痛点、谁付费、竞争可切入、验证成本低；"
            "不要把「获客/投放/落地页」当成主题本身——那是商机确认后的落地动作。"
            "可借鉴中国独立开发者已上线产品的差异化切口，但禁止编造其数据。"
            "禁止跨行业；禁止复述已排除主题；禁止占位符与套话。"
            "只输出 JSON："
            '{"items":[{"topic":"...","reason":"...","score":0.0,"industry":"行业名",'
            '"pain_core":"...","audience_segment":"...","industry_niche":"...",'
            '"validate_ease":0.0,"willingness_to_pay":0.0,"competition_gap":0.0,'
            '"success_likelihood":0.0,"feasibility":0.0}]}。'
            "topic 用中文短句，格式贴近「人群×痛点×方案」，彼此差异明显。"
            "score 必须等于 (validate_ease+willingness_to_pay+competition_gap)/3。"
        ),
        user=(
            f"nonce={nonce}; 今天(UTC)={now.date().isoformat()}; "
            f"theme_pack={theme_pack}; pack_name={meta.get('name')}; "
            f"industry_id={industry_meta['id']}; industry_name={industry_meta['name']}; "
            f"industry_focus={industry_meta.get('hint')}; "
            f"extra_hint={user_hint or '无'}; "
            f"必须排除的旧主题={list(exclude)[:12] or '无'}。"
            f"请在「{industry_meta['name']}」行业内给出 {limit} 个全新商机主题。"
            f"评分等权：A=1–2周可验证；B=付费意愿/客单清晰；C=竞争缺口。三项都要高才算高成功率。"
            f"细分要求：audience_segment 禁止写「大众/用户」；industry_niche 必须比行业名更细。"
            f"聚焦：{combined_hint or industry_meta['name']}。"
        ),
        mock_payload=None,
        allow_mock=False,
        temperature=0.95,
    )
    if used_mock:
        raise RuntimeError("模型返回了 mock 数据，已拒绝。请关闭 MOCK_LLM 并确认 API Key。")

    items = _clean_items(
        payload.get("items") if isinstance(payload, dict) else None,
        limit=limit,
        exclude=exclude,
    )
    if not items:
        raise RuntimeError(
            f"模型未返回有效选题 items（行业={industry_meta['name']}）。"
            f"raw_keys={list(payload.keys()) if isinstance(payload, dict) else type(payload)}"
        )

    for item in items:
        if not item.get("industry"):
            item["industry"] = industry_meta["name"]

    return {
        "theme_pack": theme_pack,
        "pack": meta,
        "hint": hint,
        "industry": industry_meta,
        "industries": list_industries(theme_pack),
        "recommended": items[0]["topic"],
        "items": items,
        "source": f"llm:{model}",
        "model": model,
        "nonce": nonce,
        "generated_at": now.isoformat(),
    }
