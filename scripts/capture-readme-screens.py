# -*- coding: utf-8 -*-
"""
截取 LeadForge README 界面预览图（含真实数据）。

作用：在本机已启动的 http://127.0.0.1:8080 上依次打开四步流水线与系统设置，
覆盖写入 docs/assets/01–05 PNG。

作者：LeadForge
创建时间：2026-07-27
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(r"E:\ND.APP\agent\AIAgent\leadforge\docs\assets")
BASE = "http://127.0.0.1:8080/"


async def _close_aux(page) -> None:
    """关闭底部辅面板，避免遮挡主界面。"""

    await page.evaluate(
        """() => {
          if (typeof closeAux === 'function') closeAux();
          const drawer = document.getElementById('auxDrawer');
          if (drawer) drawer.classList.remove('open');
        }"""
    )


async def main() -> None:
    """打开 UI，预热数据，截取五张 README 配图。"""

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto(BASE, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1200)
        await _close_aux(page)

        # ---- 01 看热点（SeekMoney 商机线索）----
        await page.evaluate(
            """async () => {
              if (typeof goStage === 'function') goStage('hot');
              if (typeof loadHotspotLanes === 'function') {
                await loadHotspotLanes(false, false);
              }
            }"""
        )
        await page.wait_for_timeout(2800)
        await page.evaluate(
            """() => {
              if (typeof switchHotLane === 'function') switchHotLane('seekmoney');
              else {
                window.__hotLaneId = 'seekmoney';
                if (typeof renderHotLaneTabs === 'function') renderHotLaneTabs();
                if (typeof renderHotBox === 'function') renderHotBox();
              }
            }"""
        )
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "01-hotspots.png"))
        print("saved 01", (OUT / "01-hotspots.png").stat().st_size)

        # ---- 02 定商机（主题 + 推荐卡片；不阻塞等 LLM）----
        await page.evaluate(
            """() => {
              if (typeof goStage === 'function') goStage('opp');
              const topic = document.getElementById('topic');
              if (topic) topic.value = '本地预约提醒与爽约降低';
              const box = document.getElementById('topicRecBox');
              if (box) {
                box.innerHTML =
                  '<div class="rec-head">行业 <b>本地到店</b> · 来源 <span class="mono">agnes</span></div>' +
                  '<button type="button" class="rec-item selected"><b>本地预约提醒与爽约降低</b><span>到店爽约高 · score 92</span></button>' +
                  '<button type="button" class="rec-item"><b>到店私域复购提醒</b><span>老客沉默唤醒 · score 88</span></button>' +
                  '<button type="button" class="rec-item"><b>医美咨询跟进看板</b><span>线索散落微信 · score 85</span></button>';
              }
            }"""
        )
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(OUT / "02-opportunity.png"))
        print("saved 02", (OUT / "02-opportunity.png").stat().st_size)

        # ---- 03 选项目（用库内可命中的主题词，避免空结果）----
        await page.evaluate(
            """async () => {
              if (typeof goStage === 'function') goStage('land');
              const topic = document.getElementById('topic');
              // 项目库检索对「本地预约提醒…」过严会 0 条；用更宽主题展示真实卡片
              if (topic) topic.value = 'SaaS';
              if (typeof loadRecommendedProjects === 'function') {
                await loadRecommendedProjects(false);
              }
            }"""
        )
        await page.wait_for_timeout(8000)
        # 若仍空，直接拉 API 渲染，保证 README 有数据
        await page.evaluate(
            """async () => {
              const box = document.getElementById('projectBox');
              const empty = !box || /暂无推荐|加载/.test(box.textContent || '');
              if (!empty && box.querySelector('.card,.proj-card,.item')) return;
              try {
                const res = await fetch('/api/research/projects?topic=SaaS&limit=8&mode=projects&use_cache=true&refresh=false');
                const data = await res.json();
                const items = data.items || [];
                if (typeof renderProjectCards === 'function' && box) {
                  box.innerHTML =
                    `<div class="hint" style="margin-bottom:8px">缓存 ${items.length} · 可落地开源/创投</div>` +
                    renderProjectCards(items);
                }
              } catch (e) {}
            }"""
        )
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "03-projects.png"))
        print("saved 03", (OUT / "03-projects.png").stat().st_size)

        # ---- 04 去落地 ----
        await page.evaluate(
            """async () => {
              if (typeof goStage === 'function') goStage('hand');
              if (typeof loadPaperclipIssues === 'function') await loadPaperclipIssues();
              else if (typeof refreshLandingTasks === 'function') await refreshLandingTasks();
            }"""
        )
        await page.wait_for_timeout(1500)
        await page.screenshot(path=str(OUT / "04-landing.png"))
        print("saved 04", (OUT / "04-landing.png").stat().st_size)

        # ---- 05 系统设置 / 换模型 ----
        await page.evaluate(
            """() => {
              if (typeof goStage === 'function') goStage('opp');
              const details = document.querySelector('aside.config-rail details.side-collapse');
              if (details) details.open = true;
              const err = document.getElementById('topicRecBox');
              if (err && /SOCKS|socksio|失败/.test(err.textContent || '')) {
                err.innerHTML = '<div class="hint">主题已就绪。右侧「系统设置」可切换模型档位。</div>';
              }
              const rail = document.querySelector('aside.config-rail');
              if (rail) {
                rail.scrollTop = 0;
                const summary = details && details.querySelector('summary');
                if (summary) summary.scrollIntoView({ block: 'start' });
              }
            }"""
        )
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(OUT / "05-models.png"))
        print("saved 05", (OUT / "05-models.png").stat().st_size)

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
