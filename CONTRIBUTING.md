# Contributing

Issues and pull requests are welcome. Report a security problem
[privately](https://github.com/TauricResearch/Trading-R1/security/advisories/new), not in an issue.

## Issues

Use the issue forms. Give the output of `trading-r1 --version`, the model, what you did, what you
expected and what happened. Remove keys and anything private first.

## Pull requests

- Keep one change to a pull request, and say what it does and why.
- Open an issue first for a large change or a new dependency.
- Run `make lint` and `make smoke`. Both must pass.

## Standards

- Python 3.12 or later, formatted and linted by `ruff`. TypeScript formatted by Biome.
- Every source file opens with the copyright and SPDX lines.
- Layers import downward: `contract`, `state` and `providers`, then `runtime`, then `tools` and
  `service`. `runtime` does not import `tools`.
- Tools report expected failures with `ToolError` and do not raise anything else. Every price tool
  goes through `tools/data/pricing.py`.
- Comments say what the code does or a constraint a reader must know, not its history.
- User-facing text is short and plain. Use no em dash, en dash or middle dot.
- `tui/src/lib/events.gen.ts` and `assets/taxonomy.svg` are generated. Leave them to a maintainer.
- Do not commit credentials or machine-specific paths and hosts.
