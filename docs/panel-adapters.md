# Panel adapters

## The contract

`app/panels/base.py` defines `PanelAdapter`. Every panel integration implements
the same eleven methods, so business logic never learns which panel it is
talking to.

```python
class PanelAdapter(ABC):
    async def test_connection(self) -> bool
    async def create_user(self, username, *, traffic_limit_bytes, expire_at,
                          device_limit=None, inbound_tags=None) -> PanelUser
    async def get_user(self, username) -> PanelUser | None
    async def update_user(self, username, **changes) -> PanelUser
    async def delete_user(self, username) -> bool
    async def disable_user(self, username) -> bool
    async def enable_user(self, username) -> bool
    async def get_usage(self, username) -> PanelUsage
    async def get_configs(self, username) -> list[str]
    async def renew_user(self, username, *, traffic_limit_bytes,
                         expire_at, reset_usage=True) -> PanelUser
    async def close(self) -> None
```

`PanelUser` normalises what panels return: traffic in bytes, expiry as a
timezone-aware datetime, configs as a list of URIs. Each panel's raw response
is preserved in `.raw` for debugging, but business logic reads the normalised
fields.

## Using an adapter

```python
from app.panels.manager import PanelManager

manager = PanelManager()
panel = await session.get(Panel, panel_id)

async with manager.adapter_for(panel) as adapter:
    user = await adapter.create_user(
        username=f"nexus_{subscription.id}",
        traffic_limit_bytes=plan.traffic_limit_gb * 1024**3,
        expire_at=subscription.expire_at,
    )
    configs = await adapter.get_configs(user.username)
```

Always use the async context manager. It closes the HTTP client, and a leaked
client is a leaked connection pool.

## Credential handling rules

An adapter receives a `PanelCredentials` dataclass, already decrypted, valid
for one operation. Three rules, all enforced by tests:

1. **Never log them.** Not at DEBUG, not in an error path. `PanelCredentials`
   overrides `__repr__` and `__str__` to print `[REDACTED]`, so even a
   traceback that renders it stays clean.
2. **Never return them.** No method returns a credential, and `PanelUser` has
   no field that could carry one.
3. **Never persist them.** Only `PanelManager.encrypt_credentials()` writes
   credentials, and only as ciphertext.

Forwarding a panel's raw error text to a client breaks rule 1 indirectly — a
panel's 500 page can contain internal hostnames or a token in a redirect URL.
Adapters raise `PanelError` with a message they wrote themselves and a status
code, never the upstream body.

## Writing a new adapter

1. Create `app/panels/<name>.py` with a class extending `PanelAdapter`.
2. Put endpoint paths and payload key names in module-level `ENDPOINTS` and
   `FIELDS` dicts. Panel forks rename fields between versions; keeping the
   mapping in one place means adapting to a fork is a dict edit.
3. Normalise into `PanelUser` / `PanelUsage`. Convert traffic to bytes and
   timestamps to timezone-aware UTC datetimes at the boundary.
4. Make `create_user` idempotent — check for the existing user first. Jobs get
   retried; a duplicate panel user means a user paying for one subscription and
   occupying two slots.
5. Register it in `app/panels/manager.py`: add to `_ADAPTERS` and remove from
   `_UNIMPLEMENTED`.
6. Add tests. Mock the HTTP layer with `httpx.MockTransport` — never hit a real
   panel from the test suite.

```python
_ADAPTERS = {
    PanelType.PASARGUARD: PasarGuardAdapter,
    PanelType.MARZBAN: MarzbanAdapter,   # new
}
```

Nothing outside `manager.py` changes.

## PasarGuard adapter status

Implemented against PasarGuard's Marzban-compatible REST surface: token auth at
`/api/admin/token`, user CRUD under `/api/user`, usage reset at
`/api/user/{username}/reset`.

**Verify before production use.** The endpoint paths and field names in
`ENDPOINTS` and `FIELDS` match the documented Marzban-compatible API, but they
have not been exercised against a live PasarGuard instance in this repository.
Before going live:

1. Add the panel in the admin panel with its credentials.
2. Run the connection test.
3. Create a throwaway user, read it back, check traffic and expiry, then delete
   it.

If a call fails, correct `ENDPOINTS` or `FIELDS` in `app/panels/pasarguard.py`.
Do not work around a mismatch in calling code — that is how the abstraction
starts leaking panel-specific behaviour.

## Unimplemented panels

X-UI, Marzban and Custom are registered in `_UNIMPLEMENTED` and raise
`NotImplementedYetError` (HTTP 501) with a message naming the phase they are
planned for.

This is deliberate (spec rule 67). A stub returning a plausible `PanelUser`
would let a user pay for a subscription, see a success screen, and receive a
config that connects to nothing. A 501 surfaces the gap at the moment an
operator configures the panel, which is when it is cheap to fix.
