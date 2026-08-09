"""Verify a RUNNING stack: containers, health, queues, replication.

Standard library only, so it works with no pip installs at all.

    make up
    python scripts/verify_stack.py

This checks the platform is wired correctly. It is not the end-to-end business
flow test — that is `scripts/smoke_test.py` (register, deposit, trade, settle).

Services that have not been written yet are reported as PENDING, not FAIL, so
this stays useful while the team is still building. A service whose folder
exists under services/ IS expected to answer.
"""

import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SERVICES = [
    ("gateway", 8000, "gateway"),
    ("user-service", 8001, "user-service"),
    ("account-service", 8002, "account-service"),
    ("order-service", 8003, "order-service"),
    ("matching-engine", 8004, "matching-engine"),
    ("market-data-service", 8005, "market-data-service"),
    ("portfolio-service", 8006, "portfolio-service"),
    ("notification-service", 8007, "notification-service"),
]

EXPECTED_QUEUES = {
    "q.matching.order_accepted", "q.matching.cancel_requested",
    "q.order.trade_executed", "q.order.order_cancelled", "q.order.cancel_rejected",
    "q.account.trade_executed", "q.account.order_cancelled", "q.account.order_rejected",
    "q.portfolio.trade_executed", "q.portfolio.order_cancelled", "q.portfolio.order_rejected",
    "q.marketdata.trade_executed",
    "q.notification.trade_executed", "q.notification.order_cancelled",
    "q.notification.order_rejected",
}

PASS, FAIL, PENDING, WARN = "PASS", "FAIL", "PENDING", "WARN"
results: list[tuple[str, str, str]] = []


def env_value(key: str, default: str) -> str:
    """Read a key from .env (or the real environment), so this keeps working
    when the deployment uses credentials other than the defaults."""
    import os

    if key in os.environ:
        return os.environ[key]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    return default


RABBIT_AUTH = (
    env_value("RABBITMQ_DEFAULT_USER", "guest"),
    env_value("RABBITMQ_DEFAULT_PASS", "guest"),
)


def record(status: str, label: str, detail: str = "") -> None:
    results.append((status, label, detail))
    print(f"  {status:<7} {label}" + (f" - {detail}" if detail else ""))


def get_json(url: str, timeout: float = 5.0, auth: tuple[str, str] | None = None):
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read() or b"{}")


def docker(*args: str) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=60, cwd=str(ROOT)
        )
        return result.returncode, (result.stdout + result.stderr).strip()
    except FileNotFoundError:
        return 127, "docker not installed"
    except subprocess.TimeoutExpired:
        return 124, "docker command timed out"


def check_services() -> dict[str, str]:
    print("\n-- service health --")
    fingerprints: dict[str, str] = {}
    for folder, port, name in SERVICES:
        built = (ROOT / "services" / folder).is_dir()
        try:
            status, body = get_json(f"http://localhost:{port}/health")
        except Exception as exc:
            if built:
                record(FAIL, f"{name}:{port}/health", str(exc)[:70])
            else:
                record(PENDING, f"{name}:{port}", "not implemented yet")
            continue

        if status != 200 or body.get("status") != "ok":
            record(FAIL, f"{name}:{port}/health", f"status={status} body={body}")
            continue
        record(PASS, f"{name}:{port}/health")

        try:
            _ready_status, ready = get_json(f"http://localhost:{port}/ready")
        except urllib.error.HTTPError as exc:  # 503 = degraded, still readable
            try:
                ready = json.loads(exc.read() or b"{}")
                ready = ready.get("detail", ready)
            except Exception:
                ready = {}
            record(WARN, f"{name}:{port}/ready", "degraded")
        except Exception:
            ready = {}
        if isinstance(ready, dict) and ready.get("jwt_secret_fingerprint"):
            fingerprints[name] = ready["jwt_secret_fingerprint"]
    return fingerprints


