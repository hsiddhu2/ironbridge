.PHONY: install lint format test ci clean

# Repository-level targets: the ironbridge package and every demo's offline checks.
# Each post's deploy, run, measure and destroy targets live in demos/<nn-name>/Makefile.
DEMOS := $(sort $(dir $(wildcard demos/*/Makefile)))

install:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

test:
	uv run pytest -q

# What CI runs. No AWS credentials needed.
ci: lint test
	@for d in $(DEMOS); do echo "== $$d"; $(MAKE) -C $$d install-infra ci || exit 1; done

clean:
	rm -rf .venv .pytest_cache .ruff_cache
	@for d in $(DEMOS); do $(MAKE) -C $$d clean; done
