"""Wire shapes.

Deliberately permissive on input: `quantity` and `price` arrive as `Any` and
are validated by hand with `common.money`, so a bad value produces a 400 with a
human sentence instead of FastAPI's 422 validation blob. The contract says 400
for validation errors, and "quantity must be a whole number of shares" is what
the order ticket shows the user verbatim.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import Order


class PlaceOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    side: str
    order_type: str
    price: Any = None
    quantity: Any = None
    client_order_id: str | None = Field(default=None, max_length=64)


class OrderOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    client_order_id: str | None
    symbol: str
    side: str
    order_type: str
    price: str | None           # money is a STRING on the wire
    quantity: int
    filled_quantity: int
    avg_fill_price: str
    status: str
    reserved_amount: str | None
    reject_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, order: Order) -> "OrderOut":
        from common.money import money_str

        return cls(
            id=order.id,
            user_id=order.user_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            price=money_str(order.price) if order.price is not None else None,
            quantity=order.quantity,
            filled_quantity=order.filled_quantity,
            avg_fill_price=money_str(order.avg_fill_price or 0),
            status=order.status,
            reserved_amount=money_str(order.reserved_amount) if order.reserved_amount is not None else None,
            reject_reason=order.reject_reason,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )


class OrderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    from_status: str | None
    to_status: str
    note: str | None
    created_at: datetime


class CancelAccepted(BaseModel):
    status: str = "CANCEL_PENDING"


class HealthOut(BaseModel):
    status: str
    service: str


class ReadyOut(BaseModel):
    status: str
    service: str
    database: str
    redis: str
    broker: str
    jwt_secret_fingerprint: str
