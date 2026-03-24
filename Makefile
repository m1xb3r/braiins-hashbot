.PHONY: help setup build up down restart logs logs-engine logs-dashboard shell-engine shell-dashboard ps clean

# Required files that must exist before docker compose build will work
REQUIRED_FILES = Dockerfile docker-compose.yml requirements.txt \
                 paths.py config.py keystore.py api.py main.py dashboard.py \
                 templates/index.html

help:
	@echo ""
	@echo "  Hashbot -- Docker management commands"
	@echo ""
	@echo "  First-time setup:"
	@echo "    make setup           Check files, create .env"
	@echo ""
	@echo "  Day-to-day:"
	@echo "    make up              Build image and start all services"
	@echo "    make down            Stop and remove containers"
	@echo "    make restart         Restart all services (e.g. after editing config)"
	@echo "    make build           Force-rebuild the Docker image"
	@echo "    make ps              Show running containers"
	@echo ""
	@echo "  Logs:"
	@echo "    make logs            Tail logs from both services"
	@echo "    make logs-engine     Tail engine logs only"
	@echo "    make logs-dashboard  Tail dashboard logs only"
	@echo ""
	@echo "  Debug:"
	@echo "    make shell-engine    Open a shell in the engine container"
	@echo "    make shell-dashboard Open a shell in the dashboard container"
	@echo ""
	@echo "  Cleanup:"
	@echo "    make clean           Remove containers, image, and data volume (!)"
	@echo ""

# ── Setup ──────────────────────────────────────────────────────────────────
setup: _check_files
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env"; \
	else \
		echo ".env already exists, skipping"; \
	fi
	@echo ""
	@echo "  All good. Next steps:"
	@echo "  1. Run:  make up"
	@echo "  2. Open: http://localhost:8000"
	@echo "  3. Paste your Braiins API key when prompted"
	@echo ""

# Internal target: verify all required files are present before doing anything
_check_files:
	@MISSING=""; \
	for f in $(REQUIRED_FILES); do \
		if [ ! -f "$$f" ]; then \
			MISSING="$$MISSING\n  missing: $$f"; \
		fi; \
	done; \
	if [ -n "$$MISSING" ]; then \
		echo ""; \
		echo "ERROR: Some required files are missing:"; \
		printf "$$MISSING"; \
		echo ""; \
		echo ""; \
		echo "Make sure you have the full project:"; \
		echo "  - All .py files in the project root"; \
		echo "  - templates/index.html  (note: templates is a subdirectory)"; \
		echo "  - Dockerfile, docker-compose.yml, requirements.txt"; \
		echo ""; \
		exit 1; \
	fi

# ── Build / start ──────────────────────────────────────────────────────────
build: _check_files
	docker compose build --no-cache

up: _check_files
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "Created .env from .env.example"; \
	fi
	docker compose up -d --build
	@echo ""
	@echo "Hashbot is running."
	@PORT=$$(grep HASHBOT_PORT .env 2>/dev/null | cut -d= -f2); echo "  Dashboard -> http://localhost:$${PORT:-8000}"
	@echo "  Logs      -> make logs"
	@echo ""

down:
	docker compose down

restart:
	docker compose restart

ps:
	docker compose ps

# ── Logs ───────────────────────────────────────────────────────────────────
logs:
	docker compose logs -f --tail=50

logs-engine:
	docker compose logs -f --tail=50 engine

logs-dashboard:
	docker compose logs -f --tail=50 dashboard

# ── Debug shells ───────────────────────────────────────────────────────────
shell-engine:
	docker compose exec engine /bin/sh

shell-dashboard:
	docker compose exec dashboard /bin/sh

# ── Nuclear cleanup ────────────────────────────────────────────────────────
clean:
	@echo "WARNING: This removes containers, image, AND the data volume."
	@read -p "Type 'yes' to confirm: " confirm && [ "$$confirm" = "yes" ] || exit 1
	docker compose down -v --rmi local
	@echo "Done."
