package com.nexora.vpn.core.network

import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * Carries "this session is over" from the network layer to the UI.
 *
 * The authenticator discovers a dead session on a background thread, deep
 * inside OkHttp, where it can neither navigate nor touch a ViewModel. A shared
 * flow lets the app shell react without the network layer knowing anything
 * about navigation.
 */
class SessionEvents {

    // replay = 1 so an expiry that happens before the UI subscribes — during
    // startup, say — is still delivered rather than lost.
    private val _sessionLost = MutableSharedFlow<Unit>(
        replay = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )

    val sessionLost: SharedFlow<Unit> = _sessionLost

    fun notifySessionLost() {
        _sessionLost.tryEmit(Unit)
    }

    /** Clears the replayed value once handled, so a later sign-in is not bounced. */
    fun consume() {
        _sessionLost.resetReplayCache()
    }
}
