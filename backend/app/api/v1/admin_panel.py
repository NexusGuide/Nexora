"""Admin endpoints behind the web admin panel (served at ``/admin``).

``admin.py`` holds the operational writes that existed before the panel
(panels, plans, orders). This module adds what a browser UI needs on top: who
is signed in, dashboard counts, and the customer-facing views — users,
their devices and subscriptions — plus the audit log.

Conventions shared with ``admin.py``:

* Every route is guarded by ``require_roles`` with a set taken from
  :mod:`app.api.admin_roles`, so ``GET /admin/me`` can tell the UI what to show
  from the same table the guards enforce.
* Every write records an audit entry in the same transaction as the change,
  so there is no committed change without its audit row.
* Responses carry what an operator needs and nothing that grants access:
  no password hash, no refresh token, no subscription URL, no config data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select, update

from app.api.admin_roles import capabilities_of, roles_for
from app.api.deps import SessionDep, client_ip, require_roles, user_agent
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.models.billing import Order, Plan, Subscription
from app.models.enums import (
    AdminRole,
    OrderStatus,
    PanelStatus,
    SubscriptionStatus,
    UserStatus,
)
from app.models.panel import AuditLog, Panel
from app.models.user import RefreshToken, User, UserDevice
from app.schemas.admin import UserAdminUpdate
from app.services.device_service import DeviceService
from app.services.panel_service import PanelService

router = APIRouter(prefix="/admin", tags=["admin-panel"])


def _guard(capability: str):
    return Depends(require_roles(*roles_for(capability)))


AnyAdmin = Annotated[User, _guard("stats")]
UserReader = Annotated[User, _guard("users.read")]
UserWriter = Annotated[User, _guard("users.status")]
DeviceRevoker = Annotated[User, _guard("devices.revoke")]
SubscriptionReader = Annotated[User, _guard("subscriptions.read")]
AuditReader = Annotated[User, _guard("audit.read")]

# Lists are paged; the cap keeps one request from pulling the whole table.
MAX_PAGE = 100


def _envelope(data, request: Request):
    return {
        "success": True,
        "data": data,
        "request_id": getattr(request.state, "request_id", None),
    }


def _iso(value: Any) -> str | None:
    """ISO-8601 for a datetime; SQLite aggregates can hand back a string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _role(user: User) -> AdminRole | None:
    return AdminRole(user.admin_role) if user.admin_role is not None else None


def _like(q: str) -> str:
    """A LIKE pattern matching ``q`` literally anywhere in the column.

    ``%`` and ``_`` typed into the search box are escaped, so searching for
    ``a_b`` finds that username instead of every ``a?b``.
    """
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


# ------------------------------------------------------------------------ me
@router.get("/me", summary="The signed-in administrator and what they may do")
async def admin_me(request: Request, admin: AnyAdmin):
    """The web panel calls this right after sign-in.

    A customer account gets 403 here, which is how the panel tells someone
    they signed in successfully but are not an administrator. The capability
    list only decides which buttons are drawn; every route still enforces its
    own role set.
    """
    role = _role(admin)
    return _envelope(
        {
            "id": admin.id,
            "username": admin.username,
            "role": role.value if role else None,
            "capabilities": capabilities_of(role),
        },
        request,
    )


