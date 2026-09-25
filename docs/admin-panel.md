# Web admin panel

A browser admin panel for day-to-day operations. It covers the same jobs as
the operator scripts (`scripts/plans.sh`, `orders.sh`, `panel-groups.sh`,
`register-panel.sh`) and adds the views they never had: customer accounts,
devices, subscriptions and the audit log.

It is served by the API itself, so there is nothing extra to deploy:

```
https://<api domain>/admin
```

The existing nginx configuration already proxies every path to the API, so
`/admin` works on a deployed server without any change.

The shell scripts stay. They use the same admin API, so the validation,
role checks and audit log are identical whichever one you use, and they
still work over SSH when a browser is not practical.

## What it shows

Every number and list comes straight from the database or from a panel's
own API. The panel does not estimate, cache or invent anything.

| Section | What you can do |
|---|---|
| Dashboard | Users by status, active subscriptions, subscriptions expiring in the next 7 days, pending orders, paid orders with no subscription yet, panels reachable at their last test, and paid revenue over the last 30 days per currency (Finance and Manager only). |
| Orders | Pending orders first. Confirm a card-to-card payment (with a confirmation step that shows who, what and how much), cancel an unpaid order, filter by status, and re-queue provisioning for a paid order that never received its service. |
| Plans | List every plan including hidden and archived ones, create a plan, edit name, description, price and sort order, and switch between ACTIVE, HIDDEN and ARCHIVED. Existing customers are never affected: each order keeps its own snapshot of the plan. |
| Users | Search by username, email or phone. Open an account to see its subscriptions, devices (active and revoked) and last 20 orders; remove a device; suspend, ban or reactivate the account; the owner can also grant or remove admin roles. |
| Subscriptions | Every subscription with its owner, plan, traffic used against the limit, expiry and any provisioning error; re-fetch a subscription's configs from its panel. |
| Panels | Each panel's status and last test, test the connection, choose the default groups from the live list the panel returns, register a new panel, replace a panel's credentials. Credentials are write-only: the panel shows whether they are set, never their value. |
| Servers | The registered servers and the panel each belongs to. |
| Audit log | Every administrative write: who did it, what, to which record, when and from which IP, with the (already redacted) details. |

What is deliberately **not** in it:

* **Server health and load.** The `servers` table has `is_healthy` and
  `load_percent` columns, but nothing in the backend measures them yet. A
  column of default values would look like monitoring that does not exist.
* **Live panel probing on the dashboard.** Reachability is the result of the
  last connection test. Probing every panel on each page load would make the
  dashboard as slow as the slowest panel. A panel that has never been tested
  is shown as "untested", not as reachable.
* **Charts.** There is no time-series data behind the dashboard, only
  counts, so there are no charts.
* **Refunds and deleting accounts or plans.** A refund moves money and needs
  a gateway; deleting a plan would orphan the orders that reference it (use
  ARCHIVED instead).

## Who can do what

The role sets live in one table, `backend/app/api/admin_roles.py`. The API
guards use it, and `GET /api/v1/admin/me` returns the same table to the panel
so it can hide what a role cannot do. Hiding is only a convenience: every
action is enforced by the API regardless of what the page shows.

OWNER passes every check.

| Capability | Owner | Manager | Developer | Finance | Support |
|---|:-:|:-:|:-:|:-:|:-:|
| Dashboard counts | ✓ | ✓ | ✓ | ✓ | ✓ |
| Revenue on the dashboard | ✓ | ✓ | | ✓ | |
| Orders: list, confirm payment, cancel | ✓ | ✓ | | ✓ | |
| Plans: list, create, edit | ✓ | ✓ | ✓ | ✓ | |
| Panels and servers; reprovision | ✓ | ✓ | ✓ | | |
| Users: search and view | ✓ | ✓ | | | ✓ |
| Users: suspend, ban, reactivate | ✓ | ✓ | | | |
| Devices: remove a customer's device | ✓ | ✓ | | | ✓ |
| Subscriptions: list | ✓ | ✓ | ✓ | | ✓ |
| Audit log | ✓ | ✓ | | | |
| Grant or remove admin roles | ✓ | | | | |

