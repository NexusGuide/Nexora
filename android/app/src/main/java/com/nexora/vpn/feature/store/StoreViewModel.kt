package com.nexora.vpn.feature.store

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.common.Payments
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.usecase.GetPlansUseCase
import com.nexora.vpn.domain.usecase.PurchaseAttempt
import com.nexora.vpn.domain.usecase.PurchasePlanUseCase
import com.nexora.vpn.domain.repository.WalletRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * An order waiting to be paid, with what the customer needs to decide how:
 * their balance, and the top-up limits if the balance falls short.
 */
data class Checkout(
    val order: Order,
    val planName: String,
    val balance: Long? = null,
    val minTopup: Long = 0,
    val maxTopup: Long = 0,
    val topUpAvailable: Boolean = false,
    val loading: Boolean = true,
    val paying: Boolean = false,
) {
    val canPayFromWallet: Boolean get() = balance != null && balance >= order.amount

    /** What to top up to cover this order; 0 when the balance already does. */
    val topUpAmount: Long
        get() = Payments.topUpFor(order.amount, balance ?: 0, minTopup, maxTopup)
}

data class StoreUiState(
    val plans: UiState<List<Plan>> = UiState.Loading,
    val purchasingPlanId: String? = null,
    val checkout: Checkout? = null,
    val paidOrder: Order? = null,
    val error: AppError? = null,
)

@HiltViewModel
class StoreViewModel @Inject constructor(
    private val getPlans: GetPlansUseCase,
    private val purchasePlan: PurchasePlanUseCase,
    private val wallet: WalletRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(StoreUiState())
    val state: StateFlow<StoreUiState> = _state.asStateFlow()

    /**
     * Held across retries, per plan.
     *
     * This is the whole point of the idempotency key: if the first attempt
     * times out and the user taps Buy again, the same key goes up and the
     * backend returns the original order rather than creating a second one.
     * Generating a fresh key per tap would defeat the protection entirely.
     */
    private val attempts = mutableMapOf<String, PurchaseAttempt>()

    init {
        load()
    }

    fun load(forceRefresh: Boolean = false) {
        viewModelScope.launch {
            _state.update { current ->
                val existing = current.plans
                current.copy(
                    plans = if (existing is UiState.Content) {
                        existing.copy(isRefreshing = true)
                    } else {
                        UiState.Loading
                    },
                )
            }

            when (val result = getPlans(forceRefresh)) {
                is Outcome.Success -> _state.update {
                    it.copy(
                        plans = if (result.data.isEmpty()) {
                            UiState.Empty
                        } else {
                            UiState.Content(result.data)
                        },
                    )
                }
                is Outcome.Failure -> _state.update {
                    it.copy(plans = UiState.Failed(result.error))
                }
            }
        }
    }

    fun buy(planId: String) {
        if (_state.value.purchasingPlanId != null) return

        // Reused if this plan was already attempted — that is what makes the
        // retry safe.
        val attempt = attempts.getOrPut(planId) { purchasePlan.newAttempt() }

        _state.update { it.copy(purchasingPlanId = planId, error = null) }

        viewModelScope.launch {
            when (val result = purchasePlan(attempt, planId)) {
                is Outcome.Success -> {
                    // The order exists; the key has done its job.
                    attempts.remove(planId)
                    val name = (_state.value.plans as? UiState.Content)?.data
                        ?.firstOrNull { it.id == planId }?.name.orEmpty()
                    _state.update {
                        it.copy(purchasingPlanId = null, checkout = Checkout(result.data, name))
                    }
                    loadCheckout()
                }
                is Outcome.Failure -> {
                    // Deliberately keeps the attempt, so a retry reuses the key.
                    _state.update {
                        it.copy(purchasingPlanId = null, error = result.error)
                    }
                }
            }
        }
    }

    private fun loadCheckout() {
        viewModelScope.launch {
            val w = async { wallet.wallet() }
            val m = async { wallet.paymentMethods() }
            val balance = w.await().getOrNull()?.balance
            val methods = m.await().getOrNull()
            _state.update { s ->
                s.copy(
                    checkout = s.checkout?.copy(
                        balance = balance,
                        minTopup = methods?.minTopup ?: 0,
                        maxTopup = methods?.maxTopup ?: 0,
                        topUpAvailable = methods?.hasAny == true,
                        loading = false,
                    ),
                )
            }
        }
    }

    fun payFromWallet() {
        val checkout = _state.value.checkout ?: return
        if (checkout.paying || !checkout.canPayFromWallet) return
        _state.update { it.copy(checkout = checkout.copy(paying = true)) }
        viewModelScope.launch {
            when (val result = wallet.payOrder(checkout.order.id)) {
                is Outcome.Success -> _state.update {
                    it.copy(checkout = null, paidOrder = result.data)
                }
                is Outcome.Failure -> _state.update {
                    it.copy(checkout = it.checkout?.copy(paying = false), error = result.error)
                }
            }
        }
    }

    fun dismissCheckout() = _state.update { it.copy(checkout = null) }

    fun dismissPaid() = _state.update { it.copy(paidOrder = null) }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun refresh() = load(forceRefresh = true)
}
