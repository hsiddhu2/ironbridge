# Alert test histories

Hand-built `GetExecutionHistory` responses for the alert Lambda's unit tests. They follow the
event shapes in the [HistoryEvent API reference](https://docs.aws.amazon.com/step-functions/latest/apireference/API_HistoryEvent.html)
and the TRANBKP and DAILY-TransactionBackup state names from `make build`. They are not
captured from a live run. The account id is the documentation example 111122223333.

| File | What happened |
| --- | --- |
| `tranbkp-abend.json` | STEP05 abends with S0C7 and the execution fails |
| `tranbkp-redrive-failed-again.json` | The same run is redriven while the abend is still set and fails again |
| `tranbkp-redrive-succeeded.json` | Then redriven once more after the fix and succeeds |
| `tranbkp-aborted.json` | Stopped while STEP05 runs |
| `tranbkp-timed-out.json` | The execution times out while STEP05 runs |
| `daily-parent-child-failed.json` | The DAILY parent fails because the TRANBKP child job failed |
