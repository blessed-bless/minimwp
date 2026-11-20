# Portfolio style standard

Target look: one developer, calm and concrete. Behavior stays the same; style aligns.

## Layout

```
app/               # library + app code (flat layout kept)
tests/
scripts/
Dockerfile         # multi-stage, non-root
Makefile           # run test lint fmt
.env.example       # commented keys, no secrets
README.md
pyproject.toml     # tooling; runtime deps may stay in requirements.txt
```

## Python

- Version: **3.12**
- Tooling: **ruff**
- Config: **pydantic-settings**
- Logging: stdlib `logging`, one formatter
- Comments: English only, explain *why*

## Infra

- Dockerfile: multi-stage when needed; final image non-root
- Makefile: `run`, `test`, `lint`, `fmt`
- `.env.example`: every runtime variable with a short comment

## README

One sentence · screenshot · 4–6 capabilities · mermaid · stack · run · config table.

## Commits

Conventional Commits, English, short. Sole human author.
