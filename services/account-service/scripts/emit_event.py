"""Fake the Matching Engine for local, standalone Account Service dev.

Usage (run from services/account-service, against `docker-compose.dev.yml`):
    python scripts/emit_event.py trade.executed '{"trade_id":"...", ...}'

The payload is wrapped in a real envelope via common.events.envelope, so the
shape on the wire always matches the contract in libs/common/events.py.
"""

import asyncio
import json
import sys

from common.events import Broker, envelope


async def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    event_type, payload_json = sys.argv[1], sys.argv[2]
    payload = json.loads(payload_json)

    broker = Broker("amqp://guest:guest@localhost:5672/", "tester")
    await broker.connect()
    env = envelope(event_type, payload)
    await broker.publish(event_type, env)
    await broker.close()
    print(f"published {event_type} event_id={env['event_id']}")


if __name__ == "__main__":
    asyncio.run(main())
