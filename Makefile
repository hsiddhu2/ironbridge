.PHONY: install lint format test build synth bootstrap deploy run measure destroy leftovers clean

# Region, account and stack name come from the AWS profile and infra/cdk.json context.
# Example: AWS_PROFILE=sandbox make deploy
FIXTURES := ironbridge/jcl/tests/fixtures/carddemo/app
CDK := cd infra && uv run npx cdk

install:
	uv sync --all-extras
	cd infra && npm ci

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .

test:
	uv run pytest -q

build:
	uv run ironbridge jcl to-asl --jcl $(FIXTURES)/jcl --proc $(FIXTURES)/proc \
	  --controlm $(FIXTURES)/scheduler/CardDemo.controlm --out build

synth: build
	$(CDK) synth --quiet

bootstrap:
	$(CDK) bootstrap

deploy: build
	$(CDK) deploy --all --require-approval never --outputs-file ../outputs.json

run:
	uv run python scripts/run.py

measure: build
	uv run python scripts/measure.py all

destroy: build
	$(CDK) destroy --all --force
	rm -f outputs.json

leftovers:
	uv run python scripts/leftovers.py

clean:
	rm -rf .venv build infra/cdk.out infra/node_modules outputs.json .pytest_cache .ruff_cache
