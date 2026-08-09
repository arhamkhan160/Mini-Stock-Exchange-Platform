"""Small persistence helper shared by routes.py and handlers.py."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Account


async def get_or_create_account(session: AsyncSession, user_id: uuid.UUID) -> Account:
    """Locks the row (or the gap, via INSERT) for the rest of the caller's
    transaction — every caller holds `lock:funds:{user_id}` around this too,
    but the row lock is what actually stops two Postgres transactions from
    both reading a stale balance."""
    stmt = select(Account).where(Account.user_id == user_id).with_for_update()
    account = (await session.execute(stmt)).scalars().first()
    if account is None:
        account = Account(user_id=user_id)
        session.add(account)
        await session.flush()
    return account
