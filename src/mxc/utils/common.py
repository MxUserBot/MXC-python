# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import os
import platform
import re
import shlex
from pathlib import Path
from typing import Any

import aiohttp
import psutil

COMM_DIR = Path(__file__).resolve().parents[2] / "community"


async def request(
    url: str,
    method: str = "GET",
    return_type: str = "json",
    **kwargs,
):
    async with aiohttp.ClientSession() as session:
        async with session.request(method, url, **kwargs) as response:
            response.raise_for_status()

            if return_type == "json":
                return await response.json(content_type=None)
            if return_type == "text":
                return await response.text()
            if return_type == "bytes":
                return await response.read()
            return response


def get_platform() -> str:
    os_info = f"{platform.system()} {platform.release()}"
    hostname = platform.node()
    ram = psutil.virtual_memory()

    used_ram = ram.used // 1024 // 1024
    total_ram = ram.total // 1024 // 1024
    ram_usage = f"{used_ram} / {total_ram} MB"
    cpu_usage = psutil.cpu_percent()

    return (
        f"<b>Сервер:</b> `{hostname}`<br>"
        f"<b>ОС:</b> `{os_info}`<br>"
        f"<b>Память:</b> `{ram_usage}`<br>"
        f"<b>Нагрузка CPU:</b> `{cpu_usage}%`"
    )


def get_commands(cls) -> dict:
    cmds = {}
    for attr_name in dir(cls):
        method = getattr(cls, attr_name)
        if callable(method) and getattr(method, "is_command", False):
            cmds[method.command_name] = method
    return cmds


async def get_args_raw(mx, event) -> str:
    cmd_text = ""
    if isinstance(event, str):
        cmd_text = event
    elif hasattr(event, "content") and hasattr(event.content, "body"):
        content = event.content
        relates = getattr(content, "relates_to", None) or getattr(content, "_relates_to", None)
        if relates and getattr(relates, "rel_type", None) == "m.replace":
            new_content = getattr(content, "new_content", None)
            if new_content:
                cmd_text = getattr(new_content, "body", None) or ""
        if not cmd_text:
            from .events import _apply_latest_edit
            await _apply_latest_edit(mx, event.room_id, event.event_id, event)
            cmd_text = event.content.body
    elif hasattr(event, "message"):
        cmd_text = event.message

    cmd_args = ""
    if cmd_text:
        parts = cmd_text.strip().split(maxsplit=1)
        cmd_args = parts[1].strip() if len(parts) > 1 else ""

    if len(cmd_args.split()) > 1:
        return cmd_args

    try:
        relates = getattr(event.content, "relates_to", None) or getattr(event.content, "_relates_to", None)
        if relates and getattr(relates, "in_reply_to", None):
            replied_event = await mx.client.get_event(
                room_id=event.room_id,
                event_id=relates.in_reply_to.event_id,
            )
            reply_text = getattr(replied_event.content, "body", None)
            if reply_text:
                reply_text = reply_text.strip()
                return f"{cmd_args} {reply_text}" if cmd_args else reply_text
    except Exception:
        pass

    return cmd_args


async def get_args(mx, event) -> list:
    raw = await get_args_raw(mx, event)
    if not raw:
        return []

    try:
        args = shlex.split(raw)
    except ValueError:
        args = raw.split()

    return [arg for arg in args if arg]


def escape_html(text: str, /) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_quotes(text: str, /) -> str:
    return escape_html(text).replace('"', "&quot;")


def normalize_text(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"<[^>]+>", "", text)
    allowed_extra = "., \n-()[]{}\"'«»„“:@"
    cleaned = "".join(c for c in text if c.isalnum() or c in allowed_extra)
    cleaned = re.sub(r" +", " ", cleaned)
    return cleaned.strip()


def get_base_dir() -> str:
    return get_dir(__file__)


def get_dir(mod: str) -> str:
    return os.path.abspath(os.path.dirname(os.path.abspath(mod)))


def _get_safe_path(filename: str) -> Path:
    safe_name = os.path.basename(filename)
    final_path = (COMM_DIR / safe_name).resolve()

    if COMM_DIR not in final_path.parents and final_path != COMM_DIR:
        raise PermissionError("Security: Access restricted to community folder only.")

    forbidden_ext = {".py", ".pyc", ".sh", ".bash", ".exe", ".so", ".dll"}
    if final_path.suffix.lower() in forbidden_ext:
        raise PermissionError(f"Security: Prohibited file extension: {final_path.suffix}")

    return final_path


async def safe_save(file_bytes: bytes, filename: str) -> str:
    path = _get_safe_path(filename)
    with open(path, "wb") as f:
        f.write(file_bytes)
    return str(path)


async def safe_remove(filename: str):
    path = _get_safe_path(filename)
    if path.exists():
        os.remove(path)


def should_ignore_event(mx, evt) -> bool:
    if not getattr(getattr(evt, "content", None), "body", None):
        return True

    return bool(mx.start_time and evt.timestamp < (mx.start_time - 10000))


async def get_prefix(mx) -> str:
    return mx._prefixes


async def starts_with_command(mx, body: str) -> bool:
    return body.startswith(tuple(await get_prefix(mx)))


def convert_repo_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""

    # Убираем .git из урла (https://github.com/user/repo.git → https://github.com/user/repo)
    url = re.sub(r"\.git(?=/|$)", "", url)

    if "raw.githubusercontent.com" in url:
        return url

    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+)(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?",
        url,
    )
    if not match:
        return url

    owner, repo, branch, path = match.groups()
    branch = branch or "main"
    path = (path or "").strip("/")
    raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
    return f"{raw}/{path}" if path else raw
