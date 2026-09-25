@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.home

import android.app.Activity
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.DataUsage
import androidx.compose.material.icons.filled.NetworkCheck
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.ui.ConnectedGreen
import com.nexora.vpn.core.ui.ConnectingAmber
import com.nexora.vpn.core.ui.DangerRed
import com.nexora.vpn.core.ui.DisconnectedGrey
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.EvenRow
import com.nexora.vpn.core.ui.FlagBadge
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.MetricCard
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.PowerButton
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.Sparkline
import com.nexora.vpn.core.ui.StatusPill
import com.nexora.vpn.core.ui.TrafficBar
import com.nexora.vpn.core.vpn.VpnState
import kotlinx.coroutines.delay

/**
 * Home: the connection, and the state of the service behind it — status, the
 * power button, the server, live speeds, and the plan's traffic and expiry.
 */
@Composable
fun HomeScreen(
    onBrowsePlans: () -> Unit,
    onOpenServers: () -> Unit,
    onOpenSettings: () -> Unit,
    viewModel: HomeViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Android's own "allow this app to create a VPN?" dialog, shown once.
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) viewModel.connect() else viewModel.onPermissionDenied()
    }
    val onPower: () -> Unit = {
        val starting = state.connection == ConnectionState.DISCONNECTED ||
            state.connection == ConnectionState.ERROR
        val intent = if (starting) viewModel.permissionIntent() else null
        if (intent != null) permissionLauncher.launch(intent) else viewModel.onPowerPressed()
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            CenterAlignedTopAppBar(
                title = {
                    Text(
                        stringResource(R.string.app_name),
                        style = MaterialTheme.typography.titleLarge,
                        fontWeight = FontWeight.Bold,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onOpenSettings) {
                        Icon(Icons.Filled.Settings, contentDescription = stringResource(R.string.settings_title))
                    }
                },
                colors = TopAppBarDefaults.centerAlignedTopAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            val catalog = state.catalog
            when {
                catalog.isLoading && catalog.subscription == null && catalog.error == null -> LoadingState()
                catalog.error != null && catalog.subscription == null ->
                    ErrorState(error = catalog.error, onRetry = viewModel::refresh)
                state.subscription == null -> EmptyState(
                    title = stringResource(R.string.home_no_subscription),
                    body = stringResource(R.string.home_no_subscription_body),
                    actionLabel = stringResource(R.string.home_browse_plans),
                    onAction = onBrowsePlans,
                )
                else -> HomeContent(state, onPower, onOpenServers, onBrowsePlans)
            }
        }
    }

    if (state.confirmDisconnect) {
        AlertDialog(
            onDismissRequest = viewModel::cancelDisconnect,
            title = { Text(stringResource(R.string.home_disconnect_title)) },
            text = { Text(stringResource(R.string.home_disconnect_body)) },
            confirmButton = {
                Button(
                    onClick = viewModel::confirmDisconnect,
                    colors = ButtonDefaults.buttonColors(containerColor = DangerRed),
                ) { Text(stringResource(R.string.home_disconnect)) }
            },
            dismissButton = {
                TextButton(onClick = viewModel::cancelDisconnect) {
                    Text(stringResource(R.string.action_cancel))
                }
            },
        )
    }

    state.vpnError?.let { reason ->
        AlertDialog(
            onDismissRequest = viewModel::dismissVpnError,
            title = { Text(stringResource(R.string.vpn_error_title)) },
            text = {
                Column {
                    Text(stringResource(reason.messageRes()))
                    // The underlying message, small: what to send support.
                    state.vpnErrorDetail?.let { detail ->
                        Text(
                            text = detail,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(top = Spacing.sm),
                        )
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    viewModel.dismissVpnError()
                    onPower()
                }) { Text(stringResource(R.string.action_retry)) }
            },
            dismissButton = {
                TextButton(onClick = viewModel::dismissVpnError) {
                    Text(stringResource(R.string.action_close))
                }
            },
        )
    }
}

