package com.nexora.vpn.feature.servers

import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.vpn.ServerInfo
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.VpnConfig
import com.nexora.vpn.domain.repository.ConfigRepository
import com.nexora.vpn.domain.usecase.GetPrimarySubscriptionUseCase
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit

/** A config as the server list shows it. */
data class Server(val config: VpnConfig, val info: ServerInfo) {
    val id: String get() = config.id
}

/** Result of a delay test: measuring, a time in ms, or no answer. */
sealed interface Ping {
    data object Measuring : Ping
    data class Ms(val value: Int) : Ping
    data object Timeout : Ping
}

data class CatalogState(
    val subscription: Subscription? = null,
    val servers: List<Server> = emptyList(),
    val isLoading: Boolean = true,
    val error: AppError? = null,
    val pings: Map<String, Ping> = emptyMap(),
)

/**
 * The servers of the customer's subscription, shared by Home and the server
 * list so both show the same selection and the same pings.
 *
 * "Servers" are the configs the panel issued — there is no separate server
 * directory behind them, so nothing here is invented: country and region come
 * from the flag in each config's name, and ping is measured on the phone.
 */
@Singleton
class ServerCatalog @Inject constructor(
    private val getPrimarySubscription: GetPrimarySubscriptionUseCase,
    private val configRepository: ConfigRepository,
    private val settings: AppSettings,
    private val vpn: VpnController,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val _state = MutableStateFlow(CatalogState())
    val state: StateFlow<CatalogState> = _state.asStateFlow()

    /** At most this many delay tests at once, so a long list does not flood the network. */
    private val pingLimit = Semaphore(4)

    suspend fun load(forceRefresh: Boolean = false) {
        _state.update { it.copy(isLoading = true, error = null) }
        when (val sub = getPrimarySubscription(forceRefresh)) {
            is Outcome.Failure -> _state.update { it.copy(isLoading = false, error = sub.error) }
            is Outcome.Success -> {
                val subscription = sub.data
                if (subscription == null || !subscription.isUsable) {
                    _state.update {
                        it.copy(subscription = subscription, servers = emptyList(), isLoading = false)
                    }
                    return
                }
                when (val configs = configRepository.configs(subscription.id)) {
                    is Outcome.Failure -> _state.update {
                        it.copy(subscription = subscription, isLoading = false, error = configs.error)
                    }
                    is Outcome.Success -> _state.update {
                        it.copy(
                            subscription = subscription,
                            servers = configs.data.map { c -> Server(c, ServerInfo.from(c.name)) },
                            isLoading = false,
                        )
                    }
                }
            }
        }
    }

    /**
     * The server Home connects to: the one the customer picked here, else the
     * one the backend marks active, else the first.
     */
    fun selected(state: CatalogState = _state.value, selectedId: String? = settings.current().selectedServerId): Server? =
        state.servers.firstOrNull { it.id == selectedId }
            ?: state.servers.firstOrNull { it.config.isActive }
            ?: state.servers.firstOrNull()

    /** Remembers the choice, tells the backend, and moves a live tunnel over. */
    fun select(server: Server) {
        settings.setSelectedServer(server.id)
        scope.launch { configRepository.activate(server.id) }
        val connected = vpn.state.value
        if (connected is com.nexora.vpn.core.vpn.VpnState.Connected &&
            connected.serverName != server.config.name
        ) {
            vpn.connect(server.config.configData, server.config.name)
        }
    }

    fun pingAll() {
        val servers = _state.value.servers
        _state.update { s -> s.copy(pings = servers.associate { it.id to Ping.Measuring }) }
        scope.launch {
            servers.map { server -> async { pingLimit.withPermit { measure(server) } } }.awaitAll()
        }
    }

    fun ping(server: Server) {
        _state.update { s -> s.copy(pings = s.pings + (server.id to Ping.Measuring)) }
        scope.launch { measure(server) }
    }

    private suspend fun measure(server: Server) {
        val ms = vpn.measureDelay(server.config.configData)
        _state.update { s ->
            s.copy(pings = s.pings + (server.id to (ms?.let { Ping.Ms(it) } ?: Ping.Timeout)))
        }
    }
}
