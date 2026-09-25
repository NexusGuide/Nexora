@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.profile

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CalendarMonth
import androidx.compose.material.icons.filled.DataUsage
import androidx.compose.material.icons.filled.Devices
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.WorkspacePremium
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.ui.DangerRed
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.SettingsRow
import com.nexora.vpn.core.ui.Spacing

@Composable
fun ProfileScreen(
    onSignedOut: () -> Unit,
    onOpenSettings: () -> Unit,
    onOpenStore: () -> Unit,
    onOpenServices: () -> Unit,
    viewModel: ProfileViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var confirmSignOut by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.nav_profile)) },
                actions = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = stringResource(R.string.settings_title))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
        ) {
            state.user?.let { user ->
                NexoraCard(Modifier.fillMaxWidth()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Box(
                            Modifier.size(56.dp).clip(CircleShape).background(MaterialTheme.colorScheme.primaryContainer),
                            contentAlignment = Alignment.Center,
                        ) {
                            Icon(Icons.Filled.Person, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                        }
                        Column(Modifier.padding(start = Spacing.md)) {
                            Text(user.username, style = MaterialTheme.typography.titleLarge)
                            user.email?.let { email ->
                                Text(
                                    email,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                if (!user.isEmailVerified) {
                                    Text(
                                        stringResource(R.string.profile_email_unverified),
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.error,
                                    )
                                }
                            }
                        }
                    }
                }
            }

            state.subscription?.let { sub ->
                SectionTitle(stringResource(R.string.profile_plan))
                NexoraCard(Modifier.fillMaxWidth()) {
                    SettingsRow(
                        icon = Icons.Filled.WorkspacePremium,
                        title = stringResource(R.string.profile_status),
                        subtitle = stringResource(
                            if (sub.isUsable) R.string.home_status_active else R.string.profile_status_inactive,
                        ),
                    )
                    SettingsRow(
                        icon = Icons.Filled.CalendarMonth,
                        title = stringResource(R.string.home_expires),
                        subtitle = sub.daysRemaining?.let { days ->
                            if (days <= 0) stringResource(R.string.home_expires_today)
                            else stringResource(R.string.home_days_remaining, days)
                        } ?: "—",
                    )
                    SettingsRow(
                        icon = Icons.Filled.DataUsage,
                        title = stringResource(R.string.home_traffic),
                        subtitle = if (sub.isUnlimitedTraffic) {
                            stringResource(R.string.store_unlimited)
                        } else {
                            Formatting.trafficRatio(sub.trafficUsedBytes, sub.trafficLimitBytes)
                        },
                    )
                    SettingsRow(
                        icon = Icons.Filled.Devices,
                        title = stringResource(R.string.profile_devices),
                        subtitle = if (sub.deviceLimit > 0) {
                            stringResource(R.string.profile_devices_count, state.devices.size, sub.deviceLimit)
                        } else {
                            state.devices.size.toString()
                        },
                    )
                }
            }

            Gap(Spacing.sm)
            SettingsRow(
                icon = Icons.Filled.Star,
                title = stringResource(R.string.nav_services),
                onClick = onOpenServices,
            )
            SettingsRow(
                icon = Icons.Filled.ShoppingCart,
                title = stringResource(R.string.nav_store),
                onClick = onOpenStore,
            )

            SectionTitle(stringResource(R.string.profile_devices))
            state.devices.forEach { device ->
                NexoraCard(Modifier.fillMaxWidth().padding(bottom = Spacing.sm)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(device.name, style = MaterialTheme.typography.titleSmall)
                            if (device.isCurrent) {
                                Text(
                                    stringResource(R.string.profile_device_current),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.primary,
                                )
                            }
                        }
                        if (!device.isCurrent) {
                            TextButton(onClick = { viewModel.revokeDevice(device.id) }) {
                                Text(stringResource(R.string.profile_device_revoke))
                            }
                        }
                    }
                }
            }

            OutlinedButton(
                onClick = { confirmSignOut = true },
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.md),
            ) {
                Text(stringResource(R.string.profile_sign_out), color = DangerRed)
            }
            Gap(Spacing.lg)
        }
    }

    if (confirmSignOut) {
        AlertDialog(
            onDismissRequest = { confirmSignOut = false },
            title = { Text(stringResource(R.string.profile_sign_out)) },
            text = { Text(stringResource(R.string.profile_sign_out_body)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmSignOut = false
                    viewModel.signOut(onSignedOut)
                }) { Text(stringResource(R.string.profile_sign_out), color = DangerRed) }
            },
            dismissButton = {
                TextButton(onClick = { confirmSignOut = false }) { Text(stringResource(R.string.action_cancel)) }
            },
        )
    }
}
