# LeadForge 详细设计文档

> 版本：1.1 · 更新日期：2026-07-27

## 1. 目录与代码映射

```
leadforge/
├── apps/api/                 # FastAPI 服务 + 静态控制台
│   ├── app/
│   │   ├── main.py           # 路由聚合
│   │   ├── agents/           # Agent 管线
│   │   ├── tools/            # 业务工具（热点/项目/任务）
│   │   ├── memory/           # 长期记忆
│   │   └── *.py              # 工作流/LLM/信封等
│   └── static/index.html     # 控制台 SPA
├── config/                   # 模型档位、工作流、skills、绑定
├── theme-packs/              # 垂直场景包
├── rules/                    # 合规规则
├── schemas/                  # JSON Schema
├── integrations/             # Sidecar 配置（非主逻辑）
├── docs/                     # 本设计文档集
├── scripts/                  # 启停脚本
└── docker-compose.yml
```

## 2. 数据设计

### 2.1 落地任务（`data/landing_tasks.json`）

```json
{
  "id": "uuid",
  "title": "落地 · 主题",
  "topic": "商机主题",
  "industry": "行业",
  "status": "todo|doing|done|blocked|cancelled",
  "priority": "high",
  "context": "备注",
  "plan_markdown": "...",
  "projects": [{"name":"","url":"","role":""}],
  "children": [
    {"id":"uuid","title":"确认付费方与核心动作","status":"todo"}
  ],
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "source": "local"
}
```

**规则**：子任务全部 `done` 时父任务自动 `done`；存在 `doing`/`blocked` 时上浮父状态。

### 2.2 热点条目（统一结构）

| 字段 | 类型 | 说明 |
|:---|:---|:---|
| title | string | 展示标题 |
| url | string | 来源或伪协议 `leadforge://` |
| snippet | string | 摘要 |
| provider | string | 来源前缀（newsnow/seekmoney/cyzone…） |
| heat | number | 排序热度 |
| meta | object | lane/industry/评分等扩展 |
| lane | string | platforms/seekmoney/vc/pain/ai_rebuild |

### 2.3 SeekMoney 元数据（商机线索）

`surface_pain` / `root_cause` / `user_scenario` / `who_pays` / `mvp_2w` / `demand_score` / `market_score` / `competition_score` / `priority_score` / `data_quality`

### 2.4 运行 Trace

- 图：`nodes[id].status|inputs|outputs|x|y`
- 事件：`step` / `run_paused` / `run_finished`
- 控制：pause / resume / stop / restart

## 3. API 设计（摘要）

### 3.1 研究与热点

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| GET | `/api/research/hotspots/lanes` | 多维热点通道 |
| POST | `/api/research/hotspot-opportunities` | AI 提炼商机 |
| GET/POST | `/api/research/sync/*` | 同步状态/日志/配置/立即跑 |
| GET | `/api/themes/recommend` | 主题推荐 |
| GET | `/api/themes/industries` | 行业目录 |

### 3.2 运行与工作流

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| POST | `/api/runs/async` | 启动验证 |
| GET | `/api/runs/{id}/stream` | SSE |
| GET | `/api/runs/{id}/graph` | 图状态 |
| POST | `/api/runs/{id}/pause\|resume\|stop\|restart` | 控制 |
| GET | `/api/traces` | 运行记录 |

### 3.3 落地任务 CRUD

| 方法 | 路径 | 说明 |
|:---|:---|:---|
| GET | `/api/landing-tasks?q=&status=` | 列表/搜索 |
| GET | `/api/landing-tasks/{id}` | 详情 |
| POST | `/api/landing-tasks` | 创建 |
| PATCH | `/api/landing-tasks/{id}` | 更新 |
| DELETE | `/api/landing-tasks/{id}` | 删除 |
| POST | `/api/landing-tasks/{id}/children` | 增子项 |
| PATCH | `/api/landing-tasks/{id}/children/{cid}` | 改子项 |
| DELETE | `/api/landing-tasks/{id}/children/{cid}` | 删子项 |
| POST | `/api/paperclip/handoff` | 从商机生成（本地必写，远程可选） |

## 4. 关键算法/策略

### 4.1 商机线索（`seekmoney_clues.py`）

1. 过滤非商业噪声（明星/体育/纯吃瓜等正则）。
2. 以创投行业模板为主生成「可验证付费痛点」。
3. 热搜仅作弱证据挂 `source_titles`；含商业信号才可进信号驱动条目。
4. 按 `priority_score` 排序截断。

### 4.2 行业过滤

- 未选行业：全网。
- 已选行业：标题/摘要关键词匹配（不盲信污染的 industry 字段）。

### 4.3 可落地项目

- `landable_filter` + 库内评分；输出角色（主项目/工具/对标）。
- `landing_plan` 生成两周 Markdown（OPC 约束）。

## 5. 前端交互要点（控制台）

| 场景 | 行为 |
|:---|:---|
| 开始验证 | 校验主题 → POST async → `openAux('canvas')` → 进度卡刷新 |
| 定商机建议 | `.rec-item` 高对比文字；选中高亮 |
| 去落地 | 搜索/筛选；详情可编辑；子任务状态下拉 |

静态资源由 FastAPI `/` 与 `/static` 提供，无独立前端构建。

## 6. 配置项

| 配置 | 位置 | 用途 |
|:---|:---|:---|
| `.env` | 根目录 | 密钥与端口 |
| `MODEL_PROFILE` | env / UI | 模型档位 |
| `LEADFORGE_DATA_DIR` | env | 数据目录 |
| `workflow_templates` | config/ | 验证图 |
| `pack.yaml` | theme-packs/ | 垂直 KPI 与默认主题 |

## 7. 错误处理约定

- 外部采集失败：返回明确 HTTP 502/业务提示，不静默空列表覆盖（热点有合并策略）。
- 正式推荐：`source` 为 mock/fallback 时前端拒绝展示为真实结果。
- CRUD：`KeyError` → 404；`ValueError` → 400。

## 8. 测试建议

| 层级 | 内容 |
|:---|:---|
| 单元 | `seekmoney_clues` 噪声过滤；`landing_tasks` CRUD |
| 接口 | `/api/landing-tasks` 冒烟；`/api/research/hotspots/lanes` |
| 手工 | 四步流水线走通 + HITL 批准 |

## 9. 演进 backlog

1. TikHub UGC + Embedding 聚类对齐 SeekMoney 原版数据面。
2. 任务看板拖拽与截止日期。
3. 多 Theme Pack 市场样例（跨境/SaaS）。
