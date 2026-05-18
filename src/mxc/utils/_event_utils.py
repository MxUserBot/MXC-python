def should_ignore_event(mx, evt) -> bool:
    if not getattr(getattr(evt, "content", None), "body", None):
        return True
    return bool(mx.start_time and evt.timestamp < (mx.start_time - 10000))


async def get_prefix(mx) -> str:
    return mx._prefixes


async def starts_with_command(mx, body: str) -> bool:
    return body.startswith(tuple(await get_prefix(mx)))
