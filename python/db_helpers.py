async def db_exec_affected(pool, query: str, *args) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(query, *args)
    return result.split()[-1] == "1"
