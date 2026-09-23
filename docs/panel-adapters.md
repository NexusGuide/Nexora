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

**Authentication and reachability are verified against a live panel.** The
connection test (`POST /api/v1/admin/panels/{id}/test`) has been run against a
running PasarGuard instance and passes, which confirms the base URL handling
and the token endpoint at `/api/admin/token`.

**The user-lifecycle calls are not yet confirmed.** Reachability proves the
adapter can authenticate; it does not prove that create, read, reset and delete
agree with your panel's API version. Before selling a real subscription:

1. Register a plan and a server bound to this panel.
2. Put a test order through to provisioning and check the panel shows the user.
3. Read it back — traffic and expiry should match what the plan specifies.

```bash
bash scripts/register-panel.sh     # registers a panel and runs the test
```

If a call fails, correct `ENDPOINTS` or `FIELDS` in `app/panels/pasarguard.py`.
Do not work around a mismatch in calling code — that is how the abstraction
starts leaking panel-specific behaviour.

## Group-based panels

Newer PasarGuard does not attach inbounds to users. It attaches them to
**groups**, and a user's access is whatever groups they are in. A user created
in no group exists, is active, and receives **no config** — which is exactly
what the first live purchase against a real panel produced.

Each panel therefore carries the groups new users are placed in:

```bash
bash scripts/panel-groups.sh      # lists the panel's groups live, stores your choice
```

or directly:

```
GET /api/v1/admin/panels/{id}/groups     the panel's groups, read live
PUT /api/v1/admin/panels/{id}/groups     {"group_ids": [1]}
```

The choice is checked against the panel before it is stored: an id that does
not exist, a disabled group, or a group granting no inbound is refused, since
any of them would let every purchase succeed and deliver nothing.

On a panel without a group model the list is empty, no `group_ids` field is
sent, and nothing changes.

## Unimplemented panels

X-UI, Marzban and Custom are registered in `_UNIMPLEMENTED` and raise
`NotImplementedYetError` (HTTP 501) with a message naming the phase they are
planned for.

This is deliberate (spec rule 67). A stub returning a plausible `PanelUser`
would let a user pay for a subscription, see a success screen, and receive a
config that connects to nothing. A 501 surfaces the gap at the moment an
operator configures the panel, which is when it is cheap to fix.
