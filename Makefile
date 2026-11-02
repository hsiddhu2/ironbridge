.PHONY: node-check install lint format test build synth bootstrap deploy run measure destroy leftovers clean

# Stack name comes from infra/cdk.json context. Region: AWS_REGION, else cdk.json, else us-east-1.
# Example: AWS_PROFILE=sandbox make deploy
FIXTURES := ironbridge/jcl/tests/fixtures/carddemo/app
CDK := cd infra && uv run npx cdk

# Warns on Node 20, fails below 20. See scripts/check-node.sh.
node-check:
	@sh scripts/check-node.sh

install: node-check
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

synth: node-check build
	$(CDK) synth --quiet

bootstrap: node-check
	$(CDK) bootstrap

deploy: node-check build
	$(CDK) deploy --all --require-approval never --outputs-file ../outputs.json

run:
	uv run python scripts/run.py

measure: build
	uv run python scripts/measure.py all

destroy: node-check build
	$(CDK) destroy --all --force
	rm -f outputs.json

leftovers:
	uv run python scripts/leftovers.py

clean:
	rm -rf .venv build infra/cdk.out infra/node_modules outputs.json .pytest_cache .ruff_cache
