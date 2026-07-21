# AI-Powered E-Commerce Platform

A production-ready microservices e-commerce platform with AI-powered search, recommendations, and a RAG chatbot.

## Architecture

| Service | Port | Description |
|---|---|---|
| Kong API Gateway | 8000 | JWT auth, rate limiting, routing |
| User Service | 8001 | Auth, JWT (RS256), profiles |
| Product Service | 8002 | Catalog, semantic search (Qdrant + VoyageAI) |
| Cart Service | 8003 | Redis-native cart, checkout |
| Order Service | 8004 | Order lifecycle FSM |
| Inventory Service | 8005 | Stock management, reservations |
| Payment Service | 8006 | Mock Stripe payment processing |
| Notification Service | 8007 | Event-driven emails + in-app notifications |
| Recommendation Service | 8008 | Qdrant + Claude personalized recommendations |
| AI Assistant Service | 8009 | RAG chatbot with streaming + tool use |

**Supporting services:** PostgreSQL 16, Redis 7, Qdrant, MailHog (dev email), Prometheus, Grafana

## Quick Start

### Prerequisites
- Docker + Docker Compose
- `make` (or run commands manually)
- Anthropic API key
- Voyage AI API key

### Setup

```bash
# 1. Clone and set up environment
cp .env.example .env
# Edit .env — fill in ANTHROPIC_API_KEY and VOYAGE_API_KEY

# 2. Generate RS256 JWT keys
make keys

# 3. Start all services
make up

# 4. Check health
curl http://localhost:8000/api/v1/users/health
```

### First API calls

```bash
# Register a user
curl -X POST http://localhost:8000/api/v1/users/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "securepass123"}'

# Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/users/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "securepass123"}' | jq -r .access_token)

# Search products (semantic)
curl "http://localhost:8000/api/v1/products/search?q=wireless+headphones" \
  -H "Authorization: Bearer $TOKEN"

# Start a chat with the AI assistant
curl -X POST http://localhost:8000/api/v1/assistant/sessions \
  -H "Authorization: Bearer $TOKEN"
# → {"session_id": "..."}

curl -X POST "http://localhost:8000/api/v1/assistant/sessions/{SESSION_ID}/messages" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "What headphones do you recommend under $100?"}' \
  --no-buffer  # streaming response
```

## Development

```bash
make logs        # follow all service logs
make test        # run all tests
make lint        # ruff + mypy all services
make down        # stop everything
```

## Monitoring

- **Grafana:** http://localhost:3000 (admin / admin)
- **Prometheus:** http://localhost:9090
- **MailHog (emails):** http://localhost:8025
- **Qdrant UI:** http://localhost:6333/dashboard

## Kubernetes Deployment

```bash
# Install with Helm
make keys
kubectl create secret generic app-secrets \
  --from-literal=ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
  --from-literal=VOYAGE_API_KEY=$VOYAGE_API_KEY \
  --from-file=JWT_PRIVATE_KEY=keys/private.pem \
  --from-file=JWT_PUBLIC_KEY=keys/public.pem \
  -n ai-ecommerce

helm upgrade --install ai-ecommerce ./infrastructure/helm/ai-ecommerce \
  -n ai-ecommerce --create-namespace \
  --values ./infrastructure/helm/ai-ecommerce/values.yaml
```

## Tech Stack

| Layer | Technology |
|---|---|
| API Framework | FastAPI (async) |
| ORM | SQLAlchemy 2.0 async |
| Database | PostgreSQL 16 |
| Cache / Pub-Sub | Redis 7 |
| Vector DB | Qdrant |
| LLM | Anthropic Claude (claude-opus-4-8) |
| Embeddings | Voyage AI (voyage-3, 1024-dim) |
| Auth | RS256 JWT |
| API Gateway | Kong (DB-less) |
| Container | Docker + Docker Compose |
| Orchestration | Kubernetes + Helm |
| Monitoring | Prometheus + Grafana |
| CI/CD | GitHub Actions |
| Testing | pytest-asyncio + httpx |
