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
