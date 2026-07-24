.PHONY: install uv-install venv frontend-deps frontend-build run run-backend test-run test clean

VENV_PATH ?= .venv
PYTHON := $(VENV_PATH)/bin/python
UV ?= $(or $(shell command -v uv 2>/dev/null),$(HOME)/.local/bin/uv)
UV_INSTALL_URL ?= https://astral.sh/uv/install.sh
PYPI_INDEX_URL ?= https://mirrors.aliyun.com/pypi/simple
FRONTEND_DIR := web/frontend

install: venv
	$(UV) pip install --python $(PYTHON) -i $(PYPI_INDEX_URL) -r requirements.txt
	$(MAKE) frontend-deps
	$(MAKE) frontend-build

uv-install:
	@if command -v uv >/dev/null 2>&1; then \
		echo "Using uv: $$(command -v uv)"; \
	elif [ -x "$(HOME)/.local/bin/uv" ]; then \
		echo "Using uv: $(HOME)/.local/bin/uv"; \
	else \
		echo "Installing uv from $(UV_INSTALL_URL)"; \
		curl -LsSf "$(UV_INSTALL_URL)" | sh; \
	fi

venv: uv-install
	@test -x "$(PYTHON)" || "$(UV)" venv "$(VENV_PATH)"

frontend-deps:
	cd "$(FRONTEND_DIR)" && npm install

frontend-build: frontend-deps
	cd "$(FRONTEND_DIR)" && npm run build

# Override with: make run ZERO_BIND_HOST=0.0.0.0
ZERO_BIND_HOST ?= 127.0.0.1
ZERO_BIND_PORT ?= 8000

run: install
	$(PYTHON) -m uvicorn web.backend.main:app --host $(ZERO_BIND_HOST) --port $(ZERO_BIND_PORT)

run-backend: venv
	$(PYTHON) -m uvicorn web.backend.main:app --host $(ZERO_BIND_HOST) --port $(ZERO_BIND_PORT) --reload

test-run: install
	@echo "Backend: http://$(ZERO_BIND_HOST):$(ZERO_BIND_PORT)"
	@echo "Vite:    http://localhost:5173"
	@$(PYTHON) -m uvicorn web.backend.main:app --host $(ZERO_BIND_HOST) --port $(ZERO_BIND_PORT) --reload & \
	cd "$(FRONTEND_DIR)" && npm run dev

test: venv
	@$(UV) pip install --python $(PYTHON) -i $(PYPI_INDEX_URL) pytest >/dev/null 2>&1 || \
		$(PYTHON) -m pip install -q pytest
	$(PYTHON) -m pytest -q

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
	rm -rf build dist .pytest_cache "$(FRONTEND_DIR)/dist"
	@echo "Cleaned temporary files"
