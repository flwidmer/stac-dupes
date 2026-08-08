POETRY_VERSION ?= 2.1.4
POETRY ?= poetry
POETRY_INSTALL ?= $(HOME)/.local/bin/poetry
COMPOSE ?= docker compose

.PHONY: install lint compose-up jupyter start
install:
	curl -sSL https://install.python-poetry.org | POETRY_VERSION=$(POETRY_VERSION) python3 -
	$(POETRY_INSTALL) install

lint:
	$(POETRY) run ruff check .

compose-up:
	$(COMPOSE) up -d

jupyter:
	$(POETRY) run jupyter lab

start: compose-up
	$(POETRY) run jupyter lab
