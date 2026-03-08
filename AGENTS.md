# Nasuchan Agent Guide

This repository is the frontend layer for self-hosted backend service APIs.
Phase 1 is a Telegram Bot only. Future clients such as a web UI may be added
later, but they are out of scope for the initial implementation.

This file is the operating contract for coding agents and contributors entering
the repo at an early stage. It is intentionally short and practical.

## Current State

- The repo is currently minimal and only has an existing `.venv`.
- The environment already indicates `uv` management and Python `3.13.x`.
- New code should follow the structure and conventions in this document even if
  the directories do not exist yet.

## Runtime And Tooling

- Python version: `3.13.x` to match the existing `.venv` (currently `3.13.8`).
- Package and command management must go through `uv`.
- Prefer:
  - `uv sync`
  - `uv add ...`
  - `uv run ...`
- Do not use raw `pip install` or create ad-hoc virtual environments unless
  explicitly asked.
- Use `aiogram` as the Telegram Bot framework.
- Keep secrets in environment variables or local `.env` files. Do not hardcode
  tokens, backend URLs, or deployment-specific values in source code or docs.

## Expected Project Layout

Create new code under the following layout:

```text
src/
  nasuchan/
    bot/
    clients/
    config/
    services/
tests/
```

Directory responsibilities:

- `src/nasuchan/bot/`: Telegram startup, routers, handlers, middleware, and
  Telegram-specific presentation logic.
- `src/nasuchan/clients/`: adapters for self-hosted backend APIs.
- `src/nasuchan/config/`: centralized settings, environment loading, and app
  configuration.
- `src/nasuchan/services/`: orchestration between bot handlers and backend
  clients.
- `tests/`: unit and integration tests.

## Internal Architecture Contract

Use this flow as the default contract:

`Telegram handler -> service layer -> backend API client`

Required boundaries:

- Keep Telegram-facing concerns inside `bot/`.
- Keep backend integrations isolated inside `clients/`.
- Keep application orchestration in `services/`.
- Keep configuration loading centralized in `config/`.
- Backend clients should expose domain-oriented async methods, not raw
  Telegram-specific payload shaping.
- Favor async-first code paths to match `aiogram`.
- Prefer typed request and response models at boundaries between handlers,
  services, and backend clients.

## Working Conventions

- New entrypoints should be runnable through `uv run ...`.
- When new handler logic is added, add tests for its behavior.
- When new backend clients are added, add tests for request shaping, response
  handling, and expected failure cases.
- When integrating a new external service, document required environment
  variables and failure behavior close to the code that introduces it.
- Before pushing to any public remote, scan both the current worktree and git
  history for sensitive data such as real bot tokens, API tokens, DSNs,
  passwords, private keys, `.env` contents, or committed `config.toml`.
- Run `gitleaks detect --source . --no-git` for the current worktree and
  `gitleaks git .` for repository history before any public push.
- Use obvious placeholders in examples and tests. Never commit live secrets,
  even temporarily, with the intention of rewriting history later.
- Keep the initial architecture simple. Avoid premature plugin systems,
  multi-frontend abstractions, or speculative framework layers in v1.
- Prefer small, composable modules over large mixed-responsibility files.

## Implementation Defaults

- Treat Telegram Bot as the only active frontend until the repo explicitly adds
  another client surface.
- Optimize for clear boundaries and delivery speed, not early generalization.
- If the repo later grows a second frontend, extend the service layer first
  instead of letting client-specific logic leak into backend adapters.
