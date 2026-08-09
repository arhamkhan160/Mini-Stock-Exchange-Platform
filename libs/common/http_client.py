"""httpx helper for synchronous service-to-service REST calls.

Retries ONLY on transport errors and 502/503/504 — never on 4xx, and never on
a POST that may already have been applied unless the endpoint is idempotent
(all our /internal/ endpoints are idempotent on order_id, so this is safe).
"""

import asyncio
import logging

import httpx

from .config import settings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {502, 503, 504}


class ServiceCallError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


async def call_service(
    method: str,
    url: str,
    *,
    json: dict | None = None,
    params: dict | None = None,
    internal: bool = True,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.25,
) -> dict:
    headers = {"X-Internal-Key": settings.INTERNAL_API_KEY} if internal else {}
    last_detail = "unreachable"
    last_status = 503
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(retries + 1):
            try:
                response = await client.request(method, url, json=json, params=params, headers=headers)
            except httpx.TransportError as exc:
                last_detail, last_status = str(exc), 503
                log.warning("transport error calling %s (attempt %s): %s", url, attempt + 1, exc)
                await asyncio.sleep(backoff * (2 ** attempt))
                continue
            if response.status_code in RETRYABLE_STATUS and attempt < retries:
                last_detail, last_status = response.text, response.status_code
                await asyncio.sleep(backoff * (2 ** attempt))
                continue
            if response.status_code >= 400:
                detail = response.text
                try:
                    detail = response.json().get("detail", detail)
                except Exception:
                    pass
                raise ServiceCallError(response.status_code, detail)
            if not response.content:
                return {}
            return response.json()
    raise ServiceCallError(last_status, last_detail)
