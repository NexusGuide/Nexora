package com.nexora.vpn.feature.home

import android.app.Activity
import android.os.SystemClock
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.ui.ConnectedGreen
import com.nexora.vpn.core.ui.ConnectingAmber
import com.nexora.vpn.core.ui.DisconnectedGrey
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.StatBlock
import com.nexora.vpn.core.ui.StatusPill
import com.nexora.vpn.core.ui.TrafficBar
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.core.vpn.VpnState
import com.nexora.vpn.domain.model.Subscription
import kotlinx.coroutines.delay

/**
 * Home: the connection, and the state of the service behind it.
 *
 * Follows the spec's Home layout (rule 51) — status, server, ping, the connect
 * control, then traffic and expiry.
 */
@Composable
fun HomeScreen(
    onBrowsePlans: () -> Unit,
    viewModel: HomeViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Android's own "allow this app to create a VPN?" dialog. Shown once; after
    // the user agrees, permissionIntent() returns null and connecting is direct.
    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            viewModel.toggleConnection()
        } else {
            viewModel.onPermissionDenied()
        }
    }
    val onConnectClick: () -> Unit = {
        val needsPermission = state.connection == ConnectionState.DISCONNECTED ||
            state.connection == ConnectionState.ERROR
        val intent = if (needsPermission) viewModel.permissionIntent() else null
        if (intent != null) permissionLauncher.launch(intent) else viewModel.toggleConnection()
    }

    state.vpnError?.let { reason ->
        AlertDialog(
            onDismissRequest = viewModel::dismissVpnError,
            text = {
                Column {
                    Text(stringResource(reason.messageRes()))
                    // The underlying message, small: what to send when
                    // reporting the problem.
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
                TextButton(onClick = viewModel::dismissVpnError) {
                    Text(stringResource(R.string.action_close))
                }
            },
        )
    }

    when (val subscription = state.subscription) {
        is UiState.Loading -> LoadingState()

        is UiState.Empty -> EmptyState(
            title = stringResource(R.string.home_no_subscription),
            body = stringResource(R.string.home_no_subscription_body),
            actionLabel = stringResource(R.string.home_browse_plans),
            onAction = onBrowsePlans,
        )

        is UiState.Failed -> ErrorState(
            error = subscription.error,
            onRetry = viewModel::refresh,
        )

        is UiState.Content -> {
            val data = subscription.data
            if (data == null) {
                EmptyState(
                    title = stringResource(R.string.home_no_subscription),
                    body = stringResource(R.string.home_no_subscription_body),
                    actionLabel = stringResource(R.string.home_browse_plans),
                    onAction = onBrowsePlans,
                )
            } else {
                HomeContent(
                    subscription = data,
                    connection = state.connection,
                    serverName = state.activeConfig?.name,
                    hasConfig = state.activeConfig != null,
                    pingMs = state.pingMs,
                    connectedSinceMs = state.connectedSinceMs,
                    isProvisioning = state.isProvisioning,
                    onConnectClick = onConnectClick,
                )
            }
        }
    }
}

@Composable
private fun HomeContent(
    subscription: Subscription,
    connection: ConnectionState,
    serverName: String?,
    hasConfig: Boolean,
    pingMs: Int?,
    connectedSinceMs: Long?,
    isProvisioning: Boolean,
    onConnectClick: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(Spacing.md),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(Spacing.lg))

        val (statusLabel, statusColour) = when (connection) {
            ConnectionState.CONNECTED ->
                stringResource(R.string.home_status_connected) to ConnectedGreen
            ConnectionState.CONNECTING ->
                stringResource(R.string.home_status_connecting) to ConnectingAmber
            ConnectionState.DISCONNECTING ->
                stringResource(R.string.home_status_disconnecting) to ConnectingAmber
            else ->
                stringResource(R.string.home_status_disconnected) to DisconnectedGrey
        }

        StatusPill(text = statusLabel, colour = statusColour)

        Spacer(Modifier.height(Spacing.lg))

        if (isProvisioning) {
            // The subscription is paid for but the worker has not finished on
            // the panel. Saying so beats showing a connect button that cannot
            // work yet.
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(Spacing.md)) {
                    Text(
                        text = stringResource(R.string.home_setting_up),
                        style = MaterialTheme.typography.titleMedium,
                    )
                    Text(
                        text = stringResource(R.string.home_setting_up_body),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = Spacing.xs),
                    )
                }
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
            ) {
                StatBlock(
                    label = stringResource(R.string.home_server),
                    value = serverName ?: "—",
                )
                StatBlock(
                    label = stringResource(R.string.home_ping),
                    value = Formatting.latency(pingMs),
                )
            }

            Spacer(Modifier.height(Spacing.lg))

            val connected = connection == ConnectionState.CONNECTED
            Button(
                onClick = onConnectClick,
                // No config yet means nothing to connect to; disconnecting is
                // already in hand.
                enabled = hasConfig && connection != ConnectionState.DISCONNECTING,
                shape = CircleShape,
                colors = if (connected) {
                    ButtonDefaults.buttonColors(containerColor = DisconnectedGrey)
                } else {
                    ButtonDefaults.buttonColors()
                },
                modifier = Modifier.size(width = 200.dp, height = 56.dp),
            ) {
                Text(
                    stringResource(
                        if (connected || connection == ConnectionState.CONNECTING) {
                            R.string.home_disconnect
                        } else {
                            R.string.home_connect
                        },
                    ),
                )
            }

            if (!hasConfig) {
                Text(
                    text = stringResource(R.string.home_no_config),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = Spacing.sm),
                )
            }

            if (connected && connectedSinceMs != null) {
                SessionTimer(sinceElapsedMs = connectedSinceMs)
            }
        }

        Spacer(Modifier.height(Spacing.xl))

        Card(Modifier.fillMaxWidth()) {
            Column(Modifier.padding(Spacing.md)) {
                Text(
                    text = stringResource(R.string.home_traffic),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = if (subscription.isUnlimitedTraffic) {
                        stringResource(R.string.store_unlimited)
                    } else {
                        Formatting.trafficRatio(
                            subscription.trafficUsedBytes,
                            subscription.trafficLimitBytes,
                        )
                    },
                    style = MaterialTheme.typography.headlineSmall,
                )
                TrafficBar(fraction = subscription.trafficFraction)

                Spacer(Modifier.height(Spacing.md))

                StatBlock(
                    label = stringResource(R.string.home_expires),
                    value = subscription.daysRemaining?.let { days ->
                        if (days <= 0) {
                            stringResource(R.string.home_expires_today)
                        } else {
                            stringResource(R.string.home_days_remaining, days)
                        }
                    } ?: "—",
                )
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
        text = stringResource(R.string.home_session) + " " +
            Formatting.duration(((now - sinceElapsedMs) / 1000).coerceAtLeast(0)),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = Spacing.sm),
    )
}

private fun VpnState.Reason.messageRes(): Int = when (this) {
    VpnState.Reason.UNSUPPORTED_CONFIG -> R.string.vpn_error_unsupported
    VpnState.Reason.PERMISSION_DENIED -> R.string.vpn_error_permission
    VpnState.Reason.REVOKED -> R.string.vpn_error_revoked
    VpnState.Reason.INTERFACE_FAILED -> R.string.vpn_error_interface
    VpnState.Reason.CORE_FAILED -> R.string.vpn_error_core
}
