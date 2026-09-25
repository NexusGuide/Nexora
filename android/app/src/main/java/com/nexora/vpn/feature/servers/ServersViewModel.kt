package com.nexora.vpn.feature.servers

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.vpn.ProxyProfile
import com.nexora.vpn.core.vpn.ProxyUri
import com.nexora.vpn.core.vpn.Security
import com.nexora.vpn.core.vpn.ServerInfo
import com.nexora.vpn.core.vpn.Transport
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.core.vpn.VpnState
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/** "All", "Favourites", or a region. */
sealed interface ServerFilter {
    data object All : ServerFilter
    data object Favourites : ServerFilter
    data class InRegion(val region: ServerInfo.Region) : ServerFilter
}

data class ServersUiState(
    val catalog: CatalogState = CatalogState(),
    val query: String = "",
    val filter: ServerFilter = ServerFilter.All,
    val favourites: Set<String> = emptySet(),
    val selectedId: String? = null,
    /** The server the tunnel is on right now, if any. */
    val connectedName: String? = null,
    /** Only regions that actually have a server, so no chip leads to an empty list. */
    val regions: List<ServerInfo.Region> = emptyList(),
    val visible: List<Server> = emptyList(),
)

/** The technical facts of a config, read from its share link. */
data class ServerSpec(
    val protocol: String,
    val transport: String,
    val security: String,
    val port: Int,
)

@HiltViewModel
class ServersViewModel @Inject constructor(
    private val catalog: ServerCatalog,
    private val settings: AppSettings,
    private val vpn: VpnController,
) : ViewModel() {

    private val query = MutableStateFlow("")
    private val filter = MutableStateFlow<ServerFilter>(ServerFilter.All)

    val state: StateFlow<ServersUiState> = combine(
        catalog.state,
        settings.settings,
        query,
        filter,
        vpn.state,
    ) { cat, prefs, q, f, vpnState ->
        val selected = catalog.selected(cat, prefs.selectedServerId)
        val needle = q.trim().lowercase()
        val visible = cat.servers.filter { s ->
            val matchesQuery = needle.isEmpty() ||
                s.config.name.lowercase().contains(needle) ||
                (s.info.countryCode?.lowercase() == needle)
            val matchesFilter = when (f) {
                ServerFilter.All -> true
                ServerFilter.Favourites -> s.id in prefs.favouriteServers
                is ServerFilter.InRegion -> s.info.region == f.region
            }
            matchesQuery && matchesFilter
        }
        ServersUiState(
            catalog = cat,
            query = q,
            filter = f,
            favourites = prefs.favouriteServers,
            selectedId = selected?.id,
            connectedName = (vpnState as? VpnState.Connected)?.serverName,
            regions = cat.servers.map { it.info.region }.distinct().sortedBy { it.ordinal },
            visible = visible,
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), ServersUiState())

    init {
        viewModelScope.launch {
            if (catalog.state.value.servers.isEmpty()) catalog.load()
            if (catalog.state.value.pings.isEmpty()) catalog.pingAll()
        }
    }

    fun onQuery(value: String) {
        query.value = value
    }

    fun onFilter(value: ServerFilter) {
        filter.value = value
    }

    fun select(server: Server) = catalog.select(server)
    fun toggleFavourite(server: Server) = settings.toggleFavourite(server.id)
    fun pingAll() = catalog.pingAll()
    fun ping(server: Server) = catalog.ping(server)

    fun refresh() {
        viewModelScope.launch {
            catalog.load(forceRefresh = true)
            catalog.pingAll()
        }
    }

    fun find(id: String): Server? = catalog.state.value.servers.firstOrNull { it.id == id }

    /** Connects to this server now (after selecting it). */
    fun connect(server: Server) {
        catalog.select(server)
        vpn.connect(server.config.configData, server.config.name)
    }

    fun permissionIntent() = vpn.permissionIntent()

    companion object {
        /** Parsed from the link itself; null when the link is one the app cannot run. */
        fun specOf(server: Server): ServerSpec? {
            val profile = runCatching { ProxyUri.parse(server.config.configData) }.getOrNull()
                ?: return null
            val protocol = when (profile) {
                is ProxyProfile.Vless -> "VLESS"
                is ProxyProfile.Vmess -> "VMess"
                is ProxyProfile.Trojan -> "Trojan"
                is ProxyProfile.Shadowsocks -> "Shadowsocks"
            }
            val transport = when (val t = profile.transport) {
                is Transport.Tcp -> if (t.httpHost != null || t.httpPath != null) "TCP (HTTP)" else "TCP"
                is Transport.Ws -> "WebSocket"
                is Transport.Grpc -> "gRPC"
                is Transport.HttpUpgrade -> "HTTPUpgrade"
                is Transport.Xhttp -> "XHTTP"
            }
            val security = when (profile.security) {
                Security.None -> if (profile is ProxyProfile.Shadowsocks) "AEAD" else "—"
                is Security.Tls -> "TLS"
                is Security.Reality -> "REALITY"
            }
            return ServerSpec(protocol, transport, security, profile.port)
        }
    }
}
