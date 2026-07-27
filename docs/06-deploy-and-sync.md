# 部署与双远程同步

> 版本：1.0 · 更新日期：2026-07-27  
> 约束来源：根目录 [`AGENTS.md`](../AGENTS.md)

## 1. 三种对外方式

| 方式 | URL / 入口 | 适用 |
|:---|:---|:---|
| 本机 | http://127.0.0.1:8080 · `start-local.bat` | 开发 |
| 本机穿透 | cpolar 公网 https · `start-public.bat` / `start-tunnel.bat` | **国内访客免翻墙试用** |
| Vercel | https://ai-leadforge.vercel.app · `api/index.py` | 海外 / 可访问 vercel.app 的用户 |

> 大陆直连 `*.vercel.app` 常失败，属网络环境限制，非应用配置错误。

## 2. Vercel 结构

| 文件 | 作用 |
|:---|:---|
| `api/index.py` | Serverless 入口；设置 `/tmp` 数据目录与 `LEADFORGE_SKIP_BACKGROUND=1` |
| `vercel.json` | `maxDuration`、安装命令、排除大目录 |
| `requirements.txt` / `pyproject.toml` | Python 依赖与 `[tool.vercel] entrypoint` |
| `.vercelignore` | 减小上传体积 |

环境变量（Vercel Dashboard，勿进 Git）：`AGNES_API_KEY`、`AGNES_API_BASE`、`MODEL_PROFILE`、`GITHUB_TOKEN`（可选）、`LEADFORGE_DATA_DIR=/tmp/leadforge-data`、`LEADFORGE_SKIP_BACKGROUND=1`。

限制：无持久盘；无 TrendRadar/Paperclip/Redis/Qdrant；单请求约 60s；同步日志可能跨实例丢失（已有内存合并与前端兜底）。

## 3. 本机穿透（cpolar）

1. 安装 https://www.cpolar.com/download  
2. `cpolar authtoken <token>`（勿把字面量 `authtoken` 拼进 token 值）  
3. `start-public.bat` 或 `start-local.bat` + `start-tunnel.bat`  
4. 分享终端中的 `https://xxxx.cpolar.*`；电脑需保持开机  

脚本：`scripts/start-tunnel.ps1`、`start-tunnel.bat`、`start-public.bat`。

## 4. GitHub ↔ Vercel 对齐流程

```text
改代码 + 改文档
    → git commit
    → git push origin main          # 仓库：taomylife521/AI-LeadForge
    → Vercel 自动 Production 部署   # 项目已关联 GitHub 时
    → 若未自动：npx vercel --prod --yes
    → 打开 https://ai-leadforge.vercel.app/api/health 核对
```

**禁止**仅 CLI 部署未推送的提交，导致 GitHub 与 Vercel 长期不一致。

## 5. 健康检查

- 本机：`GET /api/health`  
- Vercel：同上；响应可含 `ephemeral_storage: true`  
- 穿透：对公网 URL 请求 `/api/health`

## 6. 本机代理排障

若终端继承了 `ALL_PROXY` / `HTTPS_PROXY`（如本机 SOCKS），而 venv 未装 `socksio`，热点同步 / 主题推荐 / 项目抓取可能报：

`Using SOCKS proxy, but the 'socksio' package is not installed`

处理任选其一：

```powershell
apps\api\.venv\Scripts\python.exe -m pip install "httpx[socks]" socksio
# 或启动前清空代理后再起 uvicorn
$env:ALL_PROXY=''; $env:HTTPS_PROXY=''; $env:HTTP_PROXY=''
```

README 配图可用：`scripts/capture-readme-screens.py`（需 8080 已启动且库内有数据）。
