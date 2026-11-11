# Hand-written baselines

These three files were written by hand from the CardDemo JCL alone, before any generator code existed. They are the reference for the structural diff in `demos/01-jcl-to-asl/scripts/measure.py diff`.

| File | Source JCL | Why it was picked |
| --- | --- | --- |
| `TRANBKP.asl.json` | `TRANBKP.jcl` | COND=(4,LT), a PROC call and a GDG (+1) |
| `DEFGDGD.asl.json` | `DEFGDGD.jcl` | Four COND=(0,NE) steps, three GDG bases and the RESTART= comment |
| `TRANREPT.asl.json` | `TRANREPT.jcl` | PROC through JCLLIB, five GDG (+1) refs and a duplicate step name |

Conventions used by hand:

- `JOB` is a Pass state that sets `$rc`, `$gdg`, `$gdgnew` and `$sim` from the input.
- Each EXEC is a Task named after the step. A PROC step is `<step>.<procstep>`.
- Each COND= is a Choice named `COND <step>` that jumps past the step when the test is true.
- `EOJ` returns the RCs, MAXCC and the advanced GDG map.
- No Catch and no Fail. An abend leaves the execution failed so it can be redriven.

Do not edit these files. The measurement is invalid if they change after the first emitter commit.
