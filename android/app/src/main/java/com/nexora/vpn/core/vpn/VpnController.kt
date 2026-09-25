package com.nexora.vpn.core.vpn

import android.content.Context
import android.content.Intent
import android.net.VpnService
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.nexora.vpn.BuildConfig
import com.nexora.vpn.core.settings.AppRoutingMode
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.settings.RoutingMode
import com.nexora.vpn.core.stats.ConnectionLog
import com.nexora.vpn.core.stats.SessionRecord
import com.nexora.vpn.core.stats.UsageStore
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * Live numbers for the connected session. Speeds are bytes per second over
 * the last second; [downHistory] is the last minute of download speeds, for
 * the graph on Home.
 */
data class LiveTraffic(
    val downPerSecond: Long = 0,
    val upPerSecond: Long = 0,
    val sessionDown: Long = 0,
    val sessionUp: Long = 0,
    val downHistory: List<Long> = emptyList(),
)

/**
 * What the screens use to connect, disconnect and measure.
 *
 * The configuration never travels in an Intent: it holds the customer's
 * credential, and Intents are copied between processes and can surface in
 * logs. The service picks it up from here, in the same process.
 */
@Singleton
class VpnController @Inject constructor(
    @ApplicationContext private val context: Context,
    private val settings: AppSettings,
    private val usage: UsageStore,
    val log: ConnectionLog,
) {
    private val _state = MutableStateFlow<VpnState>(VpnState.Disconnected)
    val state: StateFlow<VpnState> = _state.asStateFlow()

    private val _traffic = MutableStateFlow(LiveTraffic())
    val traffic: StateFlow<LiveTraffic> = _traffic.asStateFlow()

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var meterJob: Job? = null

    /** Set by [connect], taken by the service. */
    @Volatile
    private var pending: PendingConnection? = null

    internal class PendingConnection(
        val serverName: String,
        val configJson: String,
        val appRouting: AppRoutingMode,
        val selectedApps: Set<String>,
    ) {
        override fun toString() = "PendingConnection($serverName)"
    }

    /**
     * The system dialog that lets this app create a VPN, or null when the
     * user has already allowed it. The screen must launch it and wait.
     */
    fun permissionIntent(): Intent? = VpnService.prepare(context)

    /**
     * Starts the tunnel for [configUri], with the customer's DNS, routing and
     * per-app choices as they are right now. The caller has already obtained
     * the VPN permission; if it has not, this reports
     * [VpnState.Reason.PERMISSION_DENIED].
     */
    fun connect(configUri: String, serverName: String) {
        log.info("Connecting to $serverName")
        if (permissionIntent() != null) {
            log.warning("VPN permission has not been granted")
            _state.value = VpnState.Failed(VpnState.Reason.PERMISSION_DENIED)
            return
        }
        val profile = try {
            ProxyUri.parse(configUri)
        } catch (e: UnsupportedProfileException) {
            log.error("This server's configuration is not supported: ${e.message}")
            _state.value = VpnState.Failed(VpnState.Reason.UNSUPPORTED_CONFIG, e.message)
            return
        }
        val prefs = settings.current()
        val json = XrayConfigBuilder.forTunnel(
            profile,
            XrayConfigBuilder.Options(
                bypassIran = prefs.routing == RoutingMode.BYPASS_IRAN,
                dnsServers = prefs.dnsServers,
                // The core logs destinations at "warning" and above. On a
                // customer's phone that is a browsing history in logcat.
                logLevel = if (BuildConfig.DEBUG) "warning" else "none",
            ),
        )
        log.info(
            "Protocol ${profile::class.simpleName?.lowercase()}, " +
                "routing ${prefs.routing.name.lowercase()}, DNS ${prefs.dnsServers.joinToString()}",
        )
        pending = PendingConnection(serverName, json, prefs.appRouting, prefs.selectedApps)
        _state.value = VpnState.Connecting(serverName)
        ContextCompat.startForegroundService(
            context,
            Intent(context, NexoraVpnService::class.java).setAction(NexoraVpnService.ACTION_CONNECT),
        )
    }

    fun disconnect() {
        when (_state.value) {
            VpnState.Disconnected, VpnState.Disconnecting -> return
            // Nothing is running after a failure; there is no service to stop,
            // and starting one just to stop it can be refused in the background.
            is VpnState.Failed -> {
                _state.value = VpnState.Disconnected
                return
            }
            else -> Unit
        }
        log.info("Disconnecting")
        _state.value = VpnState.Disconnecting
        context.startService(
            Intent(context, NexoraVpnService::class.java)
                .setAction(NexoraVpnService.ACTION_DISCONNECT),
        )
    }

    /**
     * Round trip through [configUri]'s server, in ms, or null when it did not
     * answer. Through the live tunnel when connected to that server,
     * otherwise directly.
     */
    suspend fun measureDelay(configUri: String, throughTunnel: Boolean = false): Int? =
        withContext(Dispatchers.IO) {
            val ms = if (throughTunnel && _state.value is VpnState.Connected) {
                XrayCore.measureRunningDelay()
            } else {
                val profile = runCatching { ProxyUri.parse(configUri) }.getOrNull()
                    ?: return@withContext null
                XrayCore.measureDelay(context, XrayConfigBuilder.forDelayTest(profile))
            }
            ms?.toInt()
        }

    // --- called by the service ----------------------------------------------------

    internal fun takePending(): PendingConnection? = pending.also { pending = null }

    internal fun report(state: VpnState) {
        val previous = _state.value
        _state.value = state
        when (state) {
            is VpnState.Connected -> {
                log.info("Connected to ${state.serverName}")
                startMeter(state)
            }
            is VpnState.Failed -> {
                log.error(
                    "Connection failed (${state.reason.name.lowercase()})" +
                        (state.detail?.let { ": $it" } ?: ""),
                )
                stopMeter()
            }
            VpnState.Disconnected -> {
                if (previous !is VpnState.Disconnected) log.info("Disconnected")
                stopMeter()
            }
            else -> Unit
        }
    }

    // --- traffic meter ---------------------------------------------------------------

    private val meterLock = Any()
    private var sessionServer: String? = null
    private var sessionStartedWallMs = 0L
    private var sessionStartedElapsed = 0L
    private var unflushedDown = 0L
    private var unflushedUp = 0L

    private fun startMeter(connected: VpnState.Connected) {
        meterJob?.cancel()
        synchronized(meterLock) {
            sessionServer = connected.serverName
            sessionStartedWallMs = System.currentTimeMillis()
            sessionStartedElapsed = connected.sinceElapsedMs
            unflushedDown = 0
            unflushedUp = 0
        }
        _traffic.value = LiveTraffic()
        meterJob = scope.launch {
            var lastFlush = SystemClock.elapsedRealtime()
            while (isActive) {
                delay(METER_INTERVAL_MS)
                val delta = TrafficCounters.parse(XrayCore.queryStats())
                val down = delta.proxyDown * 1000 / METER_INTERVAL_MS
                val up = delta.proxyUp * 1000 / METER_INTERVAL_MS
                val current = _traffic.value
                _traffic.value = current.copy(
                    downPerSecond = down,
                    upPerSecond = up,
                    sessionDown = current.sessionDown + delta.proxyDown,
                    sessionUp = current.sessionUp + delta.proxyUp,
                    downHistory = (current.downHistory + down).takeLast(HISTORY_POINTS),
                )
                synchronized(meterLock) {
                    unflushedDown += delta.proxyDown
                    unflushedUp += delta.proxyUp
                }
                // Written to disk every few seconds, not every tick.
                if (SystemClock.elapsedRealtime() - lastFlush >= FLUSH_INTERVAL_MS) {
                    flushUsage()
                    lastFlush = SystemClock.elapsedRealtime()
                }
            }
        }
    }

    private fun flushUsage() {
        val (down, up) = synchronized(meterLock) {
            val pair = unflushedDown to unflushedUp
            unflushedDown = 0
            unflushedUp = 0
            pair
        }
        usage.addTraffic(System.currentTimeMillis(), down, up)
    }

    /** Ends the meter and records the session in the local history. */
    private fun stopMeter() {
        val job = meterJob ?: return
        meterJob = null
        job.cancel()
        val server = synchronized(meterLock) { sessionServer.also { sessionServer = null } } ?: return
        val totals = _traffic.value
        val record = SessionRecord(
            serverName = server,
            startedAtMs = sessionStartedWallMs,
            durationMs = (SystemClock.elapsedRealtime() - sessionStartedElapsed).coerceAtLeast(0),
            bytesDown = totals.sessionDown,
            bytesUp = totals.sessionUp,
        )
        scope.launch(Dispatchers.IO) {
            flushUsage()
            usage.addSession(record)
        }
        _traffic.value = LiveTraffic()
    }

    private companion object {
        const val METER_INTERVAL_MS = 1_000L
        const val FLUSH_INTERVAL_MS = 10_000L
        const val HISTORY_POINTS = 60
    }
}
