package com.nexora.vpn.domain.model

/**
 * Domain models: what the app reasons about, independent of how the API
 * happens to shape its JSON today.
 *
 * DTOs in `data.remote.dto` mirror the wire format and are mapped into these.
 * That indirection earns its keep the first time the API renames a field: one
 * mapper changes, and no screen notices.
 */

// --- account ----------------------------------------------------------------

data class User(
    val id: String,
    val username: String,
    val email: String? = null,
    val phone: String? = null,
    val status: UserStatus = UserStatus.ACTIVE,
    val isEmailVerified: Boolean = false,
)

enum class UserStatus { PENDING, ACTIVE, SUSPENDED, BANNED, UNKNOWN }

data class Device(
    val id: String,
    val name: String,
    val platform: String,
    val appVersion: String? = null,
    val lastSeenAtEpochMs: Long? = null,
    val isCurrent: Boolean = false,
)

data class AuthSession(
    val accessToken: String,
    val refreshToken: String,
    val expiresInSeconds: Int,
) {
    /** Never log or display a token; this keeps an accidental print harmless. */
    override fun toString(): String = "AuthSession(expiresIn=$expiresInSeconds)"
}

// --- store ------------------------------------------------------------------

data class Plan(
    val id: String,
    val name: String,
    val description: String? = null,
    val durationDays: Int,
    val trafficLimitBytes: Long,
    val deviceLimit: Int,
    val price: Long,
    val currency: String,
) {
    /** `0` is how the backend expresses "unlimited", for traffic and devices. */
    val isUnlimitedTraffic: Boolean get() = trafficLimitBytes == 0L
    val isUnlimitedDevices: Boolean get() = deviceLimit == 0
}

// --- subscriptions ----------------------------------------------------------

data class Subscription(
    val id: String,
    val planId: String,
    val status: SubscriptionStatus,
    val trafficLimitBytes: Long,
    val trafficUsedBytes: Long,
    val deviceLimit: Int,
    val expireAtEpochMs: Long? = null,
    val daysRemaining: Int? = null,
) {
    val isUnlimitedTraffic: Boolean get() = trafficLimitBytes == 0L

    val trafficRemainingBytes: Long?
        get() = if (isUnlimitedTraffic) null
        else (trafficLimitBytes - trafficUsedBytes).coerceAtLeast(0L)

    /**
     * Fraction of quota consumed, 0f..1f. `null` when unlimited — a progress
     * bar for an unlimited plan is meaningless and the UI should omit it
     * rather than draw an empty one.
     */
    val trafficFraction: Float?
        get() = when {
            isUnlimitedTraffic -> null
            trafficLimitBytes <= 0L -> null
            else -> (trafficUsedBytes.toFloat() / trafficLimitBytes).coerceIn(0f, 1f)
        }

    val isUsable: Boolean get() = status == SubscriptionStatus.ACTIVE

    /** Worth warning about in the UI: expiring within a week, or nearly out. */
    val needsAttention: Boolean
        get() = isUsable && (
            (daysRemaining != null && daysRemaining <= 7) ||
                (trafficFraction != null && trafficFraction!! >= 0.9f)
            )
}

enum class SubscriptionStatus {
    PENDING, ACTIVE, EXPIRED, SUSPENDED, CANCELLED, UNKNOWN;

    companion object {
        fun fromApi(raw: String?): SubscriptionStatus =
            entries.firstOrNull { it.name.equals(raw, ignoreCase = true) } ?: UNKNOWN
    }
}

// --- orders -----------------------------------------------------------------

data class Order(
    val id: String,
    val planId: String,
    val subscriptionId: String? = null,
    val amount: Long,
    val currency: String,
    val status: OrderStatus,
    val createdAtEpochMs: Long? = null,
    val failureReason: String? = null,
)

enum class OrderStatus {
    PENDING, PAID, FAILED, CANCELLED, REFUNDED, UNKNOWN;

    val isSettled: Boolean get() = this != PENDING && this != UNKNOWN

    companion object {
        fun fromApi(raw: String?): OrderStatus =
            entries.firstOrNull { it.name.equals(raw, ignoreCase = true) } ?: UNKNOWN
    }
}

// --- configs ----------------------------------------------------------------

data class VpnConfig(
    val id: String,
    val subscriptionId: String,
    val name: String,
    val protocol: String? = null,
    val host: String? = null,
    val port: Int? = null,
    val configData: String,
    val latencyMs: Int? = null,
    val isActive: Boolean = false,
) {
    /**
     * Never include [configData] in a string representation. It is the
     * credential for the user's own service, and this object will end up in a
     * log or a crash report eventually.
     */
    override fun toString(): String =
        "VpnConfig(id=$id, name=$name, host=$host, active=$isActive)"
}
