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
import androidx.compose.material3.LinearProgressIndicator
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
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.ui.InlineSpinner
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.messageRes

/**
 * Account creation.
 *
 * Until this screen existed the "Sign up" link opened a second copy of the
 * sign-in form, so no account could be created from the app at all.
 */
@Composable
fun RegisterScreen(
    onSignedIn: () -> Unit,
    onBackToSignIn: () -> Unit,
    viewModel: RegisterViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var passwordVisible by remember { mutableStateOf(false) }

    LaunchedEffect(state.signedIn, state.registered) {
        when {
            state.signedIn -> onSignedIn()
            // Created, but the automatic sign-in did not go through: the
            // account exists, so the sign-in screen is the right next step.
            state.registered -> onBackToSignIn()
        }
    }

    val passwordTransformation =
        if (passwordVisible) VisualTransformation.None else PasswordVisualTransformation()

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
            text = stringResource(R.string.auth_sign_up),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(top = Spacing.sm, bottom = Spacing.lg),
        )

        OutlinedTextField(
            value = state.username,
            onValueChange = viewModel::onUsernameChange,
            label = { Text(stringResource(R.string.auth_username)) },
            supportingText = {
                Text(state.usernameError?.label() ?: stringResource(R.string.auth_username_hint))
            },
            singleLine = true,
            isError = state.usernameError != null,
            keyboardOptions = KeyboardOptions(
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
                keyboardType = KeyboardType.Ascii,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth(),
        )

        OutlinedTextField(
            value = state.email,
            onValueChange = viewModel::onEmailChange,
            label = { Text(stringResource(R.string.auth_email_optional)) },
            supportingText = state.emailError?.let { reason -> @Composable { Text(reason.label()) } },
            singleLine = true,
            isError = state.emailError != null,
            keyboardOptions = KeyboardOptions(
                // The keyboard capitalising the first letter is what made a
                // freshly registered address fail to sign in.
                capitalization = KeyboardCapitalization.None,
                autoCorrectEnabled = false,
                keyboardType = KeyboardType.Email,
                imeAction = ImeAction.Next,
            ),
            modifier = Modifier.fillMaxWidth().padding(top = Spacing.sm),
        )

        OutlinedTextField(
            value = state.password,
            onValueChange = viewModel::onPasswordChange,
            label = { Text(stringResource(R.string.auth_password)) },
            supportingText = state.passwordError?.let { reason -> @Composable { Text(reason.label()) } },
            singleLine = true,
            isError = state.passwordError != null,
            visualTransformation = passwordTransformation,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Next,
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

        if (state.password.isNotEmpty()) {
            val strength = state.passwordStrength
            LinearProgressIndicator(
                progress = { (strength.coerceIn(0, 4) + 1) / 5f },
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.xs),
            )
            Text(
                text = stringResource(
                    when {
                        strength <= 1 -> R.string.auth_strength_weak
                        strength == 2 -> R.string.auth_strength_fair
                        strength == 3 -> R.string.auth_strength_good
                        else -> R.string.auth_strength_strong
                    },
                ),
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.xs),
            )
        }

        OutlinedTextField(
            value = state.confirmation,
            onValueChange = viewModel::onConfirmationChange,
            label = { Text(stringResource(R.string.auth_confirm_password)) },
            supportingText = state.confirmationError?.let { reason -> @Composable { Text(reason.label()) } },
            singleLine = true,
            isError = state.confirmationError != null,
            visualTransformation = passwordTransformation,
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done,
            ),
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
                Text(stringResource(R.string.auth_sign_up))
            }
        }

        TextButton(onClick = onBackToSignIn, modifier = Modifier.padding(top = Spacing.sm)) {
            Text(stringResource(R.string.auth_have_account))
        }
    }

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
