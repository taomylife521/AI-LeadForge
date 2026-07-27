# LeadForge — Agent / 工程行为规范

> 本文件对 Cursor Agent 与协作者生效。细则亦见 `.cursor/rules/github-vercel-docs-sync.mdc`。

## 1. 产品目标

为独立开发者 / 一人公司提供：**看热点 → 定商机 → 选项目 → 去落地** 的可验证闭环。  
垂直默认包：`theme-packs/local-service-leadgen`（广告法与 HITL 约束见该包 `AGENTS.md`）。

## 2. 文档与代码同步（硬约束）

任何**用户可见行为变更**（热点通道、AI 重构、部署方式、穿透、任务 CRUD、环境变量）必须在同一变更集内更新：

| 必更新 | 路径 |
|:---|:---|
| 入口说明 | `README.md` |
| 文档索引 | `docs/README.md` |
| 需求/验收 | `docs/01-requirements.md`（若验收项变） |
| 架构/拓扑 | `docs/03-architecture.md` |
| API/模块 | `docs/04-detailed-design.md` |
| 交互 | `docs/05-prototype.md` |
| 部署与双远程 | `docs/06-deploy-and-sync.md` |

禁止「只改代码不改文档」合入 `main`。

## 3. GitHub ↔ Vercel 保持一致（硬约束）

1. **单一事实源**：`main` 分支 = GitHub `origin/main` = Vercel Production 应对齐的代码。  
2. **发布顺序**（默认）：
   - 本地改完 → 更新文档 → `git commit` → `git push origin main`
   - 推送后确认 Vercel 已关联仓库并自动部署；若未自动部署则执行 `npx vercel --prod --yes`
3. **禁止长期漂移**：不得只在 Vercel CLI 上传未推送的本地改动而不 push GitHub。  
4. **密钥**：`.env` / Vercel Environment Variables 不同步进 Git；文档只写变量名。  
5. **国内访问**：`*.vercel.app` 在大陆常不可达；国内演示用本机 + cpolar（见 README / `docs/06-deploy-and-sync.md`），与 Vercel 海外演示并存。

## 4. AI 重构通道规则

实现：`apps/api/app/tools/ai_rebuild_clues.py`。

- **一痛点一条**：按能力原型 / 行业场景去重，禁止多仓库套同一句痛点。  
- 每条必须含：真实痛点、为何此项目、组合更优、成功概率、可点开 URL。  
- 过滤噪声仓（面试指南、汇编示例、纯书单等）。  
- 标题格式：`AI重构：{行业/场景} · {具体痛点}`，禁止「用「仓库」重做某某」机械套话。

## 5. Serverless / 本机差异

| 能力 | 本机 | Vercel |
|:---|:---|:---|
| 数据目录 | `data/` | `/tmp/leadforge-data`（无持久） |
| 定时同步 | 可启用 | `LEADFORGE_SKIP_BACKGROUND=1` 跳过 |
| Sidecar | 可选 | 不可用 |
| 入口 | `uvicorn app.main:app` | `api/index.py` → FastAPI `app` |

## 6. 提交与安全

- 不提交 `.env`、`data/`、真实 Authtoken、API Key。  
- 提交信息说明「为什么」；文档与代码同 commit 优先。  
- 推送 GitHub 若网络受限，可经本地代理，但不得把代理密钥写入仓库。
