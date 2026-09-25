package com.nexora.vpn.feature.wallet

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.TopUp
import com.nexora.vpn.domain.model.Wallet
import com.nexora.vpn.domain.repository.WalletRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class WalletUiState(
    val wallet: UiState<Wallet> = UiState.Loading,
    val topups: List<TopUp> = emptyList(),
    val cancelling: String? = null,
    val error: AppError? = null,
)

@HiltViewModel
class WalletViewModel @Inject constructor(
    private val repository: WalletRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(WalletUiState())
    val state: StateFlow<WalletUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            val wallet = async { repository.wallet() }
            val topups = async { repository.topups() }
            val w = wallet.await()
            val t = topups.await()
            _state.update {
                it.copy(
                    wallet = when (w) {
                        is Outcome.Success -> UiState.Content(w.data)
                        is Outcome.Failure -> UiState.Failed(w.error)
                    },
                    topups = t.getOrNull() ?: it.topups,
                )
            }
        }
    }

    fun cancel(topupId: String) {
        if (_state.value.cancelling != null) return
        _state.update { it.copy(cancelling = topupId) }
        viewModelScope.launch {
            val result = repository.cancelTopUp(topupId)
            _state.update {
                it.copy(cancelling = null, error = (result as? Outcome.Failure)?.error)
            }
            load()
        }
    }

    fun dismissError() = _state.update { it.copy(error = null) }
}
