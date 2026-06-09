# Contributing to MacroShot

Thanks for your interest in contributing! Please read this short guide
before opening an issue or PR.

## Before you start

- **For features and larger changes**, open a GitHub issue first to
  discuss the design. This avoids wasted work on something that may not
  fit the project direction.
- **For bug fixes**, an issue is helpful but not required for small
  fixes - a PR with a clear description is fine.
- Search existing issues and PRs first to avoid duplicates.

## Development setup

Follow the [Quick Start](README.md#quick-start). For development also
install dev dependencies (`.venv/bin/pip install -r requirements-dev.txt`)
and run the frontend in a second shell with `cd web && npm run dev` for
hot reload.

## Tests and checks

Before opening a PR, run:

```bash
# Backend tests
.venv/bin/python -m pytest tests/ -x -q

# Frontend tests + type-check + build
cd web && npx vitest run && npx tsc -b --noEmit && npx vite build && cd ..
```

CI runs these on every PR (`.github/workflows/ci.yml`).

## Code style

- **Python**: Standard library + type hints. Follow the patterns already
  in `src/` - async throughout, Pydantic v2 models for I/O, FastAPI
  dependencies for cross-cutting concerns.
- **TypeScript / React**: Functional components, hooks. Use the shared
  components in `web/src/components/` (Button, LoadingSpinner,
  EmptyState, MacroDisplay, etc.) - don't build inline equivalents.
- **CSS**: Use the CSS variables (`var(--text-primary)`, `var(--bg-card)`,
  etc.) for theme support. Avoid hardcoded colors.

## Commit messages

Short imperative subject (under 70 chars), then a blank line and a body
explaining *why* if it's not obvious from the diff.

## Pull requests

- Keep PRs focused - one feature or fix per PR.
- Include a brief description, a test plan, and screenshots for UI
  changes.
- Rebase or merge `main` into your branch before requesting review if
  there are conflicts.

## Code of Conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed
under the [FSL-1.1-Apache-2.0](LICENSE) license.
