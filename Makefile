# Mini Stock Exchange Platform
# Windows: run these from Git Bash. Every recipe is a plain command, so you can
# also copy/paste it into PowerShell if `make` is not installed.

INFRA := postgres-user postgres-account postgres-order postgres-market-primary \
         postgres-market-replica postgres-portfolio postgres-notification redis rabbitmq

.PHONY: help env infra up down nuke restart logs ps smoke seed mm repl replica-lag health

help:
	@echo "make env      - create .env from .env.example (does not overwrite)"
	@echo "make infra    - start databases, redis and rabbitmq only"
	@echo "make up       - build and start the whole stack"
	@echo "make down     - stop everything (keeps volumes)"
	@echo "make nuke     - stop everything and DELETE all data volumes"
	@echo "make logs     - follow logs"
	@echo "make ps       - container status"
	@echo "make health   - curl /health on every service"
	@echo "make seed     - seed candle history and run the market-maker bot"
	@echo "make mm       - run the market maker in a loop (live demo)"
	@echo "make smoke    - end-to-end smoke test"
	@echo "make repl     - show streaming replication status"

env:
	@test -f .env || cp .env.example .env
	@echo ".env ready"

infra: env
	docker compose up -d $(INFRA)

up: env
	docker compose up -d --build

down:
	docker compose down

nuke:
	docker compose down -v

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

health:
	@for p in 8000 8001 8002 8003 8004 8005 8006 8007; do \
		printf "%s " $$p; \
		curl -s -o /dev/null -w "%{http_code}\n" http://localhost:$$p/health || echo "down"; \
	done

seed:
	python infra/seed/seed_market_data.py && python infra/seed/market_maker.py

mm:
	python infra/seed/market_maker.py --loop

smoke:
	python scripts/smoke_test.py

# Expect one row in `streaming` state.
repl:
	docker compose exec postgres-market-primary psql -U mse -d market_db \
		-c "SELECT client_addr, state, sync_state FROM pg_stat_replication;"

# Expect `t` — proves reads are served from a real standby.
replica-lag:
	docker compose exec postgres-market-replica psql -U mse -d market_db \
		-c "SELECT pg_is_in_recovery() AS is_replica, now() - pg_last_xact_replay_timestamp() AS lag;"
