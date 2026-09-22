package com.nexora.vpn.feature

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.network.SessionEvents
import com.nexora.vpn.core.security.TokenStore
import com.nexora.vpn.domain.repository.AuthRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@HiltViewModel
class AppViewModel @Inject constructor(
    authRepository: AuthRepository,
    private val tokenStore: TokenStore,
    sessionEvents: SessionEvents,
) : ViewModel() {

    val isSignedIn: StateFlow<Boolean> = authRepository.isSignedIn.stateIn(
        scope = viewModelScope,
        started = SharingStarted.WhileSubscribed(5_000),
        initialValue = tokenStore.isSignedIn.value,
    )

    init {
        // The authenticator discovers a dead session deep inside OkHttp and
        // signals here; clearing the store flips isSignedIn and the NavHost
        // sends the user to login.
        viewModelScope.launch {
            sessionEvents.sessionLost.collect { tokenStore.clear() }
        }
    }

    val isUsingInsecureStorage: Boolean get() = tokenStore.isUsingInsecureFallback
}