Rules on changing an account (`PATCH /api/v1/admin/users/{id}`):

* Nobody can change their own account there — not their status and not
  their role. Suspending yourself locks you out mid-session; demoting
  yourself may leave nobody able to undo it.
* Only an owner can change roles, and only an owner can change an owner's
  account.
* The last active owner cannot be demoted, suspended or banned.
* Suspending or banning revokes every refresh token of the account.
  Access tokens already issued stop working at once as well, because every
  request re-reads the account status.

Every write through the panel is recorded in the audit log in the same
transaction as the change.

## API added for the panel

All under `/api/v1/admin`, all using the usual `{success, data, request_id}`
envelope.

| Method and path | Roles |
|---|---|
| `GET /me` | any admin role |
| `GET /stats` | any admin role (revenue only for Manager/Finance) |
| `GET /users?q=&limit=&offset=` | Manager, Support |
| `GET /users/{id}` | Manager, Support |
| `PATCH /users/{id}` | Manager (status); Owner (roles) |
| `DELETE /users/{id}/devices/{device_row_id}` | Manager, Support |
| `GET /subscriptions?status=&limit=&offset=` | Manager, Developer, Support |
| `GET /audit?limit=&offset=` | Manager |

No response contains a password hash, a refresh token, a panel credential, a
subscription URL or a device's hardware identifier.

## Security choices

**Strict Content-Security-Policy.** The panel is three static files —
`index.html`, `app.css`, `app.js` in `backend/app/admin_web/` — with no build
step, no framework, no CDN and no inline script or style. That is what lets
it run under:

```
default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;
connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'
```

Only those three files are served (explicit routes, not a directory mount).
The API's own responses keep their `default-src 'none'` policy. The panel's
responses also carry `X-Frame-Options: DENY` and `Referrer-Policy:
no-referrer`, and the page itself is `Cache-Control: no-store`.

**No HTML from data.** Every string that comes from the server or the
operator is inserted with `textContent`/`createElement`, never `innerHTML`.
A username such as `<img src=x onerror=...>` is displayed as text. A test
(`tests/test_admin_panel_api.py`) fails if `innerHTML`, `insertAdjacentHTML`,
`document.write` or `eval` appears in `app.js`, or if `index.html` gains an
inline script, style or event handler.

**Token storage.** The access token is kept only in memory. The refresh token
is kept in `sessionStorage`, so a reload does not force a new sign-in, but it
is gone when the tab closes and is never readable by another origin. No
cookie is used, so there is no CSRF surface.

**Serialised refresh.** The backend treats a reused refresh token as theft
and revokes the whole token family. The panel therefore never sends two
refreshes at once: requests that get a 401 at the same moment wait for one
shared refresh, then retry once. If the refresh fails, the panel returns to
the sign-in page.

One consequence: duplicating the browser tab copies `sessionStorage`, so both
tabs hold the same refresh token. Whichever refreshes second triggers the
reuse detection and both tabs are signed out. That is the safe outcome; open
the panel in a new tab and sign in instead of duplicating.

**No device registration.** Sign-in sends no `device_id`, so the panel never
creates a device row and never takes a slot from the operator's own VPN
device allowance.

**Non-admins.** A customer can sign in (the credentials are valid), but
`GET /admin/me` answers 403. The panel then revokes the session it just
opened and says that the account is not an administrator.

**Sign-out** calls `/api/v1/auth/logout`, which revokes the refresh token's
whole family on the server, and clears `sessionStorage`.

## First use

1. Register an account normally, then promote it with the bootstrap endpoint
   (see [deployment.md](deployment.md)).
2. Open `https://<api domain>/admin` and sign in with that account.
3. Register the panel under **Panels**, test the connection, and choose its
   default groups.
4. Create plans under **Plans**.
5. Grant roles to your staff from **Users** (owner only).

## Language and layout

The interface is Persian and right-to-left. Numbers, amounts, usernames,
hosts and ids are shown left-to-right in Latin digits so they read the same
everywhere; dates use the Persian (Jalali) calendar. The layout collapses to
a single column on a phone: the section tabs scroll horizontally and every
table turns into stacked cards.
