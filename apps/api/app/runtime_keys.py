# -*- coding: utf-8 -*-
"""
运行时 API Key 维护。

作用: 在控制台粘贴 Key 后写入进程环境变量，并持久化到 data/runtime_keys.json（可选同步 .env）。
作者: LeadForge
创建时间: 2026-07-25
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import data_dir, repo_root


def _keys_path() -> Path:
    return data_dir() / "runtime_keys.json"


def load_runtime_keys() -> dict[str, str]:
    """读取已保存的环境变量名→Key 映射。"""

    path = _keys_path()
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    keys = doc.get("keys") if isinstance(doc, dict) else {}
    if not isinstance(keys, dict):
        return {}
    return {str(k): str(v) for k, v in keys.items() if k and v}


def apply_runtime_keys_to_environ() -> int:
    """把持久化 Key 注入当前进程环境（不覆盖已有非空值）。"""

    applied = 0
    for name, value in load_runtime_keys().items():
        if not os.getenv(name):
            os.environ[name] = value
            applied += 1
    return applied


def save_runtime_key(env_name: str, api_key: str, *, write_dotenv: bool = False) -> dict[str, Any]:
    """
    保存单个 API Key。

    Args:
        env_name: 环境变量名，如 OPENROUTER_API_KEY。
        api_key: 明文 Key。
        write_dotenv: 是否同步写入仓库根目录 .env。

    Returns:
        状态摘要（不含明文 Key）。
    """

    env_name = (env_name or "").strip().upper()
    api_key = (api_key or "").strip()
    if not re.match(r"^[A-Z][A-Z0-9_]*$", env_name):
        raise ValueError("环境变量名不合法")
    if len(api_key) < 8:
        raise ValueError("API Key 过短")

    keys = load_runtime_keys()
    keys[env_name] = api_key
    path = _keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"keys": keys, "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.environ[env_name] = api_key

    dotenv_written = False
    if write_dotenv:
        dotenv_written = _upsert_dotenv(env_name, api_key)

    masked = (api_key[:4] + "…" + api_key[-4:]) if len(api_key) > 8 else "****"
    return {
        "ok": True,
        "env_name": env_name,
        "masked": masked,
        "dotenv_written": dotenv_written,
        "note": "已注入当前进程；重启后仍会从 data/runtime_keys.json 自动加载。",
    }


def _upsert_dotenv(env_name: str, api_key: str) -> bool:
    """在仓库 .env 中写入或替换变量行。"""

    path = repo_root() / ".env"
    line = f"{env_name}={api_key}"
    if not path.exists():
        path.write_text(line + "\n", encoding="utf-8")
        return True
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"(?m)^{re.escape(env_name)}=.*$")
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text, encoding="utf-8")
    return True


def list_runtime_key_status(env_names: list[str]) -> dict[str, bool]:
    """批量检查环境变量是否已配置。"""

    saved = load_runtime_keys()
    out: dict[str, bool] = {}
    for name in env_names:
        out[name] = bool(os.getenv(name) or saved.get(name))
    return out
