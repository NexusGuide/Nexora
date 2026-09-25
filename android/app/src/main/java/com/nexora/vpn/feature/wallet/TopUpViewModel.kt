package com.nexora.vpn.feature.wallet

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.common.Payments
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.CryptoWallet
import com.nexora.vpn.domain.model.PaymentMethods
import com.nexora.vpn.domain.model.TopUp
import com.nexora.vpn.domain.model.TopUpMethod
import com.nexora.vpn.domain.repository.WalletRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Which of the operator's destinations the customer paid: the card, or crypto wallet n. */
sealed interface PayTarget {
    data object Card : PayTarget
    data class Crypto(val index: Int) : PayTarget
}

enum class TopUpFieldError { AMOUNT_INVALID, AMOUNT_RANGE, REFERENCE }

data class TopUpUiState(
    val methods: UiState<PaymentMethods> = UiState.Loading,
    val target: PayTarget? = null,
    val amountText: String = "",
    val reference: String = "",
    val payerNote: String = "",
    val orderId: String? = null,
    val fieldError: TopUpFieldError? = null,
    val submitting: Boolean = false,
    val submitted: TopUp? = null,
    val error: AppError? = null,
) {
    val amount: Long? get() = Payments.parseToman(amountText)

    val selectedWallet: CryptoWallet?
        get() = (target as? PayTarget.Crypto)?.let { t ->
            (methods as? UiState.Content)?.data?.crypto?.getOrNull(t.index)
        }

    /** What to send in crypto for the amount typed, for display only; the server quotes it too. */
    val cryptoQuote: String?
        get() {
            val wallet = selectedWallet ?: return null
            return Payments.cryptoQuote(amount ?: return null, wallet.rate)
        }
}

@HiltViewModel
class TopUpViewModel @Inject constructor(
    private val repository: WalletRepository,
    savedState: SavedStateHandle,
) : ViewModel() {

    private val _state = MutableStateFlow(
        TopUpUiState(
            amountText = savedState.get<String>("amount")?.takeIf { it.toLongOrNull()?.let { n -> n > 0 } == true }.orEmpty(),
            orderId = savedState.get<String>("order")?.takeIf { it.isNotBlank() },
        ),
    )
    val state: StateFlow<TopUpUiState> = _state.asStateFlow()

    init {
        load()
    }

    fun load() {
        _state.update { it.copy(methods = UiState.Loading) }
        viewModelScope.launch {
            when (val result = repository.paymentMethods()) {
                is Outcome.Success -> _state.update { current ->
                    val m = result.data
                    current.copy(
                        methods = if (m.hasAny) UiState.Content(m) else UiState.Empty,
                        // Preselect the first available method; one tap fewer.
                        target = current.target ?: when {
                            m.card != null -> PayTarget.Card
                            m.crypto.isNotEmpty() -> PayTarget.Crypto(0)
                            else -> null
                        },
                    )
                }
                is Outcome.Failure -> _state.update { it.copy(methods = UiState.Failed(result.error)) }
            }
        }
    }

    fun select(target: PayTarget) = _state.update { it.copy(target = target, fieldError = null) }
    fun setAmount(text: String) = _state.update { it.copy(amountText = text, fieldError = null) }
    fun setReference(text: String) = _state.update { it.copy(reference = text, fieldError = null) }
    fun setNote(text: String) = _state.update { it.copy(payerNote = text.take(120)) }
    fun dismissError() = _state.update { it.copy(error = null) }

    fun submit() {
        val s = _state.value
        if (s.submitting || s.submitted != null) return
        val methods = (s.methods as? UiState.Content)?.data ?: return
        val target = s.target ?: return

        val amount = s.amount
        if (amount == null || amount <= 0) {
            _state.update { it.copy(fieldError = TopUpFieldError.AMOUNT_INVALID) }
            return
        }
        if (amount < methods.minTopup || (methods.maxTopup > 0 && amount > methods.maxTopup)) {
            _state.update { it.copy(fieldError = TopUpFieldError.AMOUNT_RANGE) }
            return
        }
        val crypto = target is PayTarget.Crypto
        val reference = Payments.normaliseReference(s.reference)
        if (!Payments.isValidReference(crypto, reference)) {
            _state.update { it.copy(fieldError = TopUpFieldError.REFERENCE) }
            return
        }

        val wallet = s.selectedWallet
        _state.update { it.copy(submitting = true, error = null) }
        viewModelScope.launch {
            val result = repository.submitTopUp(
                method = if (crypto) TopUpMethod.CRYPTO else TopUpMethod.CARD,
                amount = amount,
                reference = reference,
                payerNote = s.payerNote,
                network = wallet?.network,
                asset = wallet?.asset,
                orderId = s.orderId,
            )
            _state.update {
                when (result) {
                    is Outcome.Success -> it.copy(submitting = false, submitted = result.data)
                    is Outcome.Failure -> it.copy(submitting = false, error = result.error)
                }
            }
        }
    }
}
