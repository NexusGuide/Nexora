package com.nexora.vpn.data.repository

import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.common.map
import com.nexora.vpn.core.network.ApiCall
import com.nexora.vpn.core.security.DeviceIdentity
import com.nexora.vpn.core.security.TokenStore
import com.nexora.vpn.data.remote.api.NexoraApi
import com.nexora.vpn.data.remote.dto.ForgotPasswordRequestDto
import com.nexora.vpn.data.remote.dto.LoginRequestDto
import com.nexora.vpn.data.remote.dto.LogoutRequestDto
import com.nexora.vpn.data.remote.dto.OrderCreateDto
import com.nexora.vpn.data.remote.dto.RegisterRequestDto
import com.nexora.vpn.data.remote.dto.ResetPasswordRequestDto
import com.nexora.vpn.data.remote.dto.TopUpCreateDto
import com.nexora.vpn.domain.model.Device
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.PaymentMethods
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.TopUp
import com.nexora.vpn.domain.model.TopUpMethod
import com.nexora.vpn.domain.model.User
import com.nexora.vpn.domain.model.VpnConfig
import com.nexora.vpn.domain.model.Wallet
import com.nexora.vpn.domain.repository.AuthRepository
import com.nexora.vpn.domain.repository.ConfigRepository
import com.nexora.vpn.domain.repository.OrderRepository
import com.nexora.vpn.domain.repository.StoreRepository
import com.nexora.vpn.domain.repository.SubscriptionRepository
import com.nexora.vpn.domain.repository.UserRepository
import com.nexora.vpn.domain.repository.WalletRepository
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow

@Singleton
class AuthRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
    private val tokenStore: TokenStore,
    private val deviceIdentity: DeviceIdentity,
) : AuthRepository {

    override val isSignedIn: Flow<Boolean> = tokenStore.isSignedIn

    override suspend fun register(
        username: String,
        password: String,
        email: String?,
        phone: String?,
    ): Outcome<User> = ApiCall {
        api.register(
            RegisterRequestDto(
                username = username.trim().lowercase(),
                password = password,
                email = email?.trim()?.takeIf { it.isNotEmpty() },
                phone = phone?.trim()?.takeIf { it.isNotEmpty() },
            ),
        )
    }.map { it.toDomain() }

    override suspend fun login(
        identifier: String,
        password: String,
        replaceDevice: String?,
    ): Outcome<User> {
        val result = ApiCall {
            api.login(
                LoginRequestDto(
                    identifier = identifier.trim(),
                    password = password,
                    deviceId = deviceIdentity.deviceId(),
                    deviceName = deviceIdentity.deviceName(),
                    appVersion = deviceIdentity.appVersion(),
                    replaceDevice = replaceDevice,
                ),
            )
        }

        return when (result) {
            is Outcome.Success -> {
                val payload = result.data
                tokenStore.save(
                    accessToken = payload.tokens.accessToken,
                    refreshToken = payload.tokens.refreshToken,
                    expiresInSeconds = payload.tokens.expiresIn,
                    userId = payload.user.id,
                )
                Outcome.Success(payload.user.toDomain())
            }
            is Outcome.Failure -> result
        }
    }

    override suspend fun logout(allDevices: Boolean) {
        val refreshToken = tokenStore.refreshToken()

        // Cleared first, and regardless of what the server says. If the
        // network is down, the user still expects to be signed out — leaving
        // them signed in because a request failed would be worse than the
        // server keeping a token that expires on its own.
        tokenStore.clear()

        if (!refreshToken.isNullOrEmpty()) {
            runCatching {
                api.logout(LogoutRequestDto(refreshToken, allDevices))
            }
        }
    }

    override suspend fun requestPasswordReset(identifier: String): Outcome<Unit> =
        ApiCall { api.forgotPassword(ForgotPasswordRequestDto(identifier.trim())) }
            .map { }

    override suspend fun resetPassword(
        token: String,
        newPassword: String,
    ): Outcome<Unit> = ApiCall {
        api.resetPassword(ResetPasswordRequestDto(token, newPassword))
    }.map { }
}

@Singleton
class UserRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
    private val deviceIdentity: DeviceIdentity,
) : UserRepository {

    override suspend fun me(): Outcome<User> =
        ApiCall { api.me() }.map { it.toDomain() }

    override suspend fun updateContact(
        email: String?,
        phone: String?,
    ): Outcome<User> = ApiCall {
        api.updateMe(buildMap { put("email", email); put("phone", phone) })
    }.map { it.toDomain() }

    override suspend fun devices(): Outcome<List<Device>> {
        val current = deviceIdentity.deviceId()
        return ApiCall { api.devices() }.map { list ->
            list.map { it.toDomain(currentDeviceId = current) }
        }
    }

    override suspend fun revokeDevice(deviceId: String): Outcome<Unit> =
        ApiCall { api.revokeDevice(deviceId) }.map { }
}

