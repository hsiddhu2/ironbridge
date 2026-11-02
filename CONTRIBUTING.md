# Contributing

Thanks for looking. This repo backs a blog post, so the measurement must keep working.

1. Fork and branch from `main`.
2. `make install lint test` must pass.
3. If you touch the measured path, run `make deploy measure destroy` and paste the new `results.csv` summary in the PR.
4. No secrets, account ids or employer references.