# --------------------------------------------------------------------- stats
@router.get("/stats", summary="Dashboard counts, computed from the database")
async def admin_stats(request: Request, session: SessionDep, admin: AnyAdmin):
    """Real counts only — every number is a query, nothing is estimated.

    Revenue is included only for roles holding the ``revenue`` capability;
    for everyone else the field is ``null`` rather than absent, so the UI can
    tell "not permitted" from "not implemented".

    Panel reachability is the status recorded by the last connection test,
    not a live probe: probing every panel on each dashboard load would make
    the dashboard as slow as the slowest panel. A panel that has never been
    tested is counted as ``untested``, not reachable — its status column
    defaults to ACTIVE, which says nothing about whether it answers.
    """
    now = datetime.now(UTC)

    users_by_status = {
        status.value: count
        for status, count in (
            await session.execute(select(User.status, func.count()).group_by(User.status))
        ).all()
    }
    subs_by_status = {
        status.value: count
        for status, count in (
            await session.execute(
                select(Subscription.status, func.count()).group_by(Subscription.status)
            )
        ).all()
    }
    expiring_7d = await session.scalar(
        select(func.count()).where(
            Subscription.status == SubscriptionStatus.ACTIVE,
            Subscription.expire_at >= now,
            Subscription.expire_at <= now + timedelta(days=7),
        )
    )
    orders_pending = await session.scalar(
        select(func.count()).where(Order.status == OrderStatus.PENDING)
    )
    # Paid but no subscription attached: either queued right now, or
    # provisioning failed before the panel account existed — the case the
    # order reprovision endpoint exists for.
    paid_unprovisioned = await session.scalar(
        select(func.count()).where(
            Order.status == OrderStatus.PAID, Order.subscription_id.is_(None)
        )
    )
    panels_by_status = {
        status.value: count
        for status, count in (
            await session.execute(
                select(Panel.status, func.count()).group_by(Panel.status)
            )
        ).all()
    }
    panels_reachable = await session.scalar(
        select(func.count()).where(
            Panel.status == PanelStatus.ACTIVE, Panel.last_checked_at.is_not(None)
        )
    )
    panels_untested = await session.scalar(
        select(func.count()).where(Panel.last_checked_at.is_(None))
    )

    revenue = None
    if "revenue" in capabilities_of(_role(admin)):
        since = now - timedelta(days=30)
        rows = (
            await session.execute(
                select(Order.currency, func.count(), func.sum(Order.amount))
                .where(
                    Order.status == OrderStatus.PAID,
                    Order.completed_at.is_not(None),
                    Order.completed_at >= since,
                )
                .group_by(Order.currency)
                .order_by(Order.currency)
            )
        ).all()
        # Amounts as strings: they are Decimals, and a JSON float would
        # reintroduce the rounding the Numeric column exists to avoid.
        revenue = [
            {"currency": currency, "orders": count, "amount": str(total or 0)}
            for currency, count, total in rows
        ]

    return _envelope(
        {
            "users": {
                "total": sum(users_by_status.values()),
                "by_status": users_by_status,
            },
            "subscriptions": {
                "active": subs_by_status.get(SubscriptionStatus.ACTIVE.value, 0),
                "by_status": subs_by_status,
                "expiring_7d": expiring_7d or 0,
            },
            "orders": {
                "pending": orders_pending or 0,
                "paid_unprovisioned": paid_unprovisioned or 0,
            },
            "revenue_30d": revenue,
            "panels": {
                "total": sum(panels_by_status.values()),
                "reachable": panels_reachable or 0,
                "untested": panels_untested or 0,
                "by_status": panels_by_status,
            },
            "generated_at": now.isoformat(),
        },
        request,
    )


# --------------------------------------------------------------------- users
def _active_subscriptions_subquery():
    return (
        select(
            Subscription.user_id.label("user_id"),
            func.count().label("active_count"),
            func.max(Subscription.expire_at).label("latest_expire_at"),
        )
        .where(Subscription.status == SubscriptionStatus.ACTIVE)
        .group_by(Subscription.user_id)
        .subquery()
    )


def _user_row(user: User) -> dict[str, Any]:
    # Built field by field from an allowlist: a new sensitive column on User
    # stays out of this response until someone decides it belongs here.
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "phone": user.phone,
        "status": user.status.value,
        "admin_role": user.admin_role.value if user.admin_role else None,
        "is_email_verified": user.is_email_verified,
        "created_at": _iso(user.created_at),
        "last_login_at": _iso(user.last_login_at),
    }


