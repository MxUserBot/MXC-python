# ©️ Pasha Hatsune, 2025-2026
# This file is a part of MXC
# 🌐 https://github.com/MxUserBot/MXC-python
# You can redistribute it and/or modify it under the terms of the GNU AGPLv3
# 🔑 https://www.gnu.org/licenses/agpl-3.0.html

import aiohttp


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
