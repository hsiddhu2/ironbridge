# Changelog

## v0.1.2 (Unreleased)
- Failure alerts for demo 01. An EventBridge rule matches FAILED, TIMED_OUT and ABORTED runs of the stack's state machines. A Lambda reads the execution history to find the failed step and publishes one message to a KMS-encrypted SNS topic with the job, failed step, return code or abend, error, cause, owner, runbook, console link and redrive command
- A redrive that succeeds sends no alert. A redrive that fails again sends a new one with the redrive count
- Job owners. `jcl to-asl` writes `build/owners.json` from `NOTIFY=` on each JOB card, with an optional `--owners` file for owner and runbook per job or folder. `NOTIFY=&SYSUID` is the submitter on z/OS, so it gives `unassigned` instead of a guess
- Demo 01 tags each state machine with `ironbridge:job`, `ironbridge:owner`, `ironbridge:owner-source` and `ironbridge:runbook`
- Optional email subscription with `ALERT_EMAIL` at deploy time. No address in the repo
- `make alert-test` checks one alert per failure and none after a successful redrive, through a temporary SQS queue it deletes afterwards
- `make leftovers` also checks SNS topics, SQS queues and EventBridge rules
- Demo 01 runbook at `demos/01-jcl-to-asl/docs/runbook.md`
- No change to coverage. Same numbers: 97.3% of 511 JCL constructs (497/511), 29/29 COND, GDG and PROC, Control-M 72.9% (62/85)

## v0.1.1
- Repo layout for the series: the package stays in `ironbridge/`, each post gets a self-contained folder under `demos/`, and the CardDemo sample moves to `fixtures/carddemo/` to be shared
- Demo 01 (`demos/01-jcl-to-asl`) holds the CDK stack, measurement scripts and docs for `jcl to-asl`
- No change to the converter. Same numbers: 97.3% of 511 JCL constructs (497/511), 29/29 COND, GDG and PROC, Control-M 72.9% (62/85)
- CI runs `make ci`, which checks every demo

## v0.1.0
- `ironbridge jcl to-asl`: converts JCL, PROC and Control-M XML to Step Functions ASL, EventBridge Scheduler definitions and `coverage.json`
- 97.3% of 511 JCL constructs converted with no TODO marker (497/511) on the CardDemo sample
- 29/29 COND, GDG and PROC constructs converted
- Control-M mapping at 72.9% (62/85)
- Generated definitions pass validation: 38/38 jobs and 5/5 parents
- CDK stack for three sample jobs, one Control-M folder parent and a return-code stub Lambda
- `scripts/measure.py` for the measurement
