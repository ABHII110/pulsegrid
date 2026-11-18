.DEFAULT_GOAL := help
.PHONY: help install lint format typecheck test cov check migrate run worker up down

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install runtime and dev dependencies
	uv sync

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-fix lint issues and format code
	uv run ruff check --fix .
	uv run ruff format .

typecheck: ## Run mypy in strict mode
	uv run mypy

test: ## Run the test suite
	uv run pytest

cov: ## Run tests with coverage
	uv run pytest --cov --cov-report=term-missing

check: lint typecheck test ## Run every quality gate

migrate: ## Apply database migrations
	uv run pulsegrid db upgrade

run: ## Start the REST API
	uv run pulsegrid serve

worker: ## Start the probe worker
	uv run pulsegrid worker

up: ## Start the full stack with Docker Compose
	docker compose up --build -d

down: ## Stop the Docker Compose stack
	docker compose down
