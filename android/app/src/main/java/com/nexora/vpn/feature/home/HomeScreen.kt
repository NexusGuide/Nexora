package com.nexora.vpn.feature.home

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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
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
import com.nexora.vpn.domain.model.Subscription

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
                    pingMs = state.pingMs,
                    isProvisioning = state.isProvisioning,
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
    pingMs: Int?,
    isProvisioning: Boolean,
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
            ConnectionState.CONNECTING, ConnectionState.DISCONNECTING ->
                stringResource(R.string.home_status_connecting) to ConnectingAmber
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

            // Disabled until phase 5 supplies a VpnController. A button that
            // looked active and did nothing would be worse than one that
            // explains itself.
            Button(
                onClick = { },
                enabled = false,
                shape = CircleShape,
                colors = ButtonDefaults.buttonColors(),
                modifier = Modifier.size(width = 200.dp, height = 56.dp),
            ) {
                Text(stringResource(R.string.home_connect))
            }

            Text(
                text = stringResource(R.string.home_vpn_coming),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = Spacing.sm),
            )
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
