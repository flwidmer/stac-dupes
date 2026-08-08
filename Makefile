POETRY_VERSION ?= 2.1.4
POETRY ?= poetry
POETRY_INSTALL ?= $(HOME)/.local/bin/poetry

.PHONY: install lint
install:
	curl -sSL https://install.python-poetry.org | POETRY_VERSION=$(POETRY_VERSION) python3 -
	$(POETRY_INSTALL) install

lint:
	$(POETRY) run ruff check .
