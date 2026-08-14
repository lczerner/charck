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

.PHONY: help test install uninstall clean venv

all: venv

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

test: ## Run the regression suite (exits non-zero on failure)
	$(PYTHON) tests/test_charck.py

lint: venv ## Run the lint checker on the script (flake8)
	.venv/bin/flake8 charck.py

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
	rm -rf build dist .eggs *.egg-info
	rm -rf __pycache__ tests/__pycache__
	rm -f .*.charck-tmp tests/.*.charck-tmp
