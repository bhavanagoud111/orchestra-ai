from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from orchestra.db.models import Base
from orchestra.services import Services


def get_services(request: Request) -> Services:
    return request.app.state.services


async def get_or_404[M: Base](session: AsyncSession, model: type[M], id: str, what: str) -> M:
    row = await session.get(model, id)
    if row is None:
        raise HTTPException(404, f"{what} not found")
    return row
