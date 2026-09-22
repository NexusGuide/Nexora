package com.nexora.vpn.domain.usecase

import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.common.Validation
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus
import com.nexora.vpn.domain.model.User
import com.nexora.vpn.domain.repository.AuthRepository
import com.nexora.vpn.domain.repository.OrderRepository
import com.nexora.vpn.domain.repository.StoreRepository
import com.nexora.vpn.domain.repository.SubscriptionRepository
import java.util.UUID
import javax.inject.Inject

/**
 * Use cases: the operations a screen performs, with the rules that belong to
 * the operation rather than to any one screen.
 *
 * A ViewModel could call a repository directly; these exist where there is a
 * rule worth stating once — validating before a network round trip, or
 * generating an idempotency key correctly.
 */

class SignInUseCase @Inject constructor(
    private val authRepository: AuthRepository,
) {
    suspend operator fun invoke(identifier: String, password: String): Outcome<User> {
        // Checked locally first: a round trip to be told the field is empty is
        // a slow way to say something instant.
        if (Validation.loginIdentifier(identifier) is Validation.Check.Invalid) {
            return Outcome.Failure(
                AppError.Rejected("VALIDATION_ERROR", "Enter your username or email"),
            )
        }
        if (password.isEmpty()) {
            return Outcome.Failure(
                AppError.Rejected("VALIDATION_ERROR", "Enter your password"),
            )
        }
        return authRepository.login(identifier, password)
    }
}

class RegisterUseCase @Inject constructor(
    private val authRepository: AuthRepository,
) {
    suspend operator fun invoke(
        username: String,
        password: String,
        email: String?,
        phone: String?,
    ): Outcome<User> {
        Validation.username(username).let {
            if (it is Validation.Check.Invalid) {
                return Outcome.Failure(
                    AppError.Rejected("VALIDATION_ERROR", null, mapOf("field" to "username")),
                )
            }
        }
        Validation.password(password).let {
            if (it is Validation.Check.Invalid) {
                return Outcome.Failure(
                    AppError.Rejected("VALIDATION_ERROR", null, mapOf("field" to "password")),
                )
            }
        }
        return authRepository.register(username, password, email, phone)
    }
}

/**
 * Buying a plan.
 *
 * The idempotency key is generated **here**, once per purchase attempt, and
 * handed to the repository. If the ViewModel generated it per call, a retry
 * after a timeout would carry a new key and the backend would create a second
 * order — the exact double-charge the backend's unique constraint exists to
 * prevent. The key has to survive the retry for that protection to work.
 */
class PurchasePlanUseCase @Inject constructor(
    private val orderRepository: OrderRepository,
) {
    fun newAttempt(): PurchaseAttempt = PurchaseAttempt(UUID.randomUUID().toString())

    suspend operator fun invoke(attempt: PurchaseAttempt, planId: String): Outcome<Order> =
        orderRepository.createOrder(planId, attempt.idempotencyKey)
}

/**
 * One checkout, retryable. Hold this across retries; make a new one only when
 * the user deliberately starts a fresh purchase.
 */
@JvmInline
value class PurchaseAttempt(val idempotencyKey: String)

class RenewSubscriptionUseCase @Inject constructor(
    private val subscriptionRepository: SubscriptionRepository,
) {
    fun newAttempt(): PurchaseAttempt = PurchaseAttempt(UUID.randomUUID().toString())

    suspend operator fun invoke(
        attempt: PurchaseAttempt,
        subscriptionId: String,
        planId: String,
    ): Outcome<Order> = subscriptionRepository.renew(
        subscriptionId = subscriptionId,
        planId = planId,
        idempotencyKey = attempt.idempotencyKey,
    )
}

/**
 * What the home screen shows: the subscription the user actually depends on.
 *
 * "Most relevant" is an active one, preferring the one expiring soonest —
 * that is the one they need to act on. With none active, the most recent of
 * any status, so the screen can explain the state rather than look empty.
 */
class GetPrimarySubscriptionUseCase @Inject constructor(
    private val subscriptionRepository: SubscriptionRepository,
) {
    suspend operator fun invoke(forceRefresh: Boolean = false): Outcome<Subscription?> {
        val result = subscriptionRepository.subscriptions(forceRefresh)
        return when (result) {
            is Outcome.Failure -> result
            is Outcome.Success -> Outcome.Success(pick(result.data))
        }
    }

    internal fun pick(subscriptions: List<Subscription>): Subscription? {
        val active = subscriptions.filter { it.status == SubscriptionStatus.ACTIVE }
        if (active.isNotEmpty()) {
            return active.minByOrNull { it.expireAtEpochMs ?: Long.MAX_VALUE }
        }
        val pending = subscriptions.filter { it.status == SubscriptionStatus.PENDING }
        if (pending.isNotEmpty()) return pending.first()
        return subscriptions.firstOrNull()
    }
}

class GetPlansUseCase @Inject constructor(
    private val storeRepository: StoreRepository,
) {
    suspend operator fun invoke(forceRefresh: Boolean = false): Outcome<List<Plan>> =
        storeRepository.plans(forceRefresh)
}

class SignOutUseCase @Inject constructor(
    private val authRepository: AuthRepository,
) {
    suspend operator fun invoke(allDevices: Boolean = false) =
        authRepository.logout(allDevices)
}
