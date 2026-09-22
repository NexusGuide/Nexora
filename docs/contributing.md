# Contributing

## Setup

```bash
git clone https://github.com/<your-account>/NexusVPN.git
cd NexusVPN
./scripts/install-hooks.sh          # do this first
./scripts/generate-secrets.sh --write

cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Install [gitleaks](https://github.com/gitleaks/gitleaks) too — the pre-commit
hook uses it when present and falls back to weaker regex checks when it is not.

## The rule that matters most

Never commit a secret. Not "temporarily", not "it's only a dev key", not in a
test fixture, not in a comment showing an example request.

If you need a value for a test, use a deterministic literal suffixed
`-for-tests-only`. The gitleaks allowlist recognises that suffix, and it makes
the intent obvious to a reviewer.

```python
JWT_SECRET = "a1b2c3d4e5f60718293a4b5c6d7e8f90-access-for-tests-only"  # fine
JWT_SECRET = os.urandom(32).hex()                                       # also fine
JWT_SECRET = "my-actual-dev-secret-from-my-server"                      # never
```

If you commit a secret anyway: **rotate it first**, then clean history. Deleting
the commit does not un-leak it — see [SECURITY.md](../SECURITY.md).

## Code style

```bash
cd backend
ruff check . --fix
ruff format .
mypy app
pytest
```

- Type-annotate public functions.
- Keep functions small and single-purpose; no god classes.
- No global mutable state.
- Business rules go in `services/`, not in routers.
- New config goes in `Settings`, read from the environment, with a validator if
  it is a secret.

## Comments

Comment *why*, not *what*. The code says what it does; a comment earns its place
by explaining a decision that is not obvious.

```python
# Verify against a dummy hash so a missing user costs the same time as a
# wrong password — otherwise response latency enumerates accounts.
verify_password(payload.password, _DUMMY_HASH)
```

Not:

```python
# Verify the password
verify_password(payload.password, _DUMMY_HASH)
```

## Tests

Required for anything touching authentication, payments, orders,
subscriptions or a panel adapter.

- Mock HTTP with `httpx.MockTransport`. Never call a real panel or gateway.
- Test the failure paths, not just the happy one: wrong key, expired token,
  reused token, duplicate request.
- Assert that secrets do not leak — several existing tests check that a
  response or a `repr()` contains no credential. Keep that habit.

## Pull requests

1. Branch from `develop`.
2. Make sure `ruff`, `mypy`, `pytest` and the pre-commit hook all pass.
3. Describe what changed and why. If it touches auth, payments or panels, say
   what you tested.
4. CI must be green — including the secret scan over full history.

## Adding a panel or a payment provider

See [panel-adapters.md](panel-adapters.md). Both follow the same shape:
implement the interface, register it in the manager, add tests. If it is not
finished, register it as unimplemented so it raises 501 — never ship a stub
that returns a plausible success.
