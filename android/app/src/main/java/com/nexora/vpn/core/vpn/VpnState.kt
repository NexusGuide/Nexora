package com.nexora.vpn.core.vpn

/**
 * Where the tunnel is. One source of truth, owned by [VpnController] and
 * written by [NexoraVpnService]; screens and the notification only read it.
 */
sealed interface VpnState {
    data object Disconnected : VpnState

    data class Connecting(val serverName: String) : VpnState

    data class Connected(
        val serverName: String,
        /** `SystemClock.elapsedRealtime()` at connection, for a session timer
         *  that is not thrown off by the user changing the clock. */
        val sinceElapsedMs: Long,
    ) : VpnState

    data object Disconnecting : VpnState

    /**
     * [detail] is the underlying message — the core's own, for instance —
     * shown under the explanation so a failure can be reported precisely
     * instead of guessed at. It never contains the configuration itself.
     */
    data class Failed(val reason: Reason, val detail: String? = null) : VpnState

    enum class Reason {
        /** The share link is in a form the core cannot run. */
        UNSUPPORTED_CONFIG,
        /** The user has not allowed Nexora to create a VPN, or withdrew it. */
        PERMISSION_DENIED,
        /** Another VPN app took over the tunnel. */
        REVOKED,
        /** The system refused to create the interface. */
        INTERFACE_FAILED,
        /** The core would not start with this configuration. */
        CORE_FAILED,
    }
}
