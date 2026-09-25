package com.nexora.vpn.feature.profile

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.domain.model.Device
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.feature.servers.ServerCatalog
import com.nexora.vpn.domain.model.User
import com.nexora.vpn.domain.repository.UserRepository
import com.nexora.vpn.domain.repository.WalletRepository
import com.nexora.vpn.domain.usecase.SignOutUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ProfileUiState(
    val user: User? = null,
    val devices: List<Device> = emptyList(),
    val subscription: Subscription? = null,
    val walletBalance: Long? = null,
    val isLoading: Boolean = true,
)

@HiltViewModel
class ProfileViewModel @Inject constructor(
    private val userRepository: UserRepository,
    private val signOut: SignOutUseCase,
    private val catalog: ServerCatalog,
    private val wallet: WalletRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(ProfileUiState())
    val state: StateFlow<ProfileUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            val me = userRepository.me()
            val devices = userRepository.devices()
            val balance = wallet.wallet().getOrNull()?.balance
            if (catalog.state.value.subscription == null) catalog.load()
            _state.update {
                it.copy(
                    user = me.getOrNull(),
                    devices = devices.getOrNull().orEmpty(),
                    subscription = catalog.state.value.subscription,
                    walletBalance = balance,
                    isLoading = false,
                )
            }
        }
    }

    fun revokeDevice(deviceId: String) {
        viewModelScope.launch {
            if (userRepository.revokeDevice(deviceId) is Outcome.Success) {
                load()
            }
        }
    }

    fun signOut(onDone: () -> Unit) {
        viewModelScope.launch {
            signOut.invoke()
            onDone()
        }
    }
}
