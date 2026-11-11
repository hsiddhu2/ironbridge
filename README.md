# ironbridge

[![CI](https://github.com/hsiddhu2/ironbridge/actions/workflows/ci.yml/badge.svg)](https://github.com/hsiddhu2/ironbridge/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-blue)

Mainframe artifacts in, AWS targets out. One Python CLI, tested end to end on [AWS CardDemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo). No mainframe needed.

The first command, `ironbridge jcl to-asl`, turned CardDemo's 38 JCL jobs into AWS Step Functions. 497 of 511 JCL constructs (97.3%) came out with no TODO marker.

## The series

Each post on AWS Builder Center adds one command, one measured result and one release. Every post links to its release tag, so the code you read there never changes under you.

| # | Command | Mainframe in | AWS out | Result | Release | Demo |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `jcl to-asl` | JCL, PROC, Control-M | Step Functions, EventBridge Scheduler | 97.3% of 511 JCL constructs converted | [v0.1.1](https://github.com/hsiddhu2/ironbridge/releases/tag/v0.1.1) | [demos/01-jcl-to-asl](demos/01-jcl-to-asl) |
| 2 | `vsam to-dynamodb` | VSAM, copybooks | DynamoDB | Planned | | |
| 3 | `cobol run-lambda` | COBOL batch | Lambda | Planned | | |
| 4 | `ebcdic decode` | EBCDIC files | Iceberg tables | Planned | | |
| 5 | `cics to-mcp` | CICS transaction | AgentCore Gateway tool | Planned | | |
| 6 | `transform score` | AWS Transform output | Precision and recall | Planned | | |
| 7 | Capstone | All of CardDemo | The full AWS bill | Planned | | |

## Quick start

Offline. No AWS account needed.

```bash
git clone https://github.com/hsiddhu2/ironbridge.git
cd ironbridge
make install
make test
cd demos/01-jcl-to-asl && make build
```

`make build` prints the coverage table and writes the Step Functions definitions to `build/`. To deploy, run and measure on AWS, follow the [demo 01 README](demos/01-jcl-to-asl/README.md).

## Repo layout

```
ironbridge/            the package that pip installs, one folder per command
  core/                shared helpers
  jcl/                 jcl to-asl: code, tests, hand-written baselines
demos/                 one self-contained folder per post
  01-jcl-to-asl/       README, CDK stack, measurement scripts, docs
fixtures/carddemo/     the CardDemo sample, shared by every demo
scripts/               repo-wide helpers
```

A new command adds `ironbridge/<command>/` and `demos/<nn>-<name>/`. Nothing from an earlier post moves. Each demo deploys its own stack, so two demos never collide in one AWS account.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node 22 or 24 for the CDK CLI, only for demos that deploy
- An AWS sandbox account with a Budget alarm, only for demos that deploy

## License

Apache 2.0. See `LICENSE`. The CardDemo sample under `fixtures/carddemo/` comes from [aws-samples/aws-mainframe-modernization-carddemo](https://github.com/aws-samples/aws-mainframe-modernization-carddemo) at commit 59cc6c2, Apache 2.0, with its own `LICENSE` and `NOTICE`.
