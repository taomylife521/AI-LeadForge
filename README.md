# LeadForge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![GitHub](https://img.shields.io/badge/GitHub-taomylife521%2FAI--LeadForge-blue)](https://github.com/taomylife521/AI-LeadForge)
[![Vercel](https://img.shields.io/badge/Vercel-ai--leadforge-black)](https://ai-leadforge.vercel.app)

**发现商机 · 匹配项目 · 落地执行**

LeadForge 是面向独立开发者与一人公司（OPC）的开源商业智能体工作台：从全网热点与创投信号中提炼**可验证商机**，匹配可落地开源/创投项目，并用工作流 + HITL 门禁推进到任务拆解。

> 作者 / 版权：**taomylife521 (Soul Coders)** · MIT 开源（需保留署名与许可证声明）  
> 仓库：https://github.com/taomylife521/AI-LeadForge  
> 线上（海外可访）：https://ai-leadforge.vercel.app  
> 工程规范：[`AGENTS.md`](./AGENTS.md) · 部署同步：[`docs/06-deploy-and-sync.md`](./docs/06-deploy-and-sync.md)

---

## 界面预览

### 01 看热点

多维通道：平台热搜 · SeekMoney 商机线索 · 创投 · 行业痛点 · AI 重构（一痛点一条，含为何此项目 / 组合更优 / 成功概率，可点开原文）。

![看热点](docs/assets/01-hotspots.png)

### 02 定商机

确认主题与行业，启动验证工作流；底部「工作流」实时看节点进度。

![定商机](docs/assets/02-opportunity.png)

### 03 选项目

只推荐经分析后可落地的开源 / 创投项目，并生成两周落地方案。

![选项目](docs/assets/03-projects.png)

### 04 去落地

本地落地任务增删查改，子任务状态跟踪。

![去落地](docs/assets/04-landing.png)

---

## 项目介绍

### 要解决什么问题

| 痛点 | LeadForge 做法 |
|:---|:---|
| 热搜像吃瓜，不像商机 | 对齐 SeekMoney：表面痛点 → 根因 → 付费方 → 两周 MVP |
| AI 重构像套模板 | 按能力原型去重：真实痛点 + 项目理由 + 组合建议 + 成功概率 |
| 验证链路散落各处 | 一条工作流：商机 → 模式 → 红队 → HITL → 开发 → 营销 → 飞轮 |
| 项目选型凭感觉 | 可落地过滤 + 落地方案 Markdown |
| 方案落地后无人跟 | 本地任务中心 CRUD |
| 国内打不开 Vercel | 本机 + cpolar 穿透；Vercel 作海外演示 |

### 核心能力

1. **看热点**：newsnow / 36氪 / 创业邦 / GitHub / 独立开发者线索；定时或手动增量同步与日志  
2. **定商机**：主题推荐、SeekMoney 结构线索、验证工作流与审批  
3. **选项目**：开源与创投可落地匹配、OPC 两周方案  
4. **去落地**：任务 / 子任务增删查改  
5. **AI 重构**：`ai_rebuild_clues.py` — 禁止多仓库共用一句痛点；卡片可点开 GitHub  

默认垂直场景包：`local-service-leadgen`（本地到店获客）。新增 `theme-packs/<id>` 即可扩展。

### 技术栈（摘要）

- **控制台**：FastAPI 静态 SPA（`apps/api/static/index.html`）  
- **编排**：自研工作流图 + SSE；Agent 管线（商机 / 模式 / 红队 / 开发 / 营销 / 飞轮）  
- **模型**：多档位切换（Agnes / OpenAI / 通义 / DeepSeek 等）  
- **云部署**：Vercel Python / FastAPI（`api/index.py`）  
- **可选 sidecar**：TrendRadar（GPL，进程隔离）、Paperclip（MIT）  

详细设计见 [`docs/`](docs/README.md)。

---

## 快速开始

### 本地（无需 Docker）

```powershell
copy .env.example .env
# 编辑 .env：填写 AGNES_API_KEY 等；勿把真实密钥提交到 Git
双击 start-local.bat
```

打开 http://127.0.0.1:8080

### Docker 全栈

```powershell
copy .env.example .env
双击 start.bat
```

需已安装 Docker Desktop。

### 本机穿透（国内别人免翻墙试用）

电脑保持开机，把本机 `8080` 映射成公网 https：

1. 注册并安装 [cpolar](https://www.cpolar.com/download)（免费档即可）  
2. 登录后台复制 Authtoken，执行：`cpolar authtoken <token>`（不要把文字 `authtoken` 拼进 token）  
3. 双击 `start-public.bat`（或先 `start-local.bat` 再 `start-tunnel.bat`）  
4. 把终端里的 `https://xxxx.cpolar.*` 发给对方  

注意：免费地址可能每次变化；穿透窗口不要关。勿把含密钥的环境暴露给不可信访客。

### Vercel

生产地址：**https://ai-leadforge.vercel.app**（大陆网络可能需代理才能打开）

```powershell
npx vercel login
npx vercel --prod --yes
```

或：推送 `main` 到 GitHub 后由 Vercel Git 集成自动部署。

说明：Serverless 数据在 `/tmp`（无持久盘），不启动本机定时同步；TrendRadar / Paperclip / Redis / Qdrant 等 sidecar 不可用。密钥在 Vercel Environment Variables 配置（如 `AGNES_API_KEY`）。完整说明见 [`docs/06-deploy-and-sync.md`](./docs/06-deploy-and-sync.md)。

### 保持 GitHub 与 Vercel 一致

工程硬约束（见 [`AGENTS.md`](./AGENTS.md)）：

1. 功能改动同步更新 README / `docs/`  
2. `git push origin main` 后再确认 Vercel Production  
3. 禁止只部署 Vercel 不推 GitHub（或反之长期漂移）  

---

## 文档

| 文档 | 说明 |
|:---|:---|
| [工程规范 AGENTS](./AGENTS.md) | Agent/协作硬约束（文档与双远程同步） |
| [需求文档](docs/01-requirements.md) | 功能 / 非功能需求 |
| [PRD](docs/02-prd.md) | 产品定位与用户故事 |
| [架构设计](docs/03-architecture.md) | 分层与部署拓扑 |
| [详细设计](docs/04-detailed-design.md) | API / 数据模型 / AI 重构策略 |
| [原型说明](docs/05-prototype.md) | 四步流水线交互 |
| [部署与双远程同步](docs/06-deploy-and-sync.md) | 本机 / cpolar / Vercel / GitHub |
| [Sidecar](docs/sidecars.md) | TrendRadar / Paperclip |

---

## 参考与致谢

LeadForge **不 fork 整仓**，而是吸收下列开源项目的方法论与能力边界，并在代码/文档中署名引用：

| 项目 | 链接 | 在本项目中的角色 |
|:---|:---|:---|
| **SeekMoney-ai** | [liangdabiao/SeekMoney-ai](https://github.com/liangdabiao/SeekMoney-ai) | 痛点发现框架；商机线索通道对齐 |
| **一人企业方法论** | [easychen/opc-methodology](https://github.com/easychen/opc-methodology) | OPC：两周可验证、不硬碰红海 |
| **TrendRadar** | [sansan0/TrendRadar](https://github.com/sansan0/TrendRadar) | 可选热搜增强；**GPL-3.0** sidecar |
| **Paperclip** | 控制面 sidecar（见 `docs/sidecars.md`） | 可选外部任务；本地任务为默认 |
| **chinese-independent-developer** | [1c7/chinese-independent-developer](https://github.com/1c7/chinese-independent-developer) | 中国独立开发者线索 |
| **free-llm-api-resources** | [cheahjs/free-llm-api-resources](https://github.com/cheahjs/free-llm-api-resources) | 免费模型目录参考 |
| newsnow / 36氪 PitchHub / 创业邦 | 公开热点与创投源 | 数据采集（遵守各站条款） |

方法论与实现：`methodology_playbooks.py`、`seekmoney_clues.py`、`ai_rebuild_clues.py`。

若你基于 LeadForge 二次开发，请：

1. 保留本仓库 **MIT** 版权与许可证声明（见 [`LICENSE`](./LICENSE)）  
2. 在 README / About 中署名：**LeadForge · taomylife521**  
3. 继续尊重上游参考项目的各自许可证（尤其是 GPL sidecar）

---

## 开源协议

本项目采用 [MIT License](./LICENSE)。

```
Copyright (c) 2026 taomylife521 (Soul Coders)
```

使用、修改、分发时须保留上述版权声明与许可全文；建议在衍生作品中注明：  
https://github.com/taomylife521/AI-LeadForge

---

## 安全提示

- 真实 API Key / cpolar token 只放本地 `.env` 或 Vercel 环境变量（已 `.gitignore`）  
- `.env.example` 仅含空占位  

## 贡献

Issue / PR 欢迎。提交前请确认：未含密钥与 `data/`；README/相关 docs 已同步；准备推送后与 Vercel 生产对齐。
