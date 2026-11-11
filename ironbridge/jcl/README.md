# ironbridge jcl

`ironbridge jcl to-asl` turns JCL members, PROCs and a Control-M XML export into AWS Step Functions state machines, EventBridge Scheduler definitions and a coverage report. It runs offline. No AWS call is made. To deploy and measure the output on AWS, see [demos/01-jcl-to-asl](../../demos/01-jcl-to-asl/README.md).

```bash
ironbridge jcl to-asl \
  --jcl fixtures/carddemo/app/jcl \
  --proc fixtures/carddemo/app/proc \
  --controlm fixtures/carddemo/app/scheduler/CardDemo.controlm \
  --out build
```

## Output

| Path | What it is |
| --- | --- |
| `build/jobs/<MEMBER>.asl.json` | One Standard state machine per JCL member, JSONata |
| `build/folders/<FOLDER>.asl.json` | One parent per Control-M folder. Runs jobs with `startExecution.sync:2` |
| `build/schedules/<FOLDER>.json` | Scheduler cron, IANA zone, state DISABLED |
| `build/gdg-catalog.json` | GDG bases with LIMIT, SCRATCH and the defining step |
| `build/coverage.json` | Every construct as translated or TODO, per row, with the hard subset |

Two runs on the same input give byte-identical output.

## Mapping

| JCL or Control-M | Step Functions |
| --- | --- |
| JOB card | `JOB` Pass state. Sets `$rc`, `$gdg`, `$gdgnew`, `$sim` |
| EXEC PGM= | Task to the RC stub with `pgm`, `parm` and a `dd` map |
| IEFBR14 | Pass with RC 0 |
| SDSF, FTP, DFHCSDUP, IKJEFT1B, INTRDR | Task to the stub plus a TODO marker |
| COND=(code,op[,step]) | Choice `COND <step>` before the step. True bypasses it |
| COND EVEN or ONLY | TODO. Redrive has no equivalent of running after an abend |
| GDG (0), (+1), (-n) | `gdg/<base>/GnnnnV00/` key prefix from the `gdg` input map |
| DEFINE GENERATIONDATAGROUP | `gdg-catalog.json` |
| EXEC PROC= and JCLLIB | Expanded at emit time from `--proc`, with DD overrides and symbols |
| SET and &NAME | Substituted. `&SYSUID` maps to the execution role ARN |
| RESTART= | Every step is a redrive point. No Catch, no Fail |
| FOLDER and SMART_FOLDER | One parent state machine |
| INCOND and OUTCOND in a folder | Task order. Independent jobs share a Parallel |
| Job with no member | Fail state, error `ironbridge.UnresolvedJob` |
| DAYS and month flags | Scheduler cron. TIMETO is a TODO |

## Runtime contract

Start a job with `{"gdg": {"<base>": n}, "simulate": {"<step>": rc}}`. Both keys are optional. The job returns `{"job", "rc", "maxcc", "gdg"}` where `gdg` is advanced for every generation the job created. A parent takes `{"gdg": {...}, "simulate": {"<MEMBER>": {...}}}`.

An abend is a Lambda error. It is not caught, so the execution fails and `RedriveExecution` reruns that step only.

## TODO markers

Every construct ironbridge cannot translate is written once into the output as `TODO(ironbridge): <row> at <file>:<line>. <reason>.` The count in `coverage.json` equals `grep -o 'TODO(ironbridge)' -r build | wc -l`.

## Tests and fixtures

`fixtures/carddemo/` at the repo root holds the CardDemo JCL, PROC and Control-M input at commit 59cc6c2, with its Apache 2.0 LICENSE and NOTICE. Every demo shares it. `tests/baseline/` holds the three hand-written ASL files used for the structural diff. They were committed before any generator code.

```bash
uv run pytest -q ironbridge
```
