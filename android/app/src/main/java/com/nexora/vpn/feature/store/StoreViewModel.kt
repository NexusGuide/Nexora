package com.nexora.vpn.feature.store

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.Order
import com.nexora.vpn.domain.model.Plan
import com.nexora.vpn.domain.usecase.GetPlansUseCase
import com.nexora.vpn.domain.usecase.PurchaseAttempt
import com.nexora.vpn.domain.usecase.PurchasePlanUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class StoreUiState(
    val plans: UiState<List<Plan>> = UiState.Loading,
    val purchasingPlanId: String? = null,
    val createdOrder: Order? = null,
    val error: AppError? = null,
)

@HiltViewModel
class StoreViewModel @Inject constructor(
    private val getPlans: GetPlansUseCase,
    private val purchasePlan: PurchasePlanUseCase,
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
                    _state.update {
                        it.copy(purchasingPlanId = null, createdOrder = result.data)
                    }
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

    fun dismissOrder() = _state.update { it.copy(createdOrder = null) }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun refresh() = load(forceRefresh = true)
}
