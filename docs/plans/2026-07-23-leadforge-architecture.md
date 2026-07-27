# LeadForge 架构与落地指南（历史纪要 · 2026-07-23）

> 本文为早期落地纪要，**现行完整架构请以** [../03-architecture.md](../03-architecture.md) **为准**。

## 定位
垂直场景 AI 获客落地页工厂；默认 Pack `local-service-leadgen`；主题可插拔。

## Agent 串联
Opportunity → BusinessModel → RedTeam#1 → HITL#1 → Dev → Deploy → Marketing → RedTeam#2 → HITL#2 → Flywheel

## 技术底座
n8n / Traefik(可选) / 自研编排 / LiteLLM / Qdrant / FastAPI / skills.sh 白名单。

## 数据契约
`schemas/envelope.v1.json`；运行时 Pydantic `DataEnvelope`。

## 模型路由
`config/model_profiles/*.json` + `POST /api/models/profiles/switch`

## 一键启停
`start.bat` / `stop.bat` → Docker Compose

## 可视化
`apps/api/static/index.html` 由 FastAPI `/` 提供
