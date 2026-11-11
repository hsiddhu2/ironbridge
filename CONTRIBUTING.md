# Contributing

Thanks for looking. This repo backs a blog series. Each demo under `demos/` backs one post, so its measurement must keep working.

## Run the checks

```
make install
make ci
```

`make ci` lints and tests the package, then for each demo builds it, checks its headline number, synthesizes its stack and scans the template with checkov. None of it needs AWS credentials. CI runs the same target on Node 22.

## Propose a change

1. Open an issue first for anything bigger than a fix.
2. Fork and branch from `main`.
3. Use conventional commit messages, for example `fix: handle empty DD name`.
4. Open a PR and fill in the template.

## The measurement must still pass

Each demo's `make build` must still print the number in its README. CI fails if it changes. If you touch a measured path, run `make deploy measure destroy` from that demo folder in a sandbox account and paste the summary in the PR. Do not paste `results.csv`, the execution ARNs carry the account id.

## Never include

Secrets, account ids or employer references.
