"""Who may do what in the admin API — one table, used by both sides.

The role sets live here rather than inline on each route for one reason: the
web admin panel asks ``GET /admin/me`` which actions to show, and the answer
must come from the very sets the routes enforce. Two copies (one guarding the
route, one describing it to the UI) would drift, and the first sign would be a
button that always fails or, worse, a missing button for a job someone needs
to do.

OWNER is not listed anywhere: ``require_roles`` lets OWNER through every
guard, so an empty set means "OWNER only".
"""

from __future__ import annotations

from app.models.enums import AdminRole

M, D, F, S = (
    AdminRole.MANAGER,
    AdminRole.DEVELOPER,
    AdminRole.FINANCE,
    AdminRole.SUPPORT,
)

CAPABILITIES: dict[str, frozenset[AdminRole]] = {
    # Every admin sees the dashboard counts; revenue is gated separately.
    "stats": frozenset({M, D, F, S}),
    # Money figures: the people who reconcile payments and the manager.
    "revenue": frozenset({M, F}),
    # Panels hold the credentials to the whole fleet, so only the operators
    # who run it. Also covers servers and provisioning retries.
    "panels": frozenset({M, D}),
    # Pricing is a commercial decision as well as an operational one.
    "plans": frozenset({M, D, F}),
    # Confirming a card-to-card payment is a finance job.
    "orders": frozenset({M, F}),
    # Reading customer accounts (username, email, phone, devices): the people
    # who answer customers. Finance and Developer get by with usernames on
    # orders and subscriptions and do not need contact details.
    "users.read": frozenset({M, S}),
    # Suspending or banning an account is a decision, not a support reflex.
    "users.status": frozenset({M}),
    # "I lost my phone, remove it" is the most common support request.
    "devices.revoke": frozenset({M, S}),
    # Support answers "my service does not work"; Developer debugs
    # provisioning.
    "subscriptions.read": frozenset({M, D, S}),
    # The audit log shows everyone's actions, including other admins'.
    "audit.read": frozenset({M}),
    # Reviewing top-ups and correcting a wallet: finance work.
    "payments": frozenset({M, F}),
    # Where customers send their money. Whoever can change this can redirect
    # every payment to their own card, so it is OWNER only.
    "payments.settings": frozenset(),
    # Granting or removing admin roles: OWNER only.
    "roles.write": frozenset(),
}


def roles_for(capability: str) -> tuple[AdminRole, ...]:
    """The roles to hand to ``require_roles`` for one capability."""
    return tuple(sorted(CAPABILITIES[capability]))


def capabilities_of(role: AdminRole | None) -> list[str]:
    """Every capability a role holds, for the UI to decide what to show."""
    if role is None:
        return []
    if role is AdminRole.OWNER:
        return sorted(CAPABILITIES)
    return sorted(name for name, roles in CAPABILITIES.items() if role in roles)
