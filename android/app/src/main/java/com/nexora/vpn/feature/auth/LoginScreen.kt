package com.nexora.vpn.feature.auth

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Validation
import com.nexora.vpn.core.ui.InlineSpinner
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.messageRes

@Composable
fun LoginScreen(
    onSignedIn: () -> Unit,
    onRegister: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var passwordVisible by remember { mutableStateOf(false) }
    var showForgotDialog by remember { mutableStateOf(false) }
    var forgotIdentifier by remember { mutableStateOf("") }
    var forgotSent by remember { mutableStateOf(false) }

    LaunchedEffect(state.signedIn) {
        if (state.signedIn) onSignedIn()
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(Spacing.lg),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = stringResource(R.string.app_name),
            style = MaterialTheme.typography.displaySmall,
            color = MaterialTheme.colorScheme.primary,
        )

        Text(
            text = stringResource(R.string.auth_sign_in),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(top = Spacing.sm, bottom = Spacing.lg),
        )

        OutlinedTextField(
            value = state.identifier,
            onValueChange = viewModel::onIdentifierChange,
            label = { Text(stringResource(R.string.auth_identifier)) },
            singleLine = true,
            isError = state.identifierError != null,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        OutlinedTextField(
            value = state.password,
            onValueChange = viewModel::onPasswordChange,
            label = { Text(stringResource(R.string.auth_password)) },
            singleLine = true,
            isError = state.passwordError != null,
            visualTransformation = if (passwordVisible) {
                VisualTransformation.None
            } else {
                PasswordVisualTransformation()
            },
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done,
            ),
            trailingIcon = {
                IconButton(onClick = { passwordVisible = !passwordVisible }) {
                    Text(
                        text = if (passwordVisible) "🙈" else "👁",
                        style = MaterialTheme.typography.bodyLarge,
                    )
                }
            },
            modifier = Modifier.fillMaxWidth().padding(top = Spacing.sm),
        )

        Button(
            onClick = viewModel::submit,
            enabled = state.canSubmit,
            modifier = Modifier.fillMaxWidth().padding(top = Spacing.lg),
        ) {
            if (state.isSubmitting) {
                InlineSpinner()
            } else {
                Text(stringResource(R.string.auth_sign_in))
            }
        }

        TextButton(
            onClick = {
                forgotIdentifier = state.identifier
                forgotSent = false
                showForgotDialog = true
            },
            modifier = Modifier.padding(top = Spacing.sm),
        ) {
            Text(stringResource(R.string.auth_forgot_password))
        }

        TextButton(onClick = onRegister) {
            Text(stringResource(R.string.auth_no_account))
        }
    }

    // Device limit gets a dialog of its own: it is the one failure with a
    // concrete action the user can take right now.
    val deviceLimit = state.deviceLimit
    if (deviceLimit != null) {
        AlertDialog(
            onDismissRequest = viewModel::dismissError,
            title = { Text(stringResource(R.string.device_limit_title)) },
            text = {
                Column {
                    Text(stringResource(R.string.device_limit_body, deviceLimit))
                    state.registeredDevices.forEach { device ->
                        Text(
                            text = "• ${device.name}",
                            style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.padding(top = Spacing.xs),
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = viewModel::dismissError) {
                    Text(stringResource(R.string.action_close))
                }
            },
        )
    } else {
        state.error?.let { error ->
            AlertDialog(
                onDismissRequest = viewModel::dismissError,
                text = { Text(stringResource(error.messageRes())) },
                confirmButton = {
                    TextButton(onClick = viewModel::dismissError) {
                        Text(stringResource(R.string.action_close))
                    }
                },
            )
        }
    }

    if (showForgotDialog) {
        AlertDialog(
            onDismissRequest = { showForgotDialog = false },
            title = { Text(stringResource(R.string.auth_reset_title)) },
            text = {
                if (forgotSent) {
                    // The same message regardless of whether the account
                    // exists — the backend answers identically, and so must
                    // this, or the screen leaks what the API will not.
                    Text(
                        text = stringResource(R.string.auth_reset_sent),
                        textAlign = TextAlign.Start,
                    )
                } else {
                    OutlinedTextField(
                        value = forgotIdentifier,
                        onValueChange = { forgotIdentifier = it },
                        label = { Text(stringResource(R.string.auth_identifier)) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        if (forgotSent) {
                            showForgotDialog = false
                        } else {
                            viewModel.requestPasswordReset(forgotIdentifier) {
                                forgotSent = true
                            }
                        }
                    },
                    enabled = forgotSent || forgotIdentifier.isNotBlank(),
                ) {
                    Text(
                        stringResource(
                            if (forgotSent) R.string.action_close else R.string.action_continue,
                        ),
                    )
                }
            },
            dismissButton = if (forgotSent) {
                null
            } else {
                {
                    TextButton(onClick = { showForgotDialog = false }) {
                        Text(stringResource(R.string.action_cancel))
                    }
                }
            },
        )
    }
}

/** Maps a validation reason to its message. Kept beside the screen that shows it. */
@Composable
fun Validation.Reason.label(): String = stringResource(
    when (this) {
        Validation.Reason.EMPTY -> R.string.validation_required
        Validation.Reason.USERNAME_TOO_SHORT -> R.string.validation_username_short
        Validation.Reason.USERNAME_TOO_LONG -> R.string.validation_username_long
        Validation.Reason.USERNAME_CHARSET -> R.string.validation_username_charset
        Validation.Reason.PASSWORD_TOO_SHORT -> R.string.validation_password_short
        Validation.Reason.PASSWORD_TOO_LONG -> R.string.validation_password_long
        Validation.Reason.PASSWORD_NEEDS_LOWERCASE -> R.string.validation_password_lowercase
        Validation.Reason.PASSWORD_NEEDS_UPPERCASE -> R.string.validation_password_uppercase
        Validation.Reason.PASSWORD_NEEDS_DIGIT -> R.string.validation_password_digit
        Validation.Reason.PASSWORDS_DO_NOT_MATCH -> R.string.validation_password_mismatch
        Validation.Reason.EMAIL_INVALID -> R.string.validation_email
        Validation.Reason.PHONE_INVALID -> R.string.validation_phone
    },
)
