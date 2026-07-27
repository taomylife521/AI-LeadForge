# TrendRadar / Paperclip Sidecar

TrendRadar（GPL-3.0）与 Paperclip（MIT）以**独立容器**运行，不并入 LeadForge 源码。

## 启动

```bash
docker compose up -d trendradar trendradar-mcp paperclip
# 或与全栈一起
docker compose up -d
```

- TrendRadar MCP: `http://127.0.0.1:3333/mcp`
- Paperclip UI: `http://127.0.0.1:3100`（首次打开完成公司/项目/Agent 创建）
- 配置目录: `integrations/trendradar/config/`（官方默认 config，可按需改关键词）

## LeadForge 环境变量

见 `.env.example` 中 `TRENDRADAR_*` / `PAPERCLIP_*`。

在 Paperclip 建好公司后，把 `PAPERCLIP_COMPANY_ID`（及可选 project/goal/agent）写入 `.env`，决策台即可「交接至 Paperclip」。

## 同页嵌入

LeadForge UI 通过同域代理嵌入（无需新开标签）：

- TrendRadar: `/embed/trendradar/` → `TRENDRADAR_WEB_URL`（默认 `http://127.0.0.1:8081`）
- Paperclip: `/embed/paperclip/` → `PAPERCLIP_BASE_URL`（默认 `http://127.0.0.1:3100`）

代理会去掉 `X-Frame-Options`，允许 iframe。若上游未启动，流水线仍可用 API 热点列表与 Paperclip 任务列表。