def check_jwt_agreement(fingerprints: dict[str, str]) -> None:
    print("\n-- shared secret --")
    if len(fingerprints) < 2:
        record(PENDING, "JWT secret agreement", "need at least two live services")
        return
    distinct = set(fingerprints.values())
    if len(distinct) == 1:
        record(PASS, "JWT secret identical across services", next(iter(distinct)))
    else:
        record(FAIL, "JWT secret MISMATCH", json.dumps(fingerprints))


def check_rabbitmq() -> None:
    print("\n-- rabbitmq --")
    try:
        _status, queues = get_json("http://localhost:15672/api/queues", auth=RABBIT_AUTH)
    except Exception as exc:
        record(FAIL, f"rabbitmq management API (as {RABBIT_AUTH[0]})", str(exc)[:70])
        return
    names = {q.get("name", "") for q in queues}
    record(PASS, "rabbitmq reachable", f"{len(names)} queues declared")

    missing = EXPECTED_QUEUES - names
    if missing:
        # Queues appear only once their consumer has started.
        record(PENDING, "event queues", f"{len(EXPECTED_QUEUES) - len(missing)}/{len(EXPECTED_QUEUES)} present")
    else:
        record(PASS, "all 15 event queues declared")

    retry = {n for n in names if n.endswith(".retry")}
    if retry:
        record(PASS, "retry queues present", f"{len(retry)} found")

    dead = [q for q in queues if q.get("name", "").startswith("q.") and q.get("messages", 0) > 100]
    if dead:
        record(WARN, "queue backlog", ", ".join(f"{q['name']}={q['messages']}" for q in dead[:3]))


def check_replication() -> None:
    print("\n-- postgres master-slave replication --")
    code, out = docker(
        "compose", "exec", "-T", "postgres-market-primary",
        "psql", "-U", "mse", "-d", "market_db", "-tAc",
        "SELECT state FROM pg_stat_replication;",
    )
    if code == 127:
        record(PENDING, "replication", "docker not installed")
        return
    if code != 0:
        record(FAIL, "primary unreachable", out[:70])
        return
    if "streaming" in out:
        record(PASS, "replica is streaming from the primary")
    else:
        record(FAIL, "no streaming replica", out[:70] or "pg_stat_replication is empty")

    code, out = docker(
        "compose", "exec", "-T", "postgres-market-replica",
        "psql", "-U", "mse", "-d", "market_db", "-tAc", "SELECT pg_is_in_recovery();",
    )
    if code == 0 and out.strip().startswith("t"):
        record(PASS, "replica is in recovery mode (a real standby)")
    else:
        record(FAIL, "replica is not a standby", out[:70])


def check_redis() -> None:
    print("\n-- redis --")
    code, out = docker("compose", "exec", "-T", "redis", "redis-cli", "ping")
    if code == 127:
        record(PENDING, "redis", "docker not installed")
    elif code == 0 and "PONG" in out:
        record(PASS, "redis responds to PING")
    else:
        record(FAIL, "redis", out[:70])


def check_frontend() -> None:
    print("\n-- frontend --")
    try:
        with urllib.request.urlopen("http://localhost:3000", timeout=8) as response:
            record(PASS if response.status == 200 else FAIL, "frontend :3000", f"HTTP {response.status}")
    except Exception as exc:
        record(PENDING, "frontend :3000", str(exc)[:70])


def main() -> int:
    print("Verifying the Mini Stock Exchange stack (http://localhost)")
    fingerprints = check_services()
    check_jwt_agreement(fingerprints)
    check_rabbitmq()
    check_replication()
    check_redis()
    check_frontend()

    counts = {status: sum(1 for s, _, _ in results if s == status) for status in (PASS, FAIL, WARN, PENDING)}
    print("\n" + "=" * 60)
    print(f"PASS {counts[PASS]}   FAIL {counts[FAIL]}   WARN {counts[WARN]}   PENDING {counts[PENDING]}")
    if counts[FAIL]:
        print("\nFailures:")
        for status, label, detail in results:
            if status == FAIL:
                print(f"  - {label}: {detail}")
        return 1
    if counts[PENDING]:
        print("\nPending items are components not built or not started yet, not failures.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
