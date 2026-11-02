# Changelog

## v0.1.0
- `ironbridge jcl to-asl`: converts JCL, PROC and Control-M XML to Step Functions ASL, EventBridge Scheduler definitions and `coverage.json`
- 97.3% of 511 JCL constructs converted with no TODO marker (497/511) on the CardDemo sample
- 29/29 COND, GDG and PROC constructs converted
- Control-M mapping at 72.9% (62/85)
- Generated definitions pass validation: 38/38 jobs and 5/5 parents
- CDK stack for three sample jobs, one Control-M folder parent and a return-code stub Lambda
- `scripts/measure.py` for the measurement