@Singleton
class StoreRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
) : StoreRepository {

    // Plans change rarely and the store is the first screen a buyer sees, so a
    // short in-memory cache avoids a spinner on every visit.
    private var cached: List<Plan>? = null
    private var cachedAtMs = 0L

    private companion object {
        const val CACHE_TTL_MS = 5 * 60 * 1000L
    }

    override suspend fun plans(forceRefresh: Boolean): Outcome<List<Plan>> {
        val now = System.currentTimeMillis()
        val fresh = cached
        if (!forceRefresh && fresh != null && now - cachedAtMs < CACHE_TTL_MS) {
            return Outcome.Success(fresh)
        }

        val result = ApiCall { api.plans() }.map { list -> list.map { it.toDomain() } }
        if (result is Outcome.Success) {
            cached = result.data
            cachedAtMs = now
        } else if (fresh != null && result is Outcome.Failure && result.error.isRetryable) {
            // Offline with something cached: stale plans beat an empty store
            // (spec rule 47 — show the last valid data when it is safe to).
            return Outcome.Success(fresh)
        }
        return result
    }

    override suspend fun plan(planId: String): Outcome<Plan> =
        ApiCall { api.plan(planId) }.map { it.toDomain() }
}

@Singleton
class OrderRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
) : OrderRepository {

    override suspend fun createOrder(
        planId: String,
        idempotencyKey: String,
        subscriptionId: String?,
    ): Outcome<Order> = ApiCall {
        api.createOrder(OrderCreateDto(planId, idempotencyKey, subscriptionId))
    }.map { it.toDomain() }

    override suspend fun orders(): Outcome<List<Order>> =
        ApiCall { api.orders() }.map { list -> list.map { it.toDomain() } }

    override suspend fun order(orderId: String): Outcome<Order> =
        ApiCall { api.order(orderId) }.map { it.toDomain() }

    override suspend fun cancelOrder(orderId: String): Outcome<Order> =
        ApiCall { api.cancelOrder(orderId) }.map { it.toDomain() }
}

@Singleton
class SubscriptionRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
) : SubscriptionRepository {

    private var cached: List<Subscription>? = null

    override suspend fun subscriptions(forceRefresh: Boolean): Outcome<List<Subscription>> {
        val result = ApiCall { api.subscriptions() }
            .map { list -> list.map { it.toDomain() } }

        return when {
            result is Outcome.Success -> {
                cached = result.data
                result
            }
            // Traffic figures go stale, but showing the last known state beats
            // showing nothing when the user opens the app on a bad connection.
            result is Outcome.Failure && result.error.isRetryable && cached != null ->
                Outcome.Success(cached!!)
            else -> result
        }
    }

    override suspend fun subscription(subscriptionId: String): Outcome<Subscription> =
        ApiCall { api.subscription(subscriptionId) }.map { it.toDomain() }

    override suspend fun renew(
        subscriptionId: String,
        planId: String,
        idempotencyKey: String,
    ): Outcome<Order> = ApiCall {
        api.renewSubscription(
            subscriptionId,
            OrderCreateDto(planId, idempotencyKey, subscriptionId),
        )
    }.map { it.toDomain() }

    override suspend fun requestConfigRefresh(subscriptionId: String): Outcome<Unit> =
        ApiCall { api.refreshSubscriptionConfigs(subscriptionId) }.map { }
}

@Singleton
class ConfigRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
) : ConfigRepository {

    override suspend fun configs(subscriptionId: String?): Outcome<List<VpnConfig>> =
        ApiCall { api.configs(subscriptionId) }.map { list -> list.map { it.toDomain() } }

    override suspend fun activate(configId: String): Outcome<VpnConfig> =
        ApiCall { api.activateConfig(configId) }.map { it.toDomain() }

    override suspend fun delete(configId: String): Outcome<Unit> =
        ApiCall { api.deleteConfig(configId) }.map { }
}

@Singleton
class WalletRepositoryImpl @Inject constructor(
    private val api: NexoraApi,
) : WalletRepository {

    override suspend fun wallet(): Outcome<Wallet> =
        ApiCall { api.wallet() }.map { it.toDomain() }

    override suspend fun paymentMethods(): Outcome<PaymentMethods> =
        ApiCall { api.paymentMethods() }.map { it.toDomain() }

    override suspend fun topups(): Outcome<List<TopUp>> =
        ApiCall { api.topups() }.map { list -> list.map { it.toDomain() } }

    override suspend fun submitTopUp(
        method: TopUpMethod,
        amount: Long,
        reference: String,
        payerNote: String?,
        network: String?,
        asset: String?,
        orderId: String?,
    ): Outcome<TopUp> = ApiCall {
        api.createTopup(
            TopUpCreateDto(
                method = method.name,
                amount = amount.toString(),
                reference = reference,
                payerNote = payerNote?.trim()?.takeIf { it.isNotEmpty() },
                network = network,
                asset = asset,
                orderId = orderId,
            ),
        )
    }.map { it.toDomain() }

    override suspend fun cancelTopUp(topupId: String): Outcome<TopUp> =
        ApiCall { api.cancelTopup(topupId) }.map { it.toDomain() }

    override suspend fun payOrder(orderId: String): Outcome<Order> =
        ApiCall { api.payOrder(orderId) }.map { it.toDomain() }
}
