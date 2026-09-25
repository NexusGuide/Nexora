package com.nexora.vpn.data.repository

import com.nexora.vpn.data.remote.dto.ConfigDto
import com.nexora.vpn.data.remote.dto.DeviceDto
import com.nexora.vpn.data.remote.dto.OrderDto
import com.nexora.vpn.data.remote.dto.PlanDto
import com.nexora.vpn.data.remote.dto.SubscriptionDto
import com.nexora.vpn.data.remote.dto.UserDto
import com.nexora.vpn.data.remote.dto.PaymentMethodsDto
import com.nexora.vpn.data.remote.dto.TopUpDto
import com.nexora.vpn.data.remote.dto.WalletDto
import com.nexora.vpn.data.remote.dto.WalletTxDto
import com.nexora.vpn.domain.model.CardMethod
import com.nexora.vpn.domain.model.CryptoWallet
import com.nexora.vpn.domain.model.PaymentMethods
import com.nexora.vpn.domain.model.TopUp
import com.nexora.vpn.domain.model.TopUpMethod
import com.nexora.vpn.domain.model.TopUpStatus
import com.nexora.vpn.domain.model.Wallet
import com.nexora.vpn.domain.model.WalletTransaction
import com.nexora.vpn.domain.model.WalletTxKind
import com.nexora.vpn.domain.model.Device
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.OrderStatus
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus
import com.nexora.vpn.domain.model.User
import com.nexora.vpn.domain.model.UserStatus
import com.nexora.vpn.domain.model.VpnConfig

/**
 * DTO to domain conversion. The one place the wire format is allowed to leak
 * into, so an API change is a diff here rather than across every screen.
 *
 * Nothing here throws. An installed app cannot be patched as quickly as a
 * server can be changed, so unparseable data degrades to a sensible default
 * rather than crashing the list it appears in.
 */

/**
 * Money arrives as a decimal string ("249000.00") because the backend stores
 * NUMERIC. Parsed to whole currency units — Toman has no minor unit in
 * practice, and a Double here would reintroduce exactly the rounding error the
 * backend's NUMERIC avoids.
 */
internal fun parseAmount(raw: String?): Long {
    if (raw.isNullOrBlank()) return 0L
    val whole = raw.substringBefore('.').trim()
    return whole.toLongOrNull() ?: 0L
}

/**
 * ISO-8601 to epoch millis, without pulling in a date library.
 *
 * Returns null rather than throwing on anything unexpected: a malformed
 * timestamp should blank one field, not break a screen.
 */
internal fun parseIsoToEpochMs(raw: String?): Long? {
    if (raw.isNullOrBlank()) return null
    return try {
        val normalised = raw.trim().let {
            when {
                it.endsWith("Z") -> it.dropLast(1) + "+00:00"
                // A bare timestamp from the API is UTC by convention.
                !it.contains('+') && !it.substringAfter('T').contains('-') ->
                    "$it+00:00"
                else -> it
            }
        }
        java.time.OffsetDateTime.parse(normalised).toInstant().toEpochMilli()
    } catch (e: Exception) {
        null
    }
}

internal fun UserDto.toDomain(): User = User(
    id = id,
    username = username,
    email = email,
    phone = phone,
    status = UserStatus.entries.firstOrNull { it.name.equals(status, true) }
        ?: UserStatus.UNKNOWN,
    isEmailVerified = isEmailVerified,
)

internal fun DeviceDto.toDomain(currentDeviceId: String? = null): Device = Device(
    id = id,
    name = deviceName,
    platform = platform,
    appVersion = appVersion,
    lastSeenAtEpochMs = parseIsoToEpochMs(lastSeenAt),
    isCurrent = currentDeviceId != null && id == currentDeviceId,
)

internal fun PlanDto.toDomain(): Plan = Plan(
    id = id,
    name = name,
    description = description,
    durationDays = durationDays,
    trafficLimitBytes = trafficLimitBytes,
    deviceLimit = deviceLimit,
    price = parseAmount(price),
    currency = currency,
)

internal fun OrderDto.toDomain(): Order = Order(
    id = id,
    planId = planId,
    subscriptionId = subscriptionId,
    amount = parseAmount(amount),
    currency = currency,
    status = OrderStatus.fromApi(status),
    createdAtEpochMs = parseIsoToEpochMs(createdAt),
    failureReason = failureReason,
)

internal fun SubscriptionDto.toDomain(): Subscription = Subscription(
    id = id,
    planId = planId,
    status = SubscriptionStatus.fromApi(status),
    trafficLimitBytes = trafficLimitBytes,
    trafficUsedBytes = trafficUsedBytes,
    deviceLimit = deviceLimit,
    expireAtEpochMs = parseIsoToEpochMs(expireAt),
    daysRemaining = daysRemaining,
)

internal fun ConfigDto.toDomain(): VpnConfig = VpnConfig(
    id = id,
    subscriptionId = subscriptionId,
    name = name,
    protocol = protocol,
    host = host,
    port = port,
    configData = configData,
    latencyMs = latencyMs,
    isActive = isActive,
)

// --- wallet -------------------------------------------------------------------

internal fun WalletTxDto.toDomain(): WalletTransaction = WalletTransaction(
    id = id,
    kind = WalletTxKind.fromApi(kind),
    amount = parseSignedAmount(amount),
    balanceAfter = parseAmount(balanceAfter),
    note = note,
    createdAtEpochMs = parseIsoToEpochMs(createdAt),
)

internal fun WalletDto.toDomain(): Wallet = Wallet(
    balance = parseAmount(balance),
    currency = currency,
    pendingTopups = pendingTopups,
    transactions = transactions.map { it.toDomain() },
)

internal fun PaymentMethodsDto.toDomain(): PaymentMethods = PaymentMethods(
    minTopup = parseAmount(minTopup),
    maxTopup = parseAmount(maxTopup),
    card = card?.let { CardMethod(it.number, it.holder, it.bank, it.instructions) },
    crypto = crypto.map { CryptoWallet(it.network, it.asset, it.address, it.rate) },
    cryptoInstructions = cryptoInstructions,
)

internal fun TopUpDto.toDomain(): TopUp = TopUp(
    id = id,
    method = if (method.equals("CRYPTO", ignoreCase = true)) TopUpMethod.CRYPTO else TopUpMethod.CARD,
    status = TopUpStatus.fromApi(status),
    amount = parseAmount(amount),
    reference = reference,
    network = network,
    asset = asset,
    cryptoAmount = cryptoAmount?.let { if ('.' in it) it.trimEnd('0').trimEnd('.') else it },
    orderId = orderId,
    creditedAmount = creditedAmount?.let(::parseAmount),
    rejectReason = rejectReason,
    createdAtEpochMs = parseIsoToEpochMs(createdAt),
)

/** Like [parseAmount], keeping the sign: a purchase is a negative ledger entry. */
internal fun parseSignedAmount(raw: String?): Long {
    val trimmed = raw?.trim().orEmpty()
    return if (trimmed.startsWith("-")) -parseAmount(trimmed.drop(1)) else parseAmount(trimmed)
}
