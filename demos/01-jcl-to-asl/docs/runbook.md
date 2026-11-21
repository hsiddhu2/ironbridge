# Runbook: a demo 01 job failed

You got an alert from the `ironbridge-m4-job-alerts` topic. It names the job, the step that failed and the owner. This page is the runbook every demo 01 job points to through its `ironbridge:runbook` tag. For your own jobs, point the tag at your own runbook in `owners.json`.

A few words first. A job is one JCL member, deployed as one Step Functions state machine. A step is one state in it, one program run on the mainframe. An abend is the mainframe word for a step that crashed, with a code such as S0C7 for bad numeric data. Redrive is the Step Functions way to restart a failed run from the step that failed, like RESTART= on z/OS.

## 1. Read the alert

- **Failed step** is the state that failed. In demo 01 every step calls the return-code stub, so an abend here was injected through `ABEND_STEPS`.
- **Return code** is "none" for an abend. A step that ends with a return code does not fail the job. COND decides what runs next.
- **Cause** is the error message the step raised.
- **Console** opens the execution. The graph shows the failed state in red.

## 2. Fix the cause

In demo 01 the only cause is the injected abend. Clear it:

```bash
FN=$(python3 -c 'import json; print(json.load(open("outputs.json"))["ironbridge-m4"]["StubFunctionName"])')
aws lambda update-function-configuration --function-name "$FN" \
  --environment 'Variables={ABEND_STEPS=,LOG_LEVEL=INFO}'
aws lambda wait function-updated-v2 --function-name "$FN"
```

For a real job, fix the data or the program before you go on. Redrive runs the same definition with the same input.

## 3. Redrive

Copy the command from the alert:

```bash
aws stepfunctions redrive-execution --execution-arn <execution-arn-from-the-alert>
```

Steps that already succeeded do not run again. The failed step runs again, then the rest of the job. A redrive that succeeds sends no alert. A redrive that fails again sends a new alert with a higher redrive count.

Redrive works for 14 days after the run stopped. After that, start a new execution.

## 4. If the parent failed too

A job that runs inside the DAILY-TransactionBackup parent fails the parent as well. You get two alerts: one for the job and one for the parent. The parent alert names the job as its failed step and links the child run. Fix the cause, then redrive the parent. The [redrive docs](https://docs.aws.amazon.com/step-functions/latest/dg/redrive-executions.html) say a redriven Task is scheduled and started again. For the parent, that should start the job as a new run, and the failed child run stays FAILED. The 2026-11-21 test did not redrive a parent, so check the new child run in the console.

## 5. If no alert came

Check the alert dead-letter queue, `AlertDlqUrl` in `outputs.json`. A message there means EventBridge could not call the alert Lambda or the Lambda failed three times. The Lambda logs are in the log group whose name starts with `ironbridge-m4-AlertLogs`.

An empty DLQ does not prove every failure sent an alert. Step Functions sends its events on a best-effort basis, so in rare cases one never arrives. To be sure, list the failed runs of a job:

```bash
aws stepfunctions list-executions --state-machine-arn <state-machine-arn> --status-filter FAILED
```
