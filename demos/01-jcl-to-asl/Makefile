.PHONY: node-check install install-infra build synth bootstrap deploy run measure destroy leftovers ci clean

# Post 1: ironbridge jcl to-asl on CardDemo. Run every target from this folder.
# Stack name comes from infra/cdk.json context. Region: AWS_REGION, else cdk.json, else us-east-1.
# Example: AWS_PROFILE=sandbox make deploy
REPO := ../..
FIXTURES := $(REPO)/fixtures/carddemo/app
CDK := cd infra && uv run npx cdk
HEADLINE := 97.3% (497/511)

# Warns on Node 20, fails below 20. See scripts/check-node.sh at the repo root.
node-check:
	@sh $(REPO)/scripts/check-node.sh

install: node-check
	uv sync --all-extras
	$(MAKE) install-infra

install-infra:
	cd infra && npm ci

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

# Offline checks for CI: the headline number must not move, the template must synth and pass checkov.
ci:
	$(MAKE) build | tee build.log
	grep -F '$(HEADLINE)' build.log
	$(MAKE) synth
	uv run checkov -f infra/cdk.out/*.template.json --framework cloudformation --quiet --compact

clean:
	rm -rf build infra/cdk.out infra/node_modules outputs.json build.log
