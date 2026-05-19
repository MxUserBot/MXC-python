# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC-python
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

def should_ignore_event(mx, evt) -> bool:
    if not getattr(getattr(evt, "content", None), "body", None):
        return True
    return bool(mx.start_time and evt.timestamp < (mx.start_time - 10000))


async def get_prefix(mx) -> str:
    return mx._prefixes


async def starts_with_command(mx, body: str) -> bool:
    return body.startswith(tuple(await get_prefix(mx)))
