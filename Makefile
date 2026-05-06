SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE ?= .env
WORKERS_ENV ?= deploy/workers.env

ROUTERS ?= round-robin least-loaded kv
N ?= 20
QPS ?= 0.5
SPLIT ?= lite
SEED ?= 42

help:
	@echo "Targets:"
	@echo "  up        - infra (etcd, NATS, otel-collector, jaeger) via docker compose"
	@echo "  down      - stop infra"
	@echo "  workers   - launch vLLM PD workers per deploy/workers.env"
	@echo "  workers-down - stop workers (kill PIDs in deploy/run/)"
	@echo "  frontend  - launch dynamo.frontend"
	@echo "  opencode  - launch opencode serve with experimental workspaces"
	@echo "  smoke     - run a small workload (N=$(N), QPS=$(QPS))"
	@echo "  sweep     - sweep ROUTERS=$(ROUTERS)"
	@echo "  test      - pytest"

up:
	docker compose up -d

down:
	docker compose down

workers:
	@bash deploy/launch_workers.sh start

workers-down:
	@bash deploy/launch_workers.sh stop

frontend:
	@bash deploy/launch_frontend.sh

opencode:
	@bash deploy/launch_opencode.sh

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

.PHONY: help up down workers workers-down frontend opencode smoke sweep test
