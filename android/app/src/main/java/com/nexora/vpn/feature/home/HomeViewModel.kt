package com.nexora.vpn.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus
import com.nexora.vpn.domain.model.VpnConfig
import com.nexora.vpn.domain.repository.ConfigRepository
import com.nexora.vpn.domain.usecase.GetPrimarySubscriptionUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Connection state as the UI understands it.
 *
 * Declared now, with only DISCONNECTED reachable, so the Home screen is built
 * against the real shape from the start. Phase 5 supplies a VpnController that
 * drives the rest — nothing here pretends to connect in the meantime.
 */
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING, ERROR }

data class HomeUiState(
    val subscription: UiState<Subscription?> = UiState.Loading,
    val activeConfig: VpnConfig? = null,
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    val sessionSeconds: Long = 0,
    val pingMs: Int? = null,
) {
    val primary: Subscription?
        get() = (subscription as? UiState.Content)?.data

    val isProvisioning: Boolean
        get() = primary?.status == SubscriptionStatus.PENDING

    val hasUsableService: Boolean
        get() = primary?.isUsable == true
}

@HiltViewModel
class HomeViewModel @Inject constructor(
    private val getPrimarySubscription: GetPrimarySubscriptionUseCase,
    private val configRepository: ConfigRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(HomeUiState())
    val state: StateFlow<HomeUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun load(forceRefresh: Boolean = false) {
        viewModelScope.launch {
            // A refresh keeps the current content visible rather than dropping
            // back to a spinner — the numbers on screen are still true.
            _state.update { current ->
                val existing = current.subscription
                current.copy(
                    subscription = if (existing is UiState.Content) {
                        existing.copy(isRefreshing = true)
                    } else {
                        UiState.Loading
                    },
                )
            }

            when (val result = getPrimarySubscription(forceRefresh)) {
                is Outcome.Success -> {
                    val subscription = result.data
                    _state.update {
                        it.copy(
                            subscription = if (subscription == null) {
                                UiState.Empty
                            } else {
                                UiState.Content(subscription)
                            },
                        )
                    }
                    if (subscription != null && subscription.isUsable) {
                        loadActiveConfig(subscription.id)
                    }
                }

                is Outcome.Failure -> _state.update { current ->
                    current.copy(
                        subscription = UiState.Failed(
                            error = result.error,
                            cached = current.primary,
                        ),
                    )
                }
            }
        }
    }

    private suspend fun loadActiveConfig(subscriptionId: String) {
        when (val result = configRepository.configs(subscriptionId)) {
            is Outcome.Success ->
                _state.update { current ->
                    current.copy(
                        activeConfig = result.data.firstOrNull { it.isActive }
                            ?: result.data.firstOrNull(),
                    )
                }
            // A config fetch failure is not worth an error screen: the
            // subscription details above it are still useful.
            is Outcome.Failure -> Unit
        }
    }

    fun refresh() = load(forceRefresh = true)
}
