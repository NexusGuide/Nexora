package com.nexora.vpn.feature.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.common.AppError
import com.nexora.vpn.core.common.Outcome
import com.nexora.vpn.core.common.Validation
import com.nexora.vpn.core.ui.DeviceSummary
import com.nexora.vpn.core.ui.deviceLimit
import com.nexora.vpn.core.ui.deviceLimitDevices
import com.nexora.vpn.domain.repository.AuthRepository
import com.nexora.vpn.domain.usecase.RegisterUseCase
import com.nexora.vpn.domain.usecase.SignInUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LoginUiState(
    val identifier: String = "",
    val password: String = "",
    val isSubmitting: Boolean = false,
    val identifierError: Validation.Reason? = null,
    val passwordError: Validation.Reason? = null,
    val error: AppError? = null,
    val signedIn: Boolean = false,
    /** Populated when the backend refuses because the plan's devices are used
     *  up. The screen shows these and offers to remove one. */
    val deviceLimit: Int? = null,
    val registeredDevices: List<DeviceSummary> = emptyList(),
) {
    val canSubmit: Boolean
        get() = identifier.isNotBlank() && password.isNotBlank() && !isSubmitting
}

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val signIn: SignInUseCase,
    private val authRepository: AuthRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun onIdentifierChange(value: String) {
        _state.update { it.copy(identifier = value, identifierError = null, error = null) }
    }

    fun onPasswordChange(value: String) {
        _state.update { it.copy(password = value, passwordError = null, error = null) }
    }

    /**
     * Signs in. [replaceDevice] is one of the devices listed by a
     * device-limit refusal, to sign out in favour of this one.
     */
    fun submit(replaceDevice: String? = null) {
        val current = _state.value
        if (!current.canSubmit) return

        _state.update {
            it.copy(
                isSubmitting = true,
                error = null,
                deviceLimit = null,
                registeredDevices = emptyList(),
            )
        }

        viewModelScope.launch {
            when (val result = signIn(current.identifier, current.password, replaceDevice)) {
                is Outcome.Success ->
                    _state.update { it.copy(isSubmitting = false, signedIn = true) }

                is Outcome.Failure -> {
                    val error = result.error
                    _state.update {
                        it.copy(
                            isSubmitting = false,
                            error = error,
                            // Carried through so the UI can show a way out
                            // rather than just an error.
                            deviceLimit = error.deviceLimit(),
                            registeredDevices = error.deviceLimitDevices(),
                        )
                    }
                }
            }
        }
    }

    fun dismissError() {
        // The device-limit dialog is drawn from deviceLimit, so clearing only
        // the error left it on screen with a Close button that did nothing.
        _state.update { it.copy(error = null, deviceLimit = null, registeredDevices = emptyList()) }
    }

    /** Used by the reset-password entry point, which needs no session. */
    fun requestPasswordReset(identifier: String, onDone: () -> Unit) {
        viewModelScope.launch {
            authRepository.requestPasswordReset(identifier)
            // The result is deliberately ignored: the backend answers the same
            // whether or not the account exists, and so must the UI, or the
            // screen would become the enumeration oracle the API avoids being.
            onDone()
        }
    }
}

data class RegisterUiState(
    val username: String = "",
    val password: String = "",
    val confirmation: String = "",
    val email: String = "",
    val isSubmitting: Boolean = false,
    val usernameError: Validation.Reason? = null,
    val passwordError: Validation.Reason? = null,
    val confirmationError: Validation.Reason? = null,
    val emailError: Validation.Reason? = null,
    val error: AppError? = null,
    /** Account created and signed in: go to the app. */
    val signedIn: Boolean = false,
    /** Account created but the automatic sign-in failed: go to sign-in. */
    val registered: Boolean = false,
) {
    val passwordStrength: Int get() = Validation.passwordStrength(password)

    val canSubmit: Boolean
        get() = username.isNotBlank() &&
            password.isNotBlank() &&
            confirmation.isNotBlank() &&
            !isSubmitting
}

@HiltViewModel
class RegisterViewModel @Inject constructor(
    private val register: RegisterUseCase,
    private val signIn: SignInUseCase,
) : ViewModel() {

    private val _state = MutableStateFlow(RegisterUiState())
    val state: StateFlow<RegisterUiState> = _state.asStateFlow()

    fun onUsernameChange(value: String) =
        _state.update { it.copy(username = value, usernameError = null, error = null) }

    fun onPasswordChange(value: String) =
        _state.update { it.copy(password = value, passwordError = null, error = null) }

    fun onConfirmationChange(value: String) =
        _state.update { it.copy(confirmation = value, confirmationError = null) }

    fun onEmailChange(value: String) =
        _state.update { it.copy(email = value, emailError = null) }

    fun dismissError() = _state.update { it.copy(error = null) }

    fun submit() {
        val current = _state.value
        // A second tap while the first request is in flight must not send a
        // second registration.
        if (current.isSubmitting || !validate(current)) return

        _state.update { it.copy(isSubmitting = true, error = null) }

        viewModelScope.launch {
            val result = register(
                username = current.username,
                password = current.password,
                email = current.email.takeIf { it.isNotBlank() },
                phone = null,
            )
            when (result) {
                is Outcome.Success -> {
                    // Signing in straight away: nobody wants to type the
                    // password they just chose a second time.
                    val signedIn = signIn(current.username, current.password)
                    _state.update {
                        it.copy(
                            isSubmitting = false,
                            signedIn = signedIn is Outcome.Success,
                            registered = signedIn !is Outcome.Success,
                        )
                    }
                }
                is Outcome.Failure ->
                    _state.update { it.copy(isSubmitting = false, error = result.error) }
            }
        }
    }

    /** Returns true when the form is worth submitting; marks fields otherwise. */
    private fun validate(current: RegisterUiState): Boolean {
        val username = Validation.username(current.username)
        val password = Validation.password(current.password)
        val confirmation =
            Validation.passwordConfirmation(current.password, current.confirmation)
        val email = Validation.email(current.email)

        val errors = listOf(username, password, confirmation, email)
        if (errors.all { it is Validation.Check.Valid }) return true

        _state.update {
            it.copy(
                usernameError = (username as? Validation.Check.Invalid)?.reason,
                passwordError = (password as? Validation.Check.Invalid)?.reason,
                confirmationError = (confirmation as? Validation.Check.Invalid)?.reason,
                emailError = (email as? Validation.Check.Invalid)?.reason,
            )
        }
        return false
    }
}
