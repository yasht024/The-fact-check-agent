from uuid import UUID

import psycopg


async def create_run(database_url: str, run_id: UUID, token_hash: bytes, pdf: bytes) -> None:
    async with await psycopg.AsyncConnection.connect(database_url, connect_timeout=5) as connection:
        await connection.execute(
            "INSERT INTO runs (run_id, access_token_hash) VALUES (%s, %s)",
            (run_id, token_hash),
        )
        await connection.execute(
            "INSERT INTO uploads (run_id, pdf) VALUES (%s, %s)",
            (run_id, pdf),
        )


async def get_run(database_url: str, run_id: UUID) -> tuple[str, str, bytes] | None:
    async with await psycopg.AsyncConnection.connect(
        database_url, connect_timeout=5, autocommit=True
    ) as connection:
        cursor = await connection.execute(
            "SELECT state, stage, access_token_hash FROM runs WHERE run_id = %s",
            (run_id,),
        )
        return await cursor.fetchone()
