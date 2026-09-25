package com.nexora.vpn.feature.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.vpn.LiveTraffic
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.core.vpn.VpnState
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus
import com.nexora.vpn.feature.servers.CatalogState
import com.nexora.vpn.feature.servers.Ping
import com.nexora.vpn.feature.servers.Server
import com.nexora.vpn.feature.servers.ServerCatalog
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Connection state as the Home screen draws it, derived from [VpnState]. */
enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED, DISCONNECTING, ERROR }

data class HomeUiState(
    val catalog: CatalogState = CatalogState(),
    val server: Server? = null,
    val connection: ConnectionState = ConnectionState.DISCONNECTED,
    /** `elapsedRealtime` at connection, for the session timer. */
    val connectedSinceMs: Long? = null,
    val traffic: LiveTraffic = LiveTraffic(),
    /** Through the tunnel once connected; otherwise the last direct test. */
    val pingMs: Int? = null,
    val vpnError: VpnState.Reason? = null,
    val vpnErrorDetail: String? = null,
    val confirmDisconnect: Boolean = false,
) {
    val subscription: Subscription? get() = catalog.subscription
    val isProvisioning: Boolean get() = subscription?.status == SubscriptionStatus.PENDING
    val hasUsableService: Boolean get() = subscription?.isUsable == true
}

@HiltViewModel
class HomeViewModel @Inject constructor(
    private val catalog: ServerCatalog,
    private val vpn: VpnController,
    private val settings: AppSettings,
) : ViewModel() {

    private val _state = MutableStateFlow(HomeUiState())
    val state: StateFlow<HomeUiState> = _state.asStateFlow()

    /** The tunnel's own delay, measured once per connection. */
    private var tunnelPing: Int? = null

    init {
        viewModelScope.launch {
            combine(catalog.state, settings.settings) { cat, prefs ->
                cat to catalog.selected(cat, prefs.selectedServerId)
            }.collect { (cat, server) ->
                _state.update {
                    val directPing = (server?.let { s -> cat.pings[s.id] } as? Ping.Ms)?.value
                    it.copy(
                        catalog = cat,
                        server = server,
                        pingMs = if (it.connection == ConnectionState.CONNECTED) tunnelPing else directPing,
                    )
                }
            }
        }
        viewModelScope.launch {
            vpn.state.collect { vpnState ->
                _state.update { it.withVpn(vpnState) }
                if (vpnState is VpnState.Connected) measureTunnelPing()
                if (vpnState is VpnState.Disconnected) tunnelPing = null
            }
        }
        viewModelScope.launch {
            vpn.traffic.collect { t -> _state.update { it.copy(traffic = t) } }
        }
        viewModelScope.launch {
            catalog.load()
            catalog.selected()?.let(catalog::ping)
            maybeAutoConnect()
        }
    }

    private fun HomeUiState.withVpn(vpnState: VpnState): HomeUiState = when (vpnState) {
        VpnState.Disconnected -> copy(connection = ConnectionState.DISCONNECTED, connectedSinceMs = null)
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
     * "Auto connect" in Settings: connect once when the app opens, if the
     * customer asked for it and Android has already allowed the VPN. Never
     * shows the permission dialog on its own.
     */
    private var autoConnectTried = false

    private fun maybeAutoConnect() {
        if (autoConnectTried) return
        autoConnectTried = true
        // Read from the catalog directly: this runs right after loading, before
        // the UI state has necessarily caught up.
        val server = catalog.selected() ?: return
        val usable = catalog.state.value.subscription?.isUsable == true
        if (settings.current().autoConnect &&
            vpn.state.value is VpnState.Disconnected &&
            usable &&
            vpn.permissionIntent() == null
        ) {
            vpn.log.info("Auto connect")
            vpn.connect(server.config.configData, server.config.name)
        }
    }

    fun permissionIntent() = vpn.permissionIntent()

    /** The power button. Disconnecting asks first, as in the design. */
    fun onPowerPressed() {
        when (_state.value.connection) {
            ConnectionState.CONNECTED -> _state.update { it.copy(confirmDisconnect = true) }
            ConnectionState.CONNECTING -> vpn.disconnect()
            ConnectionState.DISCONNECTING -> Unit
            ConnectionState.DISCONNECTED, ConnectionState.ERROR -> connect()
        }
    }

    fun connect() {
        val server = _state.value.server ?: return
        vpn.connect(server.config.configData, server.config.name)
    }

    fun confirmDisconnect() {
        _state.update { it.copy(confirmDisconnect = false) }
        vpn.disconnect()
    }

    fun cancelDisconnect() = _state.update { it.copy(confirmDisconnect = false) }

    fun onPermissionDenied() =
        _state.update { it.copy(vpnError = VpnState.Reason.PERMISSION_DENIED, vpnErrorDetail = null) }

    fun dismissVpnError() = _state.update { it.copy(vpnError = null, vpnErrorDetail = null) }

    fun refresh() {
        viewModelScope.launch { catalog.load(forceRefresh = true) }
    }

    private fun measureTunnelPing() {
        val server = _state.value.server ?: return
        viewModelScope.launch {
            _state.update { it.copy(pingMs = null) }
            tunnelPing = vpn.measureDelay(server.config.configData, throughTunnel = true)
            _state.update { it.copy(pingMs = tunnelPing) }
        }
    }
}
