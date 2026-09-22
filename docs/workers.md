# Workers and the job queue

## Why work happens outside the request

Creating a panel account means an HTTP call to somebody else's server. It can
be slow, it can fail, and it must be retried. Doing that inside a payment
callback would make the gateway time out and retry the callback — which is how
one payment becomes two subscriptions.

So the API only ever enqueues. The worker executes.

```
POST /admin/orders/{id}/confirm-payment
        │
        ├── mark_paid (row lock, transitions once)
        ├── commit
        └── enqueue create_panel_user ──▶ Redis Stream
                                              │
                                              ▼
                                          worker ──▶ panel
```

## Delivery guarantees

The queue is a Redis Stream with a consumer group, which gives **at-least-once**
delivery: a job stays pending until acknowledged, so a worker killed mid-job
does not lose it — `xautoclaim` hands it to another worker after 5 minutes.

At-least-once means a job can run twice. That is safe because every handler is
idempotent. Exactly-once delivery does not exist in a distributed system;
at-least-once plus idempotent handlers is the achievable equivalent, and it is
what phase 2's database constraints were built for.

## Retry and failure

| Attempt | Delay before retry |
|---|---|
| 1 | 5s |
| 2 | 30s |
| 3 | 2m |
| 4+ | 10m |

After 5 attempts a job goes to the `nexus:jobs:dead` stream with its error.
Inspect it with:

```bash
docker compose exec redis redis-cli XRANGE nexus:jobs:dead - + COUNT 20
```

A dead-lettered job means something needs a human: wrong panel credentials, a
panel that no longer exists, an API shape the adapter does not match.

Handlers signal intent by raising or returning:

- **raise** — transient. The panel is down; retry with backoff.
- **return** — permanent and already recorded. The order was cancelled;
  retrying forever helps nobody.

## Jobs

| Job | Triggered by | What it does |
|---|---|---|
| `create_panel_user` | Payment confirmation | Provisions a paid order end to end |
| `refresh_configs` | User or admin request | Re-fetches configs from the panel |
| `sync_usage` | Scheduler, every 15m | Pulls traffic; suspends on exhausted quota |
| `expire_subscriptions` | Scheduler, every 5m | Disables on panel, then marks expired |
| `sync_panel_health` | Scheduler, every 10m | Records each panel's reachability |

## Locking

Each job takes a lock on its subject — `subscription:{id}`, `order:{id}`, or
the job name for a sweep — so two workers do not act on the same subscription
at once. If the lock is held, the job is acknowledged and skipped: the holder's
run covers it.

The lock is an optimisation. It reduces duplicated work; it is not what makes
the system correct. A lock that expires mid-operation must not be able to
corrupt anything, and here it cannot, because the database constraints hold
regardless.

## Ordering that matters

**Provisioning**: create the panel account *before* activating the
subscription. Reversed, a panel failure would leave an ACTIVE subscription the
customer pays for with nothing behind it.

**Expiry**: disable on the panel *before* marking the subscription EXPIRED.
Reversed, an unreachable panel would leave a working account that nothing
points at — the customer keeps their VPN for free and no record says so. If the
disable fails, the subscription stays ACTIVE and the next sweep tries again.

## Running

```bash
docker compose up -d worker scheduler
docker compose logs -f worker
```

Scale workers horizontally; the consumer group distributes jobs:

```bash
docker compose up -d --scale worker=3
```

Run more than one scheduler only for availability — each tick takes a lock, so
two schedulers do not double-enqueue.

## Without Redis

If `REDIS_URL` is empty, both the queue and the locks fall back to in-process
implementations. That is for tests and local development only: state lives in
one process, so a second worker shares nothing. Never deploy that way.
