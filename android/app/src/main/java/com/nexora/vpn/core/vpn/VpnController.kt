package com.nexora.vpn.core.vpn

import android.content.Context
import android.content.Intent
import android.net.VpnService
import androidx.core.content.ContextCompat
import com.nexora.vpn.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext

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
) {
    private val _state = MutableStateFlow<VpnState>(VpnState.Disconnected)
    val state: StateFlow<VpnState> = _state.asStateFlow()

    /** Set by [connect], taken by the service. */
    @Volatile
    private var pending: PendingConnection? = null

    internal class PendingConnection(val serverName: String, val configJson: String) {
        override fun toString() = "PendingConnection($serverName)"
    }

    /**
     * The system dialog that lets this app create a VPN, or null when the
     * user has already allowed it. The screen must launch it and wait.
     */
    fun permissionIntent(): Intent? = VpnService.prepare(context)

    /**
     * Starts the tunnel for [configUri]. The caller has already obtained the
     * VPN permission; if it has not, this reports [VpnState.Reason.PERMISSION_DENIED].
     */
    fun connect(configUri: String, serverName: String, bypassIran: Boolean = true) {
        if (permissionIntent() != null) {
            _state.value = VpnState.Failed(VpnState.Reason.PERMISSION_DENIED)
            return
        }
        val profile = try {
            ProxyUri.parse(configUri)
        } catch (e: UnsupportedProfileException) {
            _state.value = VpnState.Failed(VpnState.Reason.UNSUPPORTED_CONFIG)
            return
        }
        val json = XrayConfigBuilder.forTunnel(
            profile,
            XrayConfigBuilder.Options(
                bypassIran = bypassIran,
                // The core logs destinations at "warning" and above. On a
                // customer's phone that is a browsing history in logcat.
                logLevel = if (BuildConfig.DEBUG) "warning" else "none",
            ),
        )
        pending = PendingConnection(serverName, json)
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
        _state.value = VpnState.Disconnecting
        context.startService(
            Intent(context, NexoraVpnService::class.java)
                .setAction(NexoraVpnService.ACTION_DISCONNECT),
        )
    }

    /**
     * Round trip through [configUri]'s server, in ms, or null when it did not
     * answer. Through the live tunnel when connected, otherwise directly.
     */
    suspend fun measureDelay(configUri: String): Int? = withContext(Dispatchers.IO) {
        val ms = if (_state.value is VpnState.Connected) {
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
        _state.value = state
    }
}
