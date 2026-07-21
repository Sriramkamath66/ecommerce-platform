.PHONY: help setup keys up down logs test lint build push

SERVICES := user-service product-service cart-service order-service \
            inventory-service payment-service notification-service \
            recommendation-service ai-assistant-service

help:
	@echo "AI E-Commerce Platform"
	@echo ""
	@echo "  make setup     — first-time setup (copy .env, generate keys)"
	@echo "  make keys      — regenerate RS256 JWT keys"
	@echo "  make up        — docker compose up --build -d"
	@echo "  make down      — docker compose down"
	@echo "  make logs      — follow all service logs"
	@echo "  make test      — run all service tests"
	@echo "  make lint      — ruff + mypy all services"
	@echo "  make build     — build all Docker images"

setup:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env — fill in secrets"; fi
	@$(MAKE) keys

keys:
	@mkdir -p keys
	openssl genrsa -out keys/private.pem 2048
	openssl rsa -in keys/private.pem -pubout -out keys/public.pem
	@echo "RS256 key pair generated in ./keys/"

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f

test:
	@for svc in $(SERVICES); do \
		echo "=== Testing $$svc ==="; \
		cd services/$$svc && pip install -r requirements.txt -q && pytest tests/ -v || exit 1; \
		cd ../..; \
	done

lint:
	@for svc in $(SERVICES); do \
		echo "=== Linting $$svc ==="; \
		cd services/$$svc && ruff check app/ && mypy app/ || exit 1; \
		cd ../..; \
	done

build:
	docker compose build

push: build
	@for svc in $(SERVICES); do \
		docker tag ai-ecommerce-$$svc ghcr.io/$$GITHUB_REPO/$$svc:latest; \
		docker push ghcr.io/$$GITHUB_REPO/$$svc:latest; \
	done