@Composable
private fun HomeContent(
    state: HomeUiState,
    onPower: () -> Unit,
    onOpenServers: () -> Unit,
    onBrowsePlans: () -> Unit,
) {
    val subscription = state.subscription ?: return
    val connected = state.connection == ConnectionState.CONNECTED
    val busy = state.connection == ConnectionState.CONNECTING ||
        state.connection == ConnectionState.DISCONNECTING

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = Spacing.md),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        val (label, colour) = when (state.connection) {
            ConnectionState.CONNECTED -> stringResource(R.string.home_status_connected) to ConnectedGreen
            ConnectionState.CONNECTING -> stringResource(R.string.home_status_connecting) to ConnectingAmber
            ConnectionState.DISCONNECTING -> stringResource(R.string.home_status_disconnecting) to ConnectingAmber
            else -> stringResource(R.string.home_status_disconnected) to DisconnectedGrey
        }
        Gap(Spacing.sm)
        StatusPill(text = label, colour = colour)
        Gap(Spacing.lg)

        if (state.isProvisioning) {
            NexoraCard(Modifier.fillMaxWidth()) {
                Text(stringResource(R.string.home_setting_up), style = MaterialTheme.typography.titleMedium)
                Text(
                    stringResource(R.string.home_setting_up_body),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = Spacing.xs),
                )
            }
        } else {
            PowerButton(
                connected = connected,
                busy = busy,
                enabled = state.server != null && state.connection != ConnectionState.DISCONNECTING,
                onClick = onPower,
            )
            Gap(Spacing.md)

            if (connected && state.connectedSinceMs != null) {
                SessionTimer(state.connectedSinceMs)
            } else {
                Text(
                    stringResource(
                        if (busy) R.string.home_status_connecting else R.string.home_tap_to_connect,
                    ),
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Gap(Spacing.lg)

            // The server, tappable to change it.
            val server = state.server
            NexoraCard(Modifier.fillMaxWidth(), onClick = onOpenServers) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    FlagBadge(server?.info?.flag)
                    Column(Modifier.weight(1f).padding(horizontal = Spacing.md)) {
                        Text(
                            stringResource(R.string.home_server),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            server?.info?.label ?: stringResource(R.string.home_no_config_short),
                            style = MaterialTheme.typography.titleMedium,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Icon(Icons.AutoMirrored.Filled.KeyboardArrowRight, contentDescription = null)
                }
            }
            if (server == null) {
                Text(
                    stringResource(R.string.home_no_config),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = Spacing.sm),
                )
            }
            Gap(Spacing.sm)

            EvenRow {
                MetricCard(
                    Icons.Filled.ArrowDownward,
                    stringResource(R.string.home_download),
                    if (connected) Formatting.speed(state.traffic.downPerSecond) else "—",
                    Modifier.weight(1f),
                )
                MetricCard(
                    Icons.Filled.ArrowUpward,
                    stringResource(R.string.home_upload),
                    if (connected) Formatting.speed(state.traffic.upPerSecond) else "—",
                    Modifier.weight(1f),
                )
            }
            Gap(Spacing.sm)
            EvenRow {
                MetricCard(
                    Icons.Filled.NetworkCheck,
                    stringResource(R.string.home_ping),
                    Formatting.latency(state.pingMs),
                    Modifier.weight(1f),
                )
                MetricCard(
                    Icons.Filled.DataUsage,
                    stringResource(R.string.home_session_data),
                    if (connected) {
                        Formatting.bytes(state.traffic.sessionDown + state.traffic.sessionUp)
                    } else {
                        "—"
                    },
                    Modifier.weight(1f),
                )
            }

            if (connected) {
                Gap(Spacing.sm)
                NexoraCard(Modifier.fillMaxWidth()) {
                    Text(
                        stringResource(R.string.home_speed_graph),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Sparkline(
                        values = state.traffic.downHistory,
                        modifier = Modifier.fillMaxWidth().height(72.dp).padding(top = Spacing.sm),
                    )
                }
            }
        }

        Gap(Spacing.sm)
        PlanCard(subscription, onBrowsePlans)
        Gap(Spacing.lg)
    }
}

@Composable
private fun PlanCard(subscription: com.nexora.vpn.domain.model.Subscription, onRenew: () -> Unit) {
    NexoraCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    stringResource(R.string.home_traffic),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    if (subscription.isUnlimitedTraffic) {
                        stringResource(R.string.store_unlimited)
                    } else {
                        Formatting.trafficRatio(subscription.trafficUsedBytes, subscription.trafficLimitBytes)
                    },
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(
                    stringResource(R.string.home_expires),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    subscription.daysRemaining?.let { days ->
                        if (days <= 0) stringResource(R.string.home_expires_today)
                        else stringResource(R.string.home_days_remaining, days)
                    } ?: "—",
                    style = MaterialTheme.typography.titleMedium,
                )
            }
        }
        TrafficBar(fraction = subscription.trafficFraction)
        if (subscription.needsAttention) {
            Row(
                Modifier.fillMaxWidth().padding(top = Spacing.sm),
                horizontalArrangement = Arrangement.End,
            ) {
                TextButton(onClick = onRenew) { Text(stringResource(R.string.home_renew)) }
            }
        }
    }
}

/** Time since connecting, ticking once a second while on screen. */
@Composable
private fun SessionTimer(sinceElapsedMs: Long) {
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(sinceElapsedMs) {
        while (true) {
            now = SystemClock.elapsedRealtime()
            delay(1_000)
        }
    }
    Text(
        text = Formatting.duration(((now - sinceElapsedMs) / 1000).coerceAtLeast(0)),
        style = MaterialTheme.typography.headlineMedium,
        fontWeight = FontWeight.SemiBold,
    )
}

internal fun VpnState.Reason.messageRes(): Int = when (this) {
    VpnState.Reason.UNSUPPORTED_CONFIG -> R.string.vpn_error_unsupported
    VpnState.Reason.PERMISSION_DENIED -> R.string.vpn_error_permission
    VpnState.Reason.REVOKED -> R.string.vpn_error_revoked
    VpnState.Reason.INTERFACE_FAILED -> R.string.vpn_error_interface
    VpnState.Reason.CORE_FAILED -> R.string.vpn_error_core
}
