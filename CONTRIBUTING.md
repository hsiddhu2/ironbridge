# Contributing

Thanks for looking. This repo backs a blog post, so the measurement must keep working.

## Run the checks

```
make install
make lint test
make build
make synth
```

None of these need AWS credentials. CI runs the same steps on Node 22, plus checkov on the synthesized template.

## Propose a change

1. Open an issue first for anything bigger than a fix.
2. Fork and branch from `main`.
3. Use conventional commit messages, for example `fix: handle empty DD name`.
4. Open a PR and fill in the template.

## The measurement must still pass

`make build` must still print the coverage line in the README. CI fails if it changes. If you touch the measured path, run `make deploy measure destroy` in a sandbox account and paste the summary in the PR. Do not paste `results.csv`, the execution ARNs carry the account id.

## Never include

Secrets, account ids or employer references.
