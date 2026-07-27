# LeadForge

全自动商业闭环多智能体工作台：**看热点 → 定商机 → 选项目 → 去落地**。

默认 Theme Pack：`local-service-leadgen`（本地到店获客落地页）。主题可插拔，新增 `theme-packs/<id>` 即可。

> 文档中心：[docs/README.md](docs/README.md)  
> 含：需求、PRD、架构、详细设计、原型说明。

## 功能速览

| 阶段 | 能力 |
|:---|:---|
| 看热点 | 平台热搜、SeekMoney 商机线索、创投、行业痛点、AI 重构；定时同步 |
| 定商机 | 行业主题推荐、验证工作流、HITL、实时进度 |
| 选项目 | 可落地开源/创投过滤、两周落地方案 |
| 去落地 | 本地任务增删查改、子任务状态、从商机生成 |

方法对齐：[SeekMoney-ai](https://github.com/liangdabiao/SeekMoney-ai) 痛点框架 + 一人公司（OPC）两周验证约束。

## 一键启停

### 方式 A：Docker 全栈（含 LiteLLM / Qdrant 等）

| 操作 | 命令 |
|:---|:---|
| 启动 | 双击 `start.bat` |
| 停止 | 双击 `stop.bat` |
| 重建 | `powershell -File scripts\start.ps1 -Build` |

需已安装并启动 **Docker Desktop**。

### 方式 B：本地控制台（无需 Docker）

| 操作 | 命令 |
|:---|:---|
| 启动 | 双击 `start-local.bat` |
| 停止 | `powershell -File scripts\stop-local.ps1` |

无云厂商 API Key 时可将 `MOCK_LLM=true` 做演示；正式推荐请保持 `MOCK_LLM=false`。

启动后打开：**http://127.0.0.1:8080**

## 快速配置

```powershell
copy .env.example .env
# 编辑 .env：至少配置 AGNES_API_KEY 或其它厂商 Key
# 可选：GITHUB_TOKEN（提高 GitHub Search 限额）
```

控制台「系统设置」可可视化粘贴密钥并切换模型档位。

## 大模型

- 默认档位：`MODEL_PROFILE=agnes-free`（Agnes API Hub）
- 亦可切换 OpenAI / 通义 / DeepSeek / Anthropic / Ollama / 免费模型目录
- 自定义：填写 API Base + Key + model（OpenAI 兼容）

## 验证工作流（默认）

```
主题 → 商机 → 模式 → 红队 → HITL → 开发 → 部署 → 营销 → 红队 → HITL → 飞轮
```

点「开始验证」后，底部 **工作流** 抽屉会自动打开并显示节点进度；历史在 **运行记录**，待确认在 **审批中心**。

## 仓库结构

```
apps/api/          FastAPI + 控制台
config/            模型档位、工作流模板、skills
theme-packs/       垂直场景包
rules/             合规规则
schemas/           数据信封 Schema
docs/              需求/PRD/架构/详细设计/原型
integrations/      TrendRadar 等 sidecar 配置（GPL 组件不并入主逻辑）
scripts/           启停脚本
```

## 文档

| 文档 | 路径 |
|:---|:---|
| 需求 | [docs/01-requirements.md](docs/01-requirements.md) |
| PRD | [docs/02-prd.md](docs/02-prd.md) |
| 架构 | [docs/03-architecture.md](docs/03-architecture.md) |
| 详细设计 | [docs/04-detailed-design.md](docs/04-detailed-design.md) |
| 原型 | [docs/05-prototype.md](docs/05-prototype.md) |
| Sidecar | [docs/sidecars.md](docs/sidecars.md) |

## 本地开发（可选）

```powershell
cd apps\api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:LEADFORGE_DATA_DIR="..\..\data"
$env:MOCK_LLM="false"
uvicorn app.main:app --host 127.0.0.1 --port 8080
```

## 安全提示

- **勿提交** `.env`、`data/`、含 Token 的本地文件（已在 `.gitignore`）。
- GitHub Token 仅需 `public_repo` 量级权限即可提升 Search 限额。

## License

主项目以仓库内声明为准。TrendRadar 等第三方以各自许可证通过 **sidecar** 方式集成，详见 `docs/sidecars.md`。
