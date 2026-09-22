package com.nexora.vpn.domain.repository

import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.domain.model.Device
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.User
import com.nexora.vpn.domain.model.VpnConfig
import kotlinx.coroutines.flow.Flow

/**
 * What the domain needs from the outside world.
 *
 * Declared here, implemented in `data` — so a use case depends on the
 * interface and a test can substitute a fake without a network or a database.
 */

interface AuthRepository {
    val isSignedIn: Flow<Boolean>

    suspend fun register(
        username: String,
        password: String,
        email: String?,
        phone: String?,
    ): Outcome<User>

    suspend fun login(identifier: String, password: String): Outcome<User>

    suspend fun logout(allDevices: Boolean = false)

    suspend fun requestPasswordReset(identifier: String): Outcome<Unit>

    suspend fun resetPassword(token: String, newPassword: String): Outcome<Unit>
}

interface UserRepository {
    suspend fun me(): Outcome<User>
    suspend fun updateContact(email: String?, phone: String?): Outcome<User>
    suspend fun devices(): Outcome<List<Device>>
    suspend fun revokeDevice(deviceId: String): Outcome<Unit>
}

interface StoreRepository {
    suspend fun plans(forceRefresh: Boolean = false): Outcome<List<Plan>>
    suspend fun plan(planId: String): Outcome<Plan>
}

interface OrderRepository {
    /**
     * [idempotencyKey] must be generated once per checkout attempt and reused
     * across retries — that is what stops a flaky connection from charging the
     * customer twice.
     */
    suspend fun createOrder(
        planId: String,
        idempotencyKey: String,
        subscriptionId: String? = null,
    ): Outcome<Order>

    suspend fun orders(): Outcome<List<Order>>
    suspend fun order(orderId: String): Outcome<Order>
    suspend fun cancelOrder(orderId: String): Outcome<Order>
}

interface SubscriptionRepository {
    suspend fun subscriptions(forceRefresh: Boolean = false): Outcome<List<Subscription>>
    suspend fun subscription(subscriptionId: String): Outcome<Subscription>

    suspend fun renew(
        subscriptionId: String,
        planId: String,
        idempotencyKey: String,
    ): Outcome<Order>

    /** Asks the backend to re-fetch configs. Returns once queued, not once done. */
    suspend fun requestConfigRefresh(subscriptionId: String): Outcome<Unit>
}

interface ConfigRepository {
    suspend fun configs(subscriptionId: String? = null): Outcome<List<VpnConfig>>
    suspend fun activate(configId: String): Outcome<VpnConfig>
    suspend fun delete(configId: String): Outcome<Unit>
}
