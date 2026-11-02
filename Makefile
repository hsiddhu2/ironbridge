.PHONY: install lint test synth bootstrap deploy run measure destroy leftovers clean

STACK ?= IronbridgeStack
REGION ?= us-east-1

install:
	uv sync --all-extras
	cd infra && npm ci

lint:
	uv run ruff check src tests scripts infra
	uv run ruff format --check src tests scripts infra

format:
	uv run ruff format src tests scripts infra

test:
	uv run pytest -q

synth:
	cd infra && uv run cdk synth --quiet

bootstrap:
	cd infra && uv run cdk bootstrap

deploy:
	cd infra && uv run cdk deploy $(STACK) --require-approval never --outputs-file ../outputs.json

run:
	uv run python scripts/run.py

measure:
	uv run python scripts/measure.py --runs 3 --out results.csv

destroy:
	cd infra && uv run cdk destroy $(STACK) --force

leftovers:
	uv run python scripts/leftovers.py --stack $(STACK) --region $(REGION)

clean:
	rm -rf .venv infra/cdk.out outputs.json .pytest_cache .ruff_cache
