package com.nexora.vpn.feature.services

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
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
import com.nexora.vpn.core.ui.StatusPill
import com.nexora.vpn.core.ui.TrafficBar
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.domain.model.Subscription
import com.nexora.vpn.domain.model.SubscriptionStatus

@Composable
fun ServicesScreen(
    onRenew: (Subscription) -> Unit,
    viewModel: ServicesViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Column(Modifier.fillMaxSize()) {
        TabRow(selectedTabIndex = state.tab.ordinal) {
            ServicesTab.entries.forEach { tab ->
                Tab(
                    selected = state.tab == tab,
                    onClick = { viewModel.selectTab(tab) },
                    text = {
                        val label = when (tab) {
                            ServicesTab.ACTIVE -> R.string.services_tab_active
                            ServicesTab.EXPIRED -> R.string.services_tab_expired
                            ServicesTab.SUSPENDED -> R.string.services_tab_suspended
                        }
                        val count = state.countFor(tab)
                        Text(
                            if (count > 0) {
                                "${stringResource(label)} ($count)"
                            } else {
                                stringResource(label)
                            },
                        )
                    },
                )
            }
        }

        when (val subscriptions = state.subscriptions) {
            is UiState.Loading -> LoadingState()

            is UiState.Empty -> EmptyState(title = stringResource(R.string.services_empty))

            is UiState.Failed ->
                ErrorState(error = subscriptions.error, onRetry = viewModel::refresh)

            is UiState.Content -> {
                val visible = state.visible
                if (visible.isEmpty()) {
                    EmptyState(title = stringResource(R.string.services_empty))
                } else {
                    LazyColumn(
                        contentPadding = PaddingValues(Spacing.md),
                        verticalArrangement = Arrangement.spacedBy(Spacing.md),
                    ) {
                        items(visible, key = { it.id }) { subscription ->
                            SubscriptionCard(
                                subscription = subscription,
                                onRenew = { onRenew(subscription) },
                                onRefreshConfigs = {
                                    viewModel.requestConfigRefresh(subscription.id)
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun SubscriptionCard(
    subscription: Subscription,
    onRenew: () -> Unit,
    onRefreshConfigs: () -> Unit,
) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(Spacing.md)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val (labelRes, colour) = when (subscription.status) {
                    SubscriptionStatus.ACTIVE ->
                        R.string.services_status_active to ConnectedGreen
                    SubscriptionStatus.PENDING ->
                        R.string.services_status_pending to ConnectingAmber
                    SubscriptionStatus.SUSPENDED ->
                        R.string.services_status_suspended to ConnectingAmber
                    SubscriptionStatus.EXPIRED ->
                        R.string.services_status_expired to DisconnectedGrey
                    SubscriptionStatus.CANCELLED ->
                        R.string.services_status_cancelled to DisconnectedGrey
                    SubscriptionStatus.UNKNOWN ->
                        R.string.services_status_pending to DisconnectedGrey
                }
                StatusPill(text = stringResource(labelRes), colour = colour)

                subscription.daysRemaining?.let { days ->
                    Text(
                        text = stringResource(R.string.home_days_remaining, days),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Text(
                text = if (subscription.isUnlimitedTraffic) {
                    stringResource(R.string.store_unlimited)
                } else {
                    Formatting.trafficRatio(
                        subscription.trafficUsedBytes,
                        subscription.trafficLimitBytes,
                    )
                },
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.padding(top = Spacing.sm),
            )
            TrafficBar(fraction = subscription.trafficFraction)

            // Surfaced only when it matters, so the warning keeps its meaning.
            if (subscription.needsAttention) {
                Text(
                    text = if (subscription.trafficFraction?.let { it >= 0.9f } == true) {
                        stringResource(R.string.services_traffic_low)
                    } else {
                        stringResource(
                            R.string.services_expiring_soon,
                            subscription.daysRemaining ?: 0,
                        )
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.sm),
                horizontalArrangement = Arrangement.End,
            ) {
                if (subscription.isUsable) {
                    TextButton(onClick = onRefreshConfigs) {
                        Text(stringResource(R.string.configs_title))
                    }
                }
                TextButton(onClick = onRenew) {
                    Text(stringResource(R.string.services_renew))
                }
            }
        }
    }
}
