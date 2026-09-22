package com.nexora.vpn.feature.services

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus
import com.nexora.vpn.domain.repository.SubscriptionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

enum class ServicesTab { ACTIVE, EXPIRED, SUSPENDED }

data class ServicesUiState(
    val subscriptions: UiState<List<Subscription>> = UiState.Loading,
    val tab: ServicesTab = ServicesTab.ACTIVE,
    val refreshQueuedFor: String? = null,
    val error: AppError? = null,
) {
    private val all: List<Subscription>
        get() = (subscriptions as? UiState.Content)?.data.orEmpty()

    /** PENDING appears under Active: the user bought it and it is on its way,
     *  so hiding it would look like the purchase vanished. */
    val visible: List<Subscription>
        get() = when (tab) {
            ServicesTab.ACTIVE -> all.filter {
                it.status == SubscriptionStatus.ACTIVE ||
                    it.status == SubscriptionStatus.PENDING
            }
            ServicesTab.EXPIRED -> all.filter {
                it.status == SubscriptionStatus.EXPIRED ||
                    it.status == SubscriptionStatus.CANCELLED
            }
            ServicesTab.SUSPENDED -> all.filter {
                it.status == SubscriptionStatus.SUSPENDED
            }
        }

    fun countFor(tab: ServicesTab): Int = when (tab) {
        ServicesTab.ACTIVE -> all.count {
            it.status == SubscriptionStatus.ACTIVE ||
                it.status == SubscriptionStatus.PENDING
        }
        ServicesTab.EXPIRED -> all.count {
            it.status == SubscriptionStatus.EXPIRED ||
                it.status == SubscriptionStatus.CANCELLED
        }
        ServicesTab.SUSPENDED -> all.count { it.status == SubscriptionStatus.SUSPENDED }
    }
}

@HiltViewModel
class ServicesViewModel @Inject constructor(
    private val subscriptionRepository: SubscriptionRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ServicesUiState())
    val state: StateFlow<ServicesUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun selectTab(tab: ServicesTab) = _state.update { it.copy(tab = tab) }

    fun load(forceRefresh: Boolean = false) {
        viewModelScope.launch {
            _state.update { current ->
                val existing = current.subscriptions
                current.copy(
                    subscriptions = if (existing is UiState.Content) {
                        existing.copy(isRefreshing = true)
                    } else {
                        UiState.Loading
                    },
                )
            }

            when (val result = subscriptionRepository.subscriptions(forceRefresh)) {
                is Outcome.Success -> _state.update {
                    it.copy(
                        subscriptions = if (result.data.isEmpty()) {
                            UiState.Empty
                        } else {
                            UiState.Content(result.data)
                        },
                    )
                }
                is Outcome.Failure -> _state.update {
                    it.copy(subscriptions = UiState.Failed(result.error))
                }
            }
        }
    }

    /**
     * Asks the backend to re-fetch configs from the panel.
     *
     * The response only means "queued" — the worker does the panel round trip.
     * The UI says so rather than implying the configs are already new.
     */
    fun requestConfigRefresh(subscriptionId: String) {
        viewModelScope.launch {
            when (val result = subscriptionRepository.requestConfigRefresh(subscriptionId)) {
                is Outcome.Success ->
                    _state.update { it.copy(refreshQueuedFor = subscriptionId) }
                is Outcome.Failure ->
                    _state.update { it.copy(error = result.error) }
            }
        }
    }

    fun dismissRefreshNotice() = _state.update { it.copy(refreshQueuedFor = null) }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun refresh() = load(forceRefresh = true)
}
