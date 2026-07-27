# -*- coding: utf-8 -*-
"""
Vercel Serverless 入口。

作用: 导出 FastAPI `app` 供 Vercel Python Runtime / Fluid Compute 加载。
说明: 云端使用 /tmp 作为可写数据目录；不启动本机定时同步线程。
作者: LeadForge
创建时间: 2026-07-27
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 仓库根目录（api/ 的上一级）
_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _ROOT / "apps" / "api"

# 保证 `import app.*` 与配置路径可用
if str(_API_ROOT) not in sys.path:
    sys.path.insert(0, str(_API_ROOT))
os.chdir(_ROOT)

# Vercel 环境标记与可写目录（本地不受影响：仅当已在 Vercel 时设置）
if os.getenv("VERCEL") or os.getenv("VERCEL_ENV"):
    os.environ.setdefault("LEADFORGE_DATA_DIR", "/tmp/leadforge-data")
    os.environ.setdefault("LEADFORGE_SKIP_BACKGROUND", "1")
    # 无密钥时允许演示；有密钥则在 Vercel 环境变量中配置
    # MOCK_LLM 不强制覆盖用户设置

from app.main import app  # noqa: E402

__all__ = ["app"]
