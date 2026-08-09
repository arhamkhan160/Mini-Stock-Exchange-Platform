import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_validator

from common.money import MoneyError, to_money

MAX_DEPOSIT = Decimal("1000000.0000")


class Balance(BaseModel):
    user_id: str
    cash_balance: str
    held_balance: str
    available_balance: str
    currency: str = "USD"


class DepositRequest(BaseModel):
    amount: str

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: str) -> str:
        try:
            val = to_money(v)
        except MoneyError as exc:
            raise ValueError(str(exc))
        if val <= 0:
            raise ValueError("amount must be positive")
        if val > MAX_DEPOSIT:
            raise ValueError(f"amount must be <= {MAX_DEPOSIT}")
        return v


class TransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    amount: str
    balance_after: str
    reference_id: uuid.UUID | None
    description: str | None
    created_at: datetime

    @field_validator("amount", "balance_after", mode="before")
    @classmethod
    def _stringify(cls, v):
        return v if isinstance(v, str) else str(v)


# --------------------------------------------------------------- internal ---
class ReservationRequest(BaseModel):
    order_id: str
    user_id: str
    amount: str

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: str) -> str:
        try:
            val = to_money(v)
        except MoneyError as exc:
            raise ValueError(str(exc))
        if val <= 0:
            raise ValueError("amount must be positive")
        return v


class ReservationOut(BaseModel):
    order_id: str
    user_id: str
    amount: str
    status: str  # "HELD"


class ReservationReleaseRequest(BaseModel):
    # Omitted or null => release whatever remains for the order.
    amount: str | None = None

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            val = to_money(v)
        except MoneyError as exc:
            raise ValueError(str(exc))
        if val <= 0:
            raise ValueError("amount must be positive")
        return v


class ReservationReleaseOut(BaseModel):
    order_id: str
    released_amount: str
    remaining_amount: str


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