@router.get("/users", summary="Search accounts by username, email or phone")
async def list_users(
    request: Request,
    session: SessionDep,
    admin: UserReader,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    active = _active_subscriptions_subquery()
    conditions = []
    if q and q.strip():
        pattern = _like(q.strip().lower())
        conditions.append(
            or_(
                User.username.like(pattern, escape="\\"),
                func.lower(User.email).like(pattern, escape="\\"),
                User.phone.like(pattern, escape="\\"),
            )
        )

    total = await session.scalar(
        select(func.count()).select_from(User).where(*conditions)
    )
    rows = (
        await session.execute(
            select(User, active.c.active_count, active.c.latest_expire_at)
            .outerjoin(active, active.c.user_id == User.id)
            .where(*conditions)
            .order_by(User.created_at.desc(), User.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    return _envelope(
        {
            "items": [
                {
                    **_user_row(user),
                    "active_subscriptions": count or 0,
                    "latest_expire_at": _iso(expire_at),
                }
                for user, count, expire_at in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        },
        request,
    )


def _subscription_row(sub: Subscription, plan_name: str | None) -> dict[str, Any]:
    # subscription_url is left out on purpose: it is a working credential for
    # the customer's service, and support never needs to read it.
    return {
        "id": sub.id,
        "status": sub.status.value,
        "plan_id": sub.plan_id,
        "plan_name": plan_name,
        "panel_id": sub.panel_id,
        "panel_username": sub.panel_username,
        "start_at": _iso(sub.start_at),
        "expire_at": _iso(sub.expire_at),
        "traffic_limit_bytes": sub.traffic_limit_bytes,
        "traffic_used_bytes": sub.traffic_used_bytes,
        "device_limit": sub.device_limit,
        "last_synced_at": _iso(sub.last_synced_at),
        "provisioning_error": sub.provisioning_error,
        "created_at": _iso(sub.created_at),
    }


async def _get_user(session, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="USER_NOT_FOUND")
    return user


@router.get("/users/{user_id}", summary="One account with its service and history")
async def get_user(
    user_id: str, request: Request, session: SessionDep, admin: UserReader
):
    user = await _get_user(session, user_id)

    subs = (
        await session.execute(
            select(Subscription, Plan.name)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.created_at.desc())
        )
    ).all()
    devices = await session.scalars(
        select(UserDevice)
        .where(UserDevice.user_id == user.id)
        .order_by(UserDevice.status, UserDevice.last_seen_at.desc().nullslast())
    )
    orders = (
        await session.execute(
            select(Order, Plan.name)
            .join(Plan, Plan.id == Order.plan_id)
            .where(Order.user_id == user.id)
            .order_by(Order.created_at.desc())
            .limit(20)
        )
    ).all()

    return _envelope(
        {
            **_user_row(user),
            "subscriptions": [_subscription_row(s, name) for s, name in subs],
            # The hardware device_id is omitted: the row id is what revoking
            # needs, and the raw identifier is of no use to an operator.
            "devices": [
                {
                    "id": d.id,
                    "device_name": d.device_name,
                    "platform": d.platform,
                    "app_version": d.app_version,
                    "status": d.status.value,
                    "last_seen_at": _iso(d.last_seen_at),
                    "created_at": _iso(d.created_at),
                }
                for d in devices
            ],
            "orders": [
                {
                    "id": o.id,
                    "status": o.status.value,
                    "amount": str(o.amount),
                    "currency": o.currency,
                    "plan_name": name,
                    "subscription_id": o.subscription_id,
                    "created_at": _iso(o.created_at),
                    "completed_at": _iso(o.completed_at),
                }
                for o, name in orders
            ],
        },
        request,
    )


async def _active_owner_count(session) -> int:
    return (
        await session.scalar(
            select(func.count()).where(
                User.admin_role == AdminRole.OWNER, User.status == UserStatus.ACTIVE
            )
        )
        or 0
    )


@router.patch("/users/{user_id}", summary="Suspend, ban or reactivate an account")
async def update_user(
    user_id: str,
    payload: UserAdminUpdate,
    request: Request,
    session: SessionDep,
    admin: UserWriter,
    ip: Annotated[str | None, Depends(client_ip)],
    ua: Annotated[str | None, Depends(user_agent)],
):
    """Change an account's status and, for an OWNER only, its admin role.

    The rules, and why:

    * **Nobody changes their own account here.** Suspending yourself locks
      you out mid-session; demoting yourself can leave nobody able to undo it.
    * **Only OWNER changes roles.** Otherwise a MANAGER could promote
      themselves via a second account, or promote a friend to OWNER.
    * **Only OWNER touches an OWNER account.** A MANAGER suspending the owner
      would be a takeover with a paper trail, but a takeover nonetheless.
    * **The last active OWNER cannot be demoted or disabled.** Without an
      owner, nobody can grant roles again and the bootstrap endpoint stays
      closed, so the only way back would be editing the database by hand.

    Suspending or banning revokes every refresh token of the account, so
    no session can be renewed. Access tokens already issued stop working at
    once as well: every request re-reads the account status.
    """
    target = await _get_user(session, user_id)
    actor_role = _role(admin)
    role_requested = "admin_role" in payload.model_fields_set
    status_requested = payload.status is not None

    if not role_requested and not status_requested:
        return _envelope(_user_row(target), request)

    if target.id == admin.id:
        raise PermissionDeniedError(
            "You cannot change your own account here", code="CANNOT_MODIFY_SELF"
        )
    if role_requested and "roles.write" not in capabilities_of(actor_role):
        raise PermissionDeniedError(
            "Only an owner can change administrator roles", code="ROLE_CHANGE_FORBIDDEN"
        )
    if _role(target) is AdminRole.OWNER and actor_role is not AdminRole.OWNER:
        raise PermissionDeniedError(
            "Only an owner can change an owner's account", code="TARGET_IS_OWNER"
        )

    new_role = payload.admin_role if role_requested else _role(target)
    new_status = payload.status or target.status
    loses_owner = _role(target) is AdminRole.OWNER and (
        new_role is not AdminRole.OWNER or new_status is not UserStatus.ACTIVE
    )
    if loses_owner and await _active_owner_count(session) <= 1:
        raise ConflictError(
            "This is the last active owner; appoint another owner first",
            code="LAST_OWNER",
        )

    changes: dict[str, Any] = {}
    if status_requested and new_status is not target.status:
        changes["status"] = {"from": target.status.value, "to": new_status.value}
        target.status = new_status
    if role_requested and new_role != _role(target):
        changes["admin_role"] = {
            "from": target.admin_role.value if target.admin_role else None,
            "to": new_role.value if new_role else None,
        }
        target.admin_role = new_role

    revoked = 0
    if "status" in changes and new_status is not UserStatus.ACTIVE:
        result = await session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == target.id, RefreshToken.revoked_at.is_(None))
            .values(
                revoked_at=datetime.now(UTC),
                revoked_reason=f"account_{new_status.value.lower()}",
            )
        )
        revoked = result.rowcount or 0

    await session.flush()
    if changes:
        await PanelService(session).record_audit(
            actor_id=admin.id,
            action="user.update",
            entity="user",
            entity_id=target.id,
            ip_address=ip,
            user_agent=ua,
            metadata={**changes, "sessions_revoked": revoked},
        )
    await session.commit()
    return _envelope({**_user_row(target), "sessions_revoked": revoked}, request)


@router.delete(
    "/users/{user_id}/devices/{device_row_id}",
    summary="Sign a customer's device out and free its slot",
)
async def revoke_user_device(
    user_id: str,
    device_row_id: str,
    request: Request,
    session: SessionDep,
    admin: DeviceRevoker,
    ip: Annotated[str | None, Depends(client_ip)],
    ua: Annotated[str | None, Depends(user_agent)],
):
    """For the customer who lost a phone and is now at their device limit.

    Goes through ``DeviceService.revoke``, the same path the customer's own
    "remove device" uses, so the device's refresh tokens are revoked with it
    rather than left working.
    """
    await _get_user(session, user_id)
    device = await DeviceService(session).revoke(user_id, device_row_id)
    if device is None:
        raise NotFoundError("Device not found", code="DEVICE_NOT_FOUND")

    await PanelService(session).record_audit(
        actor_id=admin.id,
        action="device.revoke",
        entity="user_device",
        entity_id=device.id,
        ip_address=ip,
        user_agent=ua,
        metadata={"user_id": user_id, "device_name": device.device_name},
    )
    await session.commit()
    return _envelope({"id": device.id, "status": device.status.value}, request)


# ------------------------------------------------------------- subscriptions
@router.get("/subscriptions", summary="List subscriptions, newest first")
async def list_subscriptions(
    request: Request,
    session: SessionDep,
    admin: SubscriptionReader,
    status: SubscriptionStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    conditions = [Subscription.status == status] if status is not None else []
    total = await session.scalar(
        select(func.count()).select_from(Subscription).where(*conditions)
    )
    rows = (
        await session.execute(
            select(Subscription, User.username, Plan.name)
            .join(User, User.id == Subscription.user_id)
            .join(Plan, Plan.id == Subscription.plan_id)
            .where(*conditions)
            .order_by(Subscription.created_at.desc(), Subscription.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return _envelope(
        {
            "items": [
                {
                    **_subscription_row(sub, plan_name),
                    "user_id": sub.user_id,
                    "username": username,
                }
                for sub, username, plan_name in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        },
        request,
    )


# --------------------------------------------------------------------- audit
@router.get("/audit", summary="Recent administrative actions")
async def list_audit(
    request: Request,
    session: SessionDep,
    admin: AuditReader,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Metadata was redacted when the entry was written, so it is returned
    as stored; nothing here can carry a credential."""
    total = await session.scalar(select(func.count()).select_from(AuditLog))
    rows = (
        await session.execute(
            select(AuditLog, User.username)
            .outerjoin(User, User.id == AuditLog.actor_id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    def _metadata(raw: str | None) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    return _envelope(
        {
            "items": [
                {
                    "id": entry.id,
                    "actor_id": entry.actor_id,
                    "actor_username": username,
                    "action": entry.action,
                    "entity": entry.entity,
                    "entity_id": entry.entity_id,
                    "ip_address": entry.ip_address,
                    "metadata": _metadata(entry.metadata_json),
                    "created_at": _iso(entry.created_at),
                }
                for entry, username in rows
            ],
            "total": total or 0,
            "limit": limit,
            "offset": offset,
        },
        request,
    )
