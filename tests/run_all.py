"""Run every offline test suite in one go.

    python tests/run_all.py

Offline means: no Docker, no database, no broker. Suites that need real
infrastructure (the per-service `selfcheck.py` scripts) are listed at the end
with the command to run them once the stack is up.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SUITES = [
    ("contract  (libs/common)", ROOT / "tests" / "test_contract.py"),
    ("routes    (gateway + notification)", ROOT / "tests" / "test_routes.py"),
]
NEEDS_INFRA = [
    (
        "gateway selfcheck",
        "python services/gateway/selfcheck.py",
        "no infrastructure needed, but PYTHONPATH must include libs/",
    ),
    (
        "notification selfcheck",
        "python services/notification-service/selfcheck.py",
        "needs Postgres + Redis: docker compose -f services/notification-service/docker-compose.dev.yml up -d",
    ),
    (
        "stack verification",
        "python scripts/verify_stack.py",
        "needs the full stack: make up",
    ),
]


def main() -> int:
    failures = []
    for label, path in SUITES:
        print(f"\n=== {label} ===")
        result = subprocess.run([sys.executable, str(path)], cwd=str(ROOT))
        if result.returncode != 0:
            failures.append(label)

    print("\n" + "=" * 60)
    if failures:
        print("FAILED: " + ", ".join(failures))
    else:
        print(f"All {len(SUITES)} offline suites passed.")

    print("\nRequires a running stack (not run here):")
    for label, command, note in NEEDS_INFRA:
        print(f"  {label}\n      {command}\n      {note}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
