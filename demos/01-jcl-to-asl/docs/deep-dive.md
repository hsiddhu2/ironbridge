# TRANBKP deep dive: schedules, your own JCL, what broke, production notes

These are the parts of the TRANBKP guide that did not fit in the article. The step numbers match the steps in the guide. Code: [hsiddhu2/ironbridge at v0.1.1, demos/01-jcl-to-asl](https://github.com/hsiddhu2/ironbridge/tree/v0.1.1/demos/01-jcl-to-asl)

## How the converter writes COND

This is the function that writes the Choice expression:

```python
def cond_expr(tests: list[tuple[int, str, str]]) -> str:
    """COND bypasses the step when any test is true. No step name means every prior RC."""
    parts = []
    for code, op, step in tests:
        o = COND_OPS[op]
        if step:
            ref = f"$lookup($rc, {q(step)})"
            parts.append(f"($exists({ref}) and {code} {o} {ref})")
        else:
            parts.append(f"$count([$rc.*][{code} {o} $]) > 0")
    return "{% " + " or ".join(parts) + " %}"
```

A COND with a step name tests only that step. A COND with no step name tests every earlier step. The converter keeps the JCL order of operands, so `(4,LT)` reads as `4 < rc` and never gets flipped by mistake.

## GDG generations become S3 key prefixes

A GDG, or generation data group, is a family of versioned files. `(+1)` means write the next generation. `(0)` means read the current one. The mainframe catalog keeps track of which number is current.

ironbridge carries that number in the execution input as a map called `$gdg`. The `JOB` state seeds it, and a `(+1)` reference becomes a key prefix one generation ahead. This is TRANBKP's backup output:

```json
"DSN": "{% 'gdg/AWS.M2.CARDDEMO.TRANSACT.BKUP/G' & $formatInteger($lookup($gdg, 'AWS.M2.CARDDEMO.TRANSACT.BKUP') + 1, '0000') & 'V00/' %}"
```

COMBTRAN, another CardDemo job, reads the same base with `(0)`, so its prefix has no `+ 1`. The `EOJ` state returns the advanced map as `"gdg": "{% $merge([$gdg, $gdgnew]) %}"`, so a caller that passes it into the next run starts from the new generation. The deployed schedule passes an empty input, so each scheduled run starts again at G0001V00.

These prefixes are shaped for [Amazon S3 keys](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-prefixes.html). In v0.1.1 nothing writes to S3, because every step calls a placeholder. `build/gdg-catalog.json` lists each GDG base with the job and step that define it.

## PROCs are expanded at build time

The PROC call STEP05R became a state named `STEP05R.PRC001`. ironbridge expands PROCs at build time, so the state machine has no PROC concept to resolve at run time. The `PRC001.FILEOUT` override lands there too, which is why the `+ 1` prefix above sits under `STEP05R.PRC001`.

The full state order in `TRANBKP.asl.json` is `JOB`, `STEP05R.PRC001`, `STEP05`, `COND STEP10`, `STEP10`, `EOJ`.

The PROC is no longer one unit on AWS. Change a PROC and you rebuild and redeploy every job that calls it.

## A Control-M folder becomes a parent state machine and a schedule

The INCOND and OUTCOND flags set the order of jobs in a Control-M folder. The converter writes one parent state machine per folder. Each job is a Task that starts the child as a [nested execution with startExecution.sync:2](https://docs.aws.amazon.com/step-functions/latest/dg/connect-stepfunctions.html) and waits for it. This is the TRANBKP entry in `build/folders/DAILY-TransactionBackup.asl.json`, comment trimmed:

```json
"TRANBKP": {
  "Type": "Task",
  "Resource": "arn:aws:states:::states:startExecution.sync:2",
  "Arguments": {
    "StateMachineArn": "${Job_TRANBKP}",
    "Input": {
      "gdg": "{% $gdg %}",
      "simulate": "{% $exists($lookup($sim, 'TRANBKP')) ? $lookup($sim, 'TRANBKP') : {} %}",
      "AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID": "{% $states.context.Execution.Id %}"
    }
  },
  "Assign": {
    "gdg": "{% $merge([$gdg, $states.result.Output.gdg]) %}"
  },
  "Next": "WAITSTEP"
}
```

The parent passes the GDG map down and takes the advanced map back. So a generation written by one job is the current one for the next job in the folder.

The Control-M calendar becomes an [EventBridge Scheduler](https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html) cron schedule. This is `build/schedules/DAILY-TransactionBackup.json`:

```json
{
  "folder": "DAILY-TransactionBackup",
  "expression": "cron(0 0 * * ? *)",
  "timezone": "UTC",
  "state": "DISABLED",
  "comment": "Control-M orders this folder on its DAYS. No TIMEFROM means 00:00."
}
```

The XML has no start time or time zone, so each schedule fires at 00:00 UTC and is deployed DISABLED.

> **OPERATOR NOTE:** Scheduler's [State field](https://docs.aws.amazon.com/scheduler/latest/APIReference/API_CreateSchedule.html) takes `ENABLED` or `DISABLED`. ironbridge writes `DISABLED`. Nothing runs on its own until you choose a real start time and turn it on.

The Control-M schedule converted at a lower rate, 62 of 85 constructs, or 72.9%. All 15 TIMETO windows are TODO because EventBridge Scheduler has no end-of-window setting. Two jobs, MNTTRDB2 and TRANEXTR, have no JCL member in CardDemo and fail visibly.

TIMETO is the end of the time window in which Control-M may start a job. On AWS, a job that becomes ready after that time still starts. Control-M would not start it. A person has to decide what late means for each job.

## The 14 JCL gaps

The 14 gaps are 8 SDSF steps, 1 FTP, 1 DFHCSDUP and 1 IKJEFT1B. Then 1 IEBGENER step and its DD that write to the internal reader. The last one is 1 typo in CardDemo itself, NOTIFY=&SYUID in READCUST.

None of them is a COND, GDG or PROC construct. Those are 29 of 29. Most are mainframe utilities with no direct AWS equivalent. ironbridge leaves a TODO rather than guess.

A few words first. CICS is the mainframe's online system, the part that serves screens to users. TSO is its interactive command shell, and REXX is a scripting language that runs under it.

| Gap | Count | What it does on the mainframe | Why it stays a TODO |
| --- | --- | --- | --- |
| SDSF | 8 | Sends commands to the CICS region, the online system, to close or open VSAM files before and after a reload | It acts on the online CICS region. No AWS mapping in v0.1.1 |
| Internal reader | 2 | A step submits another job | It maps to starting another state machine. ironbridge v0.1.1 does not do that |
| FTP | 1 | Sends a file to another host | Where the file goes is a decision for a person, not a translation |
| IKJEFT1B | 1 | Runs a REXX exec under batch TSO. In CardDemo it turns the statement file into a PDF | REXX is not converted in v0.1.1 |
| DFHCSDUP | 1 | Defines CICS online resources | That is online CICS, not batch |
| Typo | 1 | NOTIFY=&SYUID instead of &SYSUID | The source is wrong. ironbridge flags it instead of fixing it |

These 14 are the ones a person has to decide.

The denominator is 511, from the parser and an independent grep of the pinned CardDemo commit 59cc6c2. The research count of 509 missed two GDG (+1) references.

Reported, not counted: 1 RESTART= comment and 16 IDCAMS IF LASTCC or MAXCC tests. The two `IF MAXCC` lines in TRANBKP's STEP05 are among them. They live inside the IDCAMS program input, so they belong to the program, not the job flow.

Every TODO lands in `build/coverage.json` with its job, file, line and reason. This is one from CardDemo:

```json
{
  "row": "EXEC steps",
  "job": "CARDFILE",
  "file": "CARDFILE.jcl",
  "line": 22,
  "state": "CLCIFIL",
  "reason": "PGM=SDSF sends console commands to CICS. No AWS mapping in v0.1.0"
}
```

## Setup notes for steps 1 and 2

Before you deploy, check which account `aws sts get-caller-identity` prints. It should be your sandbox account.

One [AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/home.html) stack holds all of it. `make deploy` creates it, `make destroy` removes it, and `make leftovers` checks that nothing is left.

> **OPERATOR NOTE:** The CDK CLI wants Node 22 or 24. Node 20 still runs, with a warning. You also need a CDK bootstrap in the account and region you deploy to. The bootstrap bucket stays after destroy.

The full `make build` output from my run on 2026-11-11:

```text
$ make build
uv run ironbridge jcl to-asl --jcl ../../fixtures/carddemo/app/jcl --proc ../../fixtures/carddemo/app/proc \
	  --controlm ../../fixtures/carddemo/app/scheduler/CardDemo.controlm --out build
construct                 total   done   todo
EXEC steps                  101     89     12
Step COND=                    9      9      0
GDG relative refs            17     17      0
GDG base definitions         11     11      0
EXEC PROC calls               3      3      0
JCLLIB                        3      3      0
DD statements               328    327      1
Symbolic lines               39     38      1
JCL coverage 97.3% (497/511)
Hard subset COND, GDG, PROC 100.0% (29/29)
Control-M coverage 72.9% (62/85)
TODO markers 37. Wrote build/
```

The validation output:

```text
jobs OK 38/38
parents OK 5/5
```

Near the end of the deploy you should see these lines:

```text
ironbridge-m4: creating CloudFormation changeset...
ironbridge-m4: deploying... [1/1]
Outputs:
ironbridge-m4.JobTRANBKPArn = arn:aws:states:us-east-1:<account-id>:stateMachine:ironbridge-m4-TRANBKP
ironbridge-m4.ParentArn = arn:aws:states:us-east-1:<account-id>:stateMachine:ironbridge-m4-DAILY-TransactionBackup
ironbridge-m4.ScheduleName = ironbridge-m4-DAILY-TransactionBackup
ironbridge-m4.StubFunctionName = ironbridge-m4-rc-stub
```

The parent calls CLOSEFIL, WAITSTEP and OPENFIL, so those are deployed too.

## The stub, for step 5

The stub is where an abend comes from. It reads the step name and either returns a code or raises an error. Logging lines are trimmed:

```python
class Abend(Exception):
    """A z/OS abend. Step Functions sees errorType "Abend" and the execution fails."""


def handler(event: dict, context) -> dict:
    job, step = str(event.get("job", "")), str(event.get("step", ""))
    key = f"{job}.{step}"
    abends = {s.strip() for s in os.environ.get("ABEND_STEPS", "").split(",") if s.strip()}
    if key in abends:
        raise Abend(f"S0C7 simulated in {key}")
    simulate = event.get("simulate") or {}
    rc = int(simulate.get(step, 0)) if isinstance(simulate, dict) else 0
    return {"job": job, "step": step, "rc": rc}
```

## The redrive history with step names, for step 6

Step 6 of the guide prints this history with None as the step on most lines. Read it from the top. The backup step entered and succeeded. STEP05 entered and failed, and the execution failed with it. Then comes `ExecutionRedriven`.

Here is the same history with the step names filled in.

| Event in the history | Step |
| --- | --- |
| TaskStateEntered | STEP05R.PRC001 |
| TaskSucceeded | STEP05R.PRC001 |
| TaskStateEntered | STEP05 |
| TaskFailed | STEP05 |
| ExecutionFailed | the job |
| ExecutionRedriven | the job |
| TaskSucceeded | STEP05, with no new TaskStateEntered |
| TaskStateEntered | STEP10 |
| TaskSucceeded | STEP10 |

The Event column is what get-execution-history returned for my run on 2026-11-11. The CLI printed None as the step on every line except TaskStateEntered, so I filled in the Step column. STEP05 on the failed and redriven Task comes from the Step Functions console event list of an earlier run of the same job, on 2026-11-01. The other names come from the TaskStateEntered line before each event. Only STEP05 ran again. The backup step before it did not.

## Step 7 in full: the schedule and the parent run

The schedule from the Control-M folder was deployed in step 2, with the `ParentArn` and `ScheduleName` lines in its output. Open EventBridge Scheduler in the console to see it. To watch the parent run once, `make run` starts it and prints the jobs it ran.

> **ABEND:** At first, `AWS_REGION=eu-west-1 make run` quietly ran in us-east-1, because `outputs.json` pointed at the us-east-1 stack. The scripts now stop on a region mismatch. Deploy, run and measure in one region.

The console shows the cron expression ironbridge wrote, `cron(0 0 * * ? *)`, with the next trigger dates.

> **CHECK:** the schedule `ironbridge-m4-DAILY-TransactionBackup` shows status Disabled and time zone UTC. If you ran `make run`, the parent ran CLOSEFIL, TRANBKP, WAITSTEP and OPENFIL and SUCCEEDED.

## Run it on your own JCL

The converter does not care that the input is CardDemo. Point it at your own folders from the repo:

```bash
uv run ironbridge jcl to-asl --jcl path/to/your/jcl --proc path/to/your/proc --out build-mine
jq '.jcl.pct, .jcl.hard_subset.pct' build-mine/coverage.json
jq -r '.jcl.todos[] | "\(.job) \(.state): \(.reason)"' build-mine/coverage.json
```

`--controlm` adds a Control-M XML export, and `--timezone` sets the IANA zone for Scheduler. I ran it on a small job outside CardDemo on 2026-11-11, with COND and GDG references. It built and wrote its own `coverage.json`. Outside CardDemo I ran that one small job, and that is all.

> **CHECK:** `build-mine/coverage.json` exists. The first `jq` prints two rates, all JCL and the COND, GDG and PROC subset. The second prints one line per TODO.

## The baseline diff

Before writing the emitter, I wrote ASL by hand for TRANBKP, DEFGDGD and TRANREPT and committed it alone. The generated state machines match those three on state types and transitions, 100%. I wrote both, so this shows the tool reproduces my own reading of the JCL. It is not an independent check.

## What broke, in full

Five of these rows went wrong for me while building or testing this. The LOG_LEVEL, Catch and AccessDenied rows come from the docs and the design. I did not hit them.

These five I saw in my own runs:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `make measure` showed `path_match=false` and an empty `steps_rerun` on every abend row, even though the history showed one step rerun | Redrive does not emit a second `TaskStateEntered` for the failed step. It schedules the Task again. My harness counted `TaskStateEntered` events, so it saw a short path | The harness in `scripts/measure.py` now walks each `TaskScheduled` back to its `TaskStateEntered`. A Task scheduled again after `ExecutionRedriven` counts as a rerun |
| Redrive ran, and the execution failed again at the same step | The abend was still set on the stub | Clear `ABEND_STEPS`, wait with `function-updated-v2`, then redrive again. The second redrive succeeded |
| `ExecutionNotRedrivable: Execution is SUCCEEDED and cannot be redriven` | I redrove an execution that already succeeded | Start a new execution instead |
| At first, `AWS_REGION=eu-west-1 make run` quietly ran in us-east-1. Now it stops with `error: the stack in outputs.json is in us-east-1 but the region is eu-west-1. Set AWS_REGION=us-east-1 or redeploy.` | `outputs.json` points at a different region from `AWS_REGION` | The scripts now check the region and stop on a mismatch. Deploy, run and measure in one region. The default is us-east-1 |
| A Python traceback ending in `ProfileNotFound`. My SSO session also expired once, with `Token has expired and refresh failed` | Missing profile or expired SSO session | The scripts now print one line instead of a traceback, such as `error: AWS profile 'doesnotexist' not found. Check AWS_PROFILE and ~/.aws/config.` Fix the profile or run `aws sso login` |

These three come from the docs and the design, not from my runs:

| Symptom | Cause | Prevention |
| --- | --- | --- |
| The stub loses `LOG_LEVEL` after you set `ABEND_STEPS` | `update-function-configuration` replaces the whole [environment](https://docs.aws.amazon.com/lambda/latest/dg/configuration-envvars.html), not one key | Pass every variable each time, as the commands in step 5 do |
| Redrive cannot restart the step that broke | A `Catch` handled the step error. The run ends on the catch path, and if that path ends in a Fail state, [redrive enters it and fails again](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html) | Leave step errors uncaught. The emitter writes no Catch and no Fail on step errors |
| `AccessDenied` on validation | `ValidateStateMachineDefinition` has [no resource type](https://docs.aws.amazon.com/service-authorization/latest/reference/list_stepfunctions.html), so a scoped ARN never matches | Allow it on `*`. The operator policy does this |

One more surprised me. I stopped the parent mid-run and redrove the aborted execution. Step Functions accepted it and it succeeded. The docs list aborted runs as redrivable, so this is the service's behavior, not something ironbridge adds.

## Failure alerts and job owners, added in v0.1.2

A reader asked: will you add automatic alerts and job ownership before using this for real batch work? Yes. Since v0.1.2 a failed job sends one message to an Amazon SNS topic. It names the job, the failed step, the error, the owner and the runbook.

Tested on AWS on 2026-11-21 in us-east-1. `make alert-test` passed 11 of 11 checks. An abend in TRANBKP STEP05 sent one alert naming STEP05 and its owner. A redrive that failed again sent one new alert. A redrive that succeeded sent none. The dead-letter queue stayed empty. The alert reached a test SQS queue 0.88 s after the run stopped, one sample with the Lambda warm. The first cold call of the Lambda took 6.36 s.

### How it works

1. Step Functions sends an Execution Status Change event to the default EventBridge bus for every Standard workflow run. No setup is needed. [EventBridge integration](https://docs.aws.amazon.com/step-functions/latest/dg/eventbridge-integration.html)
2. The rule `ironbridge-m4-job-failed` matches `FAILED`, `TIMED_OUT` and `ABORTED` for this stack's seven state machines only.
3. It calls the Lambda `ironbridge-m4-job-alert`. The Lambda reads the execution history to find the failed step, and the state machine tags to find the owner.
4. The Lambda publishes to the topic `ironbridge-m4-job-alerts`.

Why a Lambda and not an input transformer straight to SNS? The event detail has `executionArn`, `stateMachineArn`, `name`, `status`, `startDate`, `stopDate`, `input`, `output`, `redriveCount`, `redriveDate`, `redriveStatus`, `redriveStatusReason`, `error` and `cause`. It has no field for the state that failed. That only lives in the execution history, from [GetExecutionHistory](https://docs.aws.amazon.com/step-functions/latest/apireference/API_GetExecutionHistory.html).

The Lambda walks the history the same way the measure harness does. A redriven step gets no new `TaskStateEntered`, so it follows `previousEventId` back to the state that owns the failure. Only events after the last `ExecutionRedriven` count, so a run that fails again after a redrive reports its new failure. An aborted or timed out run reports the state that was entered and never exited.

If the history or tag read fails, the alert still goes out with "unknown" in that field. Only a failed publish makes the Lambda fail. Then Lambda retries it twice, and after that the event lands in the dead-letter queue `ironbridge-m4-alerts-dlq`. A dead-letter queue, or DLQ, is a queue that holds events that could not be delivered. EventBridge uses the same queue when it cannot call the Lambda at all. It retries for up to two hours first. [EventBridge DLQ](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-rule-dlq.html), [Lambda async DLQ](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-retain-records.html)

### A sample alert

This is the alert from the live run, with the account id and execution id masked. The unit tests in `ironbridge/jcl/tests/test_alerts.py` check the same lines against hand-built histories in `ironbridge/jcl/tests/fixtures/alerts/`. The subject is `ironbridge FAILED: TRANBKP at STEP05`.

```text
ironbridge job alert: TRANBKP FAILED

Job:            TRANBKP
Failed step:    STEP05
Return code:    none, the step abended with S0C7
Error:          Abend
Cause:          S0C7 simulated in TRANBKP.STEP05
Owner:          carddemo-batch-oncall (from owners.json)
Runbook:        https://github.com/hsiddhu2/ironbridge/blob/main/demos/01-jcl-to-asl/docs/runbook.md
Execution:      arn:aws:states:us-east-1:<account-id>:execution:ironbridge-m4-TRANBKP:<execution-id>
Redrive count:  0
Console:        https://us-east-1.console.aws.amazon.com/states/home?region=us-east-1#/v2/executions/details/arn:aws:states:us-east-1:<account-id>:execution:ironbridge-m4-TRANBKP:<execution-id>

Redrive: fix the cause first, then run
  aws stepfunctions redrive-execution --execution-arn arn:aws:states:us-east-1:<account-id>:execution:ironbridge-m4-TRANBKP:<execution-id>
Steps that already succeeded do not run again. The failed step runs again.
```

An abend has no return code. On z/OS a step either ends with a return code, which COND reads, or it abends with a code like S0C7. The alert says which. The message also carries the attributes `job`, `step`, `owner` and `status`. An SNS [subscription filter policy](https://docs.aws.amazon.com/sns/latest/dg/sns-message-filtering.html) on `owner` can send each team only its own jobs. The demo does not set one.

### Where the owner comes from

On the mainframe, `NOTIFY=` on the JOB card names the user who gets a message when the job ends. Every CardDemo job says `NOTIFY=&SYSUID`, except READCUST, which has the typo `NOTIFY=&SYUID`. `&SYSUID` is a system symbol. z/OS replaces it with the user id of whoever submitted the job. So the source has no fixed owner. On AWS the submitter is EventBridge Scheduler or a person starting a run, and that is not an owner either.

ironbridge does not guess. `make build` writes `build/owners.json` with one entry per job and folder. The owner is picked in this order:

| Source | Owner | `ironbridge:owner-source` tag |
| --- | --- | --- |
| The job's entry in `owners.json` | That value | `owners-file` |
| `NOTIFY=` with a fixed user id, such as `NOTIFY=OPSUSR1` | That user id | `notify` |
| The `default` entry in `owners.json` | That value | `owners-file-default` |
| Anything else, including `NOTIFY=&SYSUID` | `unassigned` | `none` |

The runbook comes from the job's entry, then the `default` entry. The demo's `owners.json` gives TRANBKP, DEFGDGD, TRANREPT and the DAILY parent an owner, and every job the demo runbook. CLOSEFIL, WAITSTEP and OPENFIL keep `unassigned`, so you can see the fallback.

```json
{
  "default": {"runbook": "https://github.com/hsiddhu2/ironbridge/blob/main/demos/01-jcl-to-asl/docs/runbook.md"},
  "jobs": {
    "TRANBKP": {"owner": "carddemo-batch-oncall"}
  }
}
```

The stack puts these on each state machine as the tags `ironbridge:job`, `ironbridge:owner`, `ironbridge:owner-source` and `ironbridge:runbook`. You see them in the console under Tags. Tag values may only hold letters, digits, spaces and `_ . : / = + - @`, up to 256 characters. [Tag restrictions](https://docs.aws.amazon.com/step-functions/latest/dg/service-quotas.html#sfn-limits-tagging). So a runbook URL cannot carry `?`, `#` or `&`. `make build` stops with one error line if a value breaks that rule, or if `owners.json` names a job the build does not have.

For your own JCL, pass your own file: `ironbridge jcl to-asl ... --owners my-owners.json`. Change an owner, then `make deploy`. Only the tags change.

### How to subscribe

No address is kept in the repo. Pass one at deploy time:

```bash
ALERT_EMAIL=you@example.com make deploy
```

SNS sends a confirmation mail first. Nothing arrives until you confirm. You can also subscribe a chat tool, a pager or a queue to `AlertTopicArn` from `outputs.json`.

### Redrive and repeat alerts

The rule never matches `SUCCEEDED`, `RUNNING` or `PENDING_REDRIVE`. So:

- A run fails. One alert, redrive count 0.
- You redrive it and it fails again. A new alert, redrive count 1.
- You fix it and redrive. It succeeds. No alert.

`make alert-test` checks all three on TRANBKP. It also checks that the DLQ is empty.

Two cases still give more than one message:

- A job inside the DAILY parent fails. The job alerts and the parent alerts. The parent's message names the job as its failed step and links the child run.
- Lambda can run the same event more than once. [Lambda async error handling](https://docs.aws.amazon.com/lambda/latest/dg/invocation-async-error-handling.html). The redrive count and execution ARN in the message tell repeats apart.

Step Functions sends its events on a best-effort basis, and they may arrive out of order. [EventBridge integration](https://docs.aws.amazon.com/step-functions/latest/dg/eventbridge-integration.html). Best effort means that in rare cases an event is never delivered. [Delivery level](https://docs.aws.amazon.com/eventbridge/latest/ref/event-delivery-level.html). Then no alert goes out and the DLQ stays empty. For batch work that must not be missed, also list failed runs on a schedule, as in step 5 of the runbook.

### Security

- The topic is encrypted with the AWS managed key `alias/aws/sns`. That works because the publisher is the Lambda role. If EventBridge published straight to the topic, it would need a customer managed key with a grant for `events.amazonaws.com`. [SNS key management](https://docs.aws.amazon.com/sns/latest/dg/sns-key-management.html). The topic policy denies any request without TLS.
- The Lambda role can read history only for this stack's executions, read tags only on this stack's state machines, publish only to this topic, and send only to its DLQ. CDK assertion tests in `infra/tests/test_stack.py` check each scope.
- The DLQ uses SQS managed encryption. checkov 3.4.0 only looks for a KMS key on queues, so `CKV_AWS_27` is skipped with that reason inline. The AWS managed SQS key cannot grant EventBridge, which writes to this queue.
- Reserved concurrency is off by default. Accounts with a concurrency limit of 10 reject any reservation. Turn it on with `-c alertReservedConcurrency=2` where your account allows it.

### Cost

Expect cents. Each alert is one Step Functions event on the default bus, one Lambda call at 128 MB, two Step Functions read calls and one SNS publish. `make alert-test` adds one temporary SQS queue and a few hundred receive calls. No customer managed KMS key is created. [EventBridge pricing](https://aws.amazon.com/eventbridge/pricing/), [SNS pricing](https://aws.amazon.com/sns/pricing/), [Lambda pricing](https://aws.amazon.com/lambda/pricing/). The full QA run on 2026-11-21, deploy, alert test, measure and destroy, cost under $0.02, estimated from usage.

### Run the alert test

After `make deploy`:

```bash
make alert-test
```

It subscribes a temporary queue to the topic, abends TRANBKP STEP05, redrives once with the abend still set, then clears it and redrives again. It prints the first alert and 11 checks, and appends them to `results-alerts.csv`. It deletes the queue and clears the abend even when a check fails. `make leftovers` also lists any SNS topic, SQS queue or EventBridge rule named after the stack.

## Production notes

**Permissions.** The stack creates an operator policy in `infra/stack.py`. Attach it to whoever runs the scripts. Validate is on `*` because it has no resource type. Start is scoped to this stack's state machines. Redrive is scoped to this stack's executions. The Lambda actions are scoped to the stub. The job and parent roles also get CloudWatch Logs delivery actions on `*`, which the [Step Functions logging docs](https://docs.aws.amazon.com/step-functions/latest/dg/cw-logs.html) require.

**Audit trail.** Every execution sends its events to CloudWatch Logs. The stub writes one JSON line per step with the job, step, program, return code and request id. The execution history keeps the failure and the `ExecutionRedriven` event together, so the abend and the rerun stay in one record.

**Cost.** A full deploy, measure and destroy cycle costs under $1. Mine was a few hundred state transitions and under 300 Lambda calls. [Redrives count as state transitions](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html) too.

**What was measured.** I measured TRANBKP, DEFGDGD and TRANREPT. The DAILY parent also ran CLOSEFIL, TRANBKP, WAITSTEP and OPENFIL. The other jobs passed validation, but I did not run them on AWS.
