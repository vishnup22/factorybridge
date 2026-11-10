# Convenience targets (POSIX shell). On Windows run the underlying commands from DEPLOYMENT_GUIDE.md.
.PHONY: up down seed demo test test-backend test-frontend lint e2e openapi pdfs

up:            ## Build and start the local stack (API :8000, web :5173; DB/Redis bound to 127.0.0.1)
	docker compose up -d --build --wait

down:
	docker compose down

seed:          ## Seed synthetic demo accounts; generated passwords go to backend/var/demo_credentials.txt
	docker compose exec -T api python -m demo.seed
	docker compose cp api:/srv/backend/var/demo_credentials.txt backend/var/demo_credentials.txt

demo:          ## Run the flagship scenario through the live API
	cd backend && uv run python ../scripts/demo_flow.py --base-url http://127.0.0.1:8000 --credentials var/demo_credentials.txt

test: test-backend test-frontend

test-backend:  ## Unit + integration tests (needs the compose Postgres/Redis)
	cd backend && uv run pytest -q -p no:logging

test-frontend:
	cd frontend && npm test

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd frontend && npm run lint && npm run typecheck

e2e:           ## AT-12 Playwright UI flow against the running stack
	cd frontend && npx playwright test

openapi:       ## Regenerate frontend API types from the backend schema
	cd backend && uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=1))" > ../frontend/src/api/openapi.json
	cd frontend && npm run gen:api

pdfs:          ## Regenerate the synthetic demo PDFs
	cd backend && uv run python ../scripts/generate_demo_pdfs.py
