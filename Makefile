# SPDX-License-Identifier: MIT
# Copyright 2026, Lukáš Czerner <lukas@czerner.cz>
#
# charck - test and install helpers.
#
# Nothing here writes outside your home directory. `install` goes through pipx,
# which builds the project into its own virtualenv under ~/.local/pipx/venvs and
# links the `charck` command into ~/.local/bin. No sudo, no system Python, no
# /usr/local. Use `uninstall` to undo it.
#
# Written for GNU make 3.81, which is what macOS ships, so no .ONESHELL and no
# $(file ...) here.

PYTHON ?= python3
PIPX   ?= pipx

.DEFAULT_GOAL := help

.PHONY: all help test lint install uninstall clean venv

# `python3 -m venv` creates .venv/bin/activate, which is also the stamp the
# venv rule is written against. A pip step that then fails would leave the
# stamp behind, newer than pyproject.toml, and every later `make test` would
# run a pytest that was never installed.
.DELETE_ON_ERROR:

all: venv ## Same as venv

help: ## Show this help
	@echo "charck - make <target>"
	@echo
	@awk 'BEGIN { FS = ":.*## " } \
		/^[a-z][a-z-]*:.*## / { printf "  %-10s %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST) < /dev/null
	@echo
	@echo "Overridable: PYTHON=$(PYTHON) PIPX=$(PIPX)"
	@echo "Installs are user-level only. Nothing is written outside \$$HOME."

venv: .venv/bin/activate ## Build virtualenv in .venv from the 'dev' dependencies

.venv/bin/activate: pyproject.toml
	$(PYTHON) -m venv .venv/
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e ".[dev]"
	touch .venv/bin/activate
	@echo
	@echo "To activate the virtual environment, run:"
	@echo "    source .venv/bin/activate"
	@echo
	@echo "The 'charck' command is now available in .venv/bin/"

test: venv ## Run the regression suite (exits non-zero on failure)
	.venv/bin/pytest

lint: venv ## Run the lint checker on the script and tests (flake8)
	.venv/bin/flake8 charck.py tests/

install: ## Install the charck command into ~/.local/bin, via pipx
	$(PIPX) install --force .
	@echo
	@echo "Installed. If the command is not found, run: $(PIPX) ensurepath"

uninstall: ## Remove the pipx installation
	$(PIPX) uninstall charck

# Not `find`: in the main checkout it would descend into .claude/worktrees and
# clean out unrelated checkouts. The temp files are what an interrupted --fix
# leaves behind, which write_atomic names .<file>.<random>.charck-tmp.
clean: ## Remove build artifacts, caches and stray --fix temp files
	rm -rf build dist .eggs *.egg-info .venv/
	rm -rf __pycache__ tests/__pycache__ .pytest_cache
	rm -f .*.charck-tmp tests/.*.charck-tmp
