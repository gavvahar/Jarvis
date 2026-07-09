import httpx


async def refresh_oauth_token(token_url: str, payload: dict, *, as_json: bool = True) -> dict:
    """POST a refresh_token grant and return the parsed token response."""
    kwargs = {"json": payload} if as_json else {"data": payload}
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(token_url, **kwargs)
        r.raise_for_status()
        return r.json()
