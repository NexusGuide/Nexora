package com.nexora.vpn.feature.profile

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.ui.Spacing

@Composable
fun ProfileScreen(
    onSignedOut: () -> Unit,
    viewModel: ProfileViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(Modifier.fillMaxSize().padding(Spacing.md)) {
        state.user?.let { user ->
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(Spacing.md)) {
                    Text(user.username, style = MaterialTheme.typography.titleLarge)
                    user.email?.let { email ->
                        Text(email, style = MaterialTheme.typography.bodyMedium)
                        if (!user.isEmailVerified) {
                            Text(
                                text = stringResource(R.string.profile_email_unverified),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                    }
                }
            }
        }

        Text(
            text = stringResource(R.string.profile_devices),
            style = MaterialTheme.typography.titleMedium,
            modifier = Modifier.padding(top = Spacing.lg, bottom = Spacing.sm),
        )

        state.devices.forEach { device ->
            Card(Modifier.fillMaxWidth().padding(bottom = Spacing.sm)) {
                Column(Modifier.padding(Spacing.md)) {
                    Text(device.name, style = MaterialTheme.typography.bodyLarge)
                    if (device.isCurrent) {
                        Text(
                            text = stringResource(R.string.profile_device_current),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.primary,
                        )
                    } else {
                        TextButton(onClick = { viewModel.revokeDevice(device.id) }) {
                            Text(stringResource(R.string.profile_device_revoke))
                        }
                    }
                }
            }
        }

        OutlinedButton(
            onClick = { viewModel.signOut(onSignedOut) },
            modifier = Modifier.fillMaxWidth().padding(top = Spacing.lg),
        ) {
            Text(stringResource(R.string.profile_sign_out))
        }
    }
}
