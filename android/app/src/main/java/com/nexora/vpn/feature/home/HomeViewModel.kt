package com.nexora.vpn.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.core.vpn.VpnState
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

/** Connection state as the Home screen draws it, derived from [VpnState]. */
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING, ERROR }

data class HomeUiState(
    val subscription: UiState<Subscription?> = UiState.Loading,
    val activeConfig: VpnConfig? = null,
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    /** `elapsedRealtime` at connection, for the session timer. */
    val connectedSinceMs: Long? = null,
    val pingMs: Int? = null,
    val vpnError: VpnState.Reason? = null,
    val vpnErrorDetail: String? = null,
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
    private val vpn: VpnController,
) : ViewModel() {

    private val _state = MutableStateFlow(HomeUiState())
    val state: StateFlow<HomeUiState> = _state.asStateFlow()

    init {
        load()
        viewModelScope.launch {
            vpn.state.collect { vpnState ->
                _state.update { it.withVpn(vpnState) }
                // Once connected, the delay that matters is through the tunnel.
                if (vpnState is VpnState.Connected) measurePing()
            }
        }
    }

    private fun HomeUiState.withVpn(vpnState: VpnState): HomeUiState = when (vpnState) {
        VpnState.Disconnected -> copy(
            connection = ConnectionState.DISCONNECTED,
            connectedSinceMs = null,
        )
        is VpnState.Connecting -> copy(connection = ConnectionState.CONNECTING, vpnError = null)
        is VpnState.Connected -> copy(
            connection = ConnectionState.CONNECTED,
            connectedSinceMs = vpnState.sinceElapsedMs,
            vpnError = null,
        )
        VpnState.Disconnecting -> copy(connection = ConnectionState.DISCONNECTING)
        is VpnState.Failed -> copy(
            connection = ConnectionState.ERROR,
            connectedSinceMs = null,
            vpnError = vpnState.reason,
            vpnErrorDetail = vpnState.detail,
        )
    }

    /**
     * The permission dialog to show before connecting, or null when Nexora
     * already may create a VPN. The screen launches it and reports back.
     */
    fun permissionIntent() = vpn.permissionIntent()

    /** Connect or disconnect, depending on where the tunnel is. */
    fun toggleConnection() {
        when (_state.value.connection) {
            ConnectionState.CONNECTED, ConnectionState.CONNECTING -> vpn.disconnect()
            ConnectionState.DISCONNECTING -> Unit
            ConnectionState.DISCONNECTED, ConnectionState.ERROR -> {
                val config = _state.value.activeConfig ?: return
                vpn.connect(config.configData, config.name)
            }
        }
    }

    fun onPermissionDenied() =
        _state.update { it.copy(vpnError = VpnState.Reason.PERMISSION_DENIED) }

    fun dismissVpnError() = _state.update { it.copy(vpnError = null, vpnErrorDetail = null) }

    private fun measurePing() {
        val config = _state.value.activeConfig ?: return
        viewModelScope.launch {
            _state.update { it.copy(pingMs = null) }
            val ms = vpn.measureDelay(config.configData)
            _state.update { it.copy(pingMs = ms) }
        }
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
            is Outcome.Success -> {
                _state.update { current ->
                    current.copy(
                        activeConfig = result.data.firstOrNull { it.isActive }
                            ?: result.data.firstOrNull(),
                    )
                }
                measurePing()
            }
            // A config fetch failure is not worth an error screen: the
            // subscription details above it are still useful.
            is Outcome.Failure -> Unit
        }
    }

    fun refresh() = load(forceRefresh = true)
}
