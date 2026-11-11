# Demo 01 measurement plan

## Primary metric
Percent of JCL construct instances emitted with no `TODO(ironbridge)` marker. One decimal.

Denominator from research.md, CardDemo commit 59cc6c2.

| Row | Count |
| --- | --- |
| EXEC steps | 101 |
| Step COND= | 9 |
| GDG relative refs | 17 (corrected 2026-11-01 from 15: grep and parser both find 15 (+1) and 2 (0)) |
| GDG base definitions | 11 |
| EXEC PROC calls | 3 |
| JCLLIB | 3 |
| DD statements | 328 |
| Symbolic lines | 39 |
| **Total** | **511** |

RESTART= is one comment and IDCAMS IF is program logic. Both are reported, not counted. Publish the per-row table and the hard subset (COND, GDG refs, PROC) so DD volume cannot hide gaps. Control-M coverage is a separate line.

## Secondary and tertiary
- All 38 job files return `result: OK` from ValidateStateMachineDefinition at `--severity WARNING`. Record warnings.
- Structural diff against hand-written ASL for TRANBKP, DEFGDGD, TRANREPT. Compare state type multisets and transition edges. Report edge match percent.

## Baseline honesty
The engineer writes the baselines by hand from the JCL into `ironbridge/jcl/tests/baseline/`. They are committed alone, before the first emitter commit. qa checks `git log` order and records each SHA-256.

## Method
1. `pip install -e .` on Python 3.12.
2. Offline: `ironbridge jcl to-asl --jcl ../../fixtures/carddemo/app/jcl --proc ../../fixtures/carddemo/app/proc --controlm ../../fixtures/carddemo/app/scheduler/CardDemo.controlm --out build/`.
3. Run it twice. `build/` must be byte-identical.
4. The `coverage.json` TODO count must equal a grep of `build/` for `TODO(ironbridge)`.
5. Parser counts must equal the denominator. Record any delta with a reason.
6. From `demos/01-jcl-to-asl`: `AWS_PROFILE=sandbox uv run python scripts/measure.py validate`, then `measure.py diff`.
7. `cdk deploy`. `measure.py run` executes each sample job three times.
   - Run 1. All RCs 0. Every step runs.
   - Run 2. RCs that trip COND. Bypass path matches the baseline.
   - Run 3. `ABEND_STEPS` on one middle step. Execution fails. Clear it. Redrive.
8. Compare each GetExecutionHistory state sequence to the expected path. After redrive, exactly one step ran twice.
9. `cdk destroy`. Confirm no stack, state machine, function, schedule or log group remains.

Sample is 9 executions and 3 redrives. Coverage is deterministic, so step 3 replaces repetition.

## Invalid if
- Region is not us-east-1 or the commit differs.
- A baseline changed after the first emitter commit.
- A deploy happened between failure and redrive.
- Throttling appears in history or on validate.
- Redrive reran a succeeded step.

## results.csv
`run_id, utc_time, phase, job, construct, total, translated, todo, pct, validate_result, warnings, scenario, execution_arn, path_match, redrive_count, steps_rerun, duration_ms, carddemo_commit, ironbridge_version, region`

`phase` is coverage, validate, diff or run. Unused columns stay empty.
