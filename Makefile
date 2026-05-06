SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE ?= .env
WORKERS_ENV ?= deploy/workers.env

ROUTERS ?= round-robin least-loaded kv
N ?= 20
QPS ?= 0.5
SPLIT ?= lite
SEED ?= 42

DC ?= docker-compose

help:
	@echo "Targets:"
	@echo "  up              - infra (NATS, otel-collector, jaeger) via $(DC)"
	@echo "  down            - stop infra"
	@echo "  workers         - launch vLLM PD workers per deploy/workers.env"
	@echo "  workers-down    - stop vLLM workers"
	@echo "  frontend        - launch dynamo.frontend (--discovery-backend file)"
	@echo "  frontend-down   - stop dynamo.frontend"
	@echo "  opencode        - launch opencode serve with experimental workspaces"
	@echo "  opencode-down   - stop opencode"
	@echo "  kill-all        - stop opencode + frontend + workers"
	@echo "  smoke           - run a small workload (N=$(N), QPS=$(QPS))"
	@echo "  sweep           - sweep ROUTERS=$(ROUTERS)"
	@echo "  test            - pytest"

up:
	$(DC) up -d

down:
	$(DC) down

workers:
	@bash deploy/launch_workers.sh start

workers-down:
	@bash deploy/launch_workers.sh stop

frontend:
	@bash deploy/launch_frontend.sh start

frontend-down:
	@bash deploy/launch_frontend.sh stop

opencode:
	@bash deploy/launch_opencode.sh start

opencode-down:
	@bash deploy/launch_opencode.sh stop

kill-all: opencode-down frontend-down workers-down

smoke:
	python -m testbed run \
		--split $(SPLIT) \
		--num-samples $(N) \
		--qps $(QPS) \
		--seed $(SEED) \
		--router $${ROUTER_MODE:-round-robin} \
		--out results/smoke

sweep:
	@for r in $(ROUTERS); do \
		echo "==> router=$$r"; \
		python -m testbed run \
			--split $(SPLIT) \
			--num-samples $(N) \
			--qps $(QPS) \
			--seed $(SEED) \
			--router $$r \
			--out results/sweep_$$r ; \
	done

test:
	pytest -q

.PHONY: help up down workers workers-down frontend frontend-down opencode opencode-down kill-all smoke sweep test
