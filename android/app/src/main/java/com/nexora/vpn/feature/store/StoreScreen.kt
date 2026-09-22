package com.nexora.vpn.feature.store

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
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
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.InlineSpinner
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.StatBlock
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.core.ui.messageRes
import com.nexora.vpn.domain.model.Plan

@Composable
fun StoreScreen(viewModel: StoreViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    when (val plans = state.plans) {
        is UiState.Loading -> LoadingState()

        is UiState.Empty -> EmptyState(title = stringResource(R.string.store_empty))

        is UiState.Failed -> ErrorState(error = plans.error, onRetry = viewModel::refresh)

        is UiState.Content -> LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = androidx.compose.foundation.layout.PaddingValues(Spacing.md),
            verticalArrangement = Arrangement.spacedBy(Spacing.md),
        ) {
            items(plans.data, key = { it.id }) { plan ->
                PlanCard(
                    plan = plan,
                    isPurchasing = state.purchasingPlanId == plan.id,
                    onBuy = { viewModel.buy(plan.id) },
                )
            }
        }
    }

    // An order exists but payment does not yet — say exactly that rather than
    // implying the service is live (spec rule 67).
    state.createdOrder?.let {
        AlertDialog(
            onDismissRequest = viewModel::dismissOrder,
            title = { Text(stringResource(R.string.store_order_pending_title)) },
            text = { Text(stringResource(R.string.store_order_pending_body)) },
            confirmButton = {
                TextButton(onClick = viewModel::dismissOrder) {
                    Text(stringResource(R.string.action_ok))
                }
            },
        )
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

@Composable
private fun PlanCard(plan: Plan, isPurchasing: Boolean, onBuy: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(Spacing.md)) {
            Text(text = plan.name, style = MaterialTheme.typography.titleLarge)

            plan.description?.takeIf { it.isNotBlank() }?.let { description ->
                Text(
                    text = description,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = Spacing.xs),
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.md),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                StatBlock(
                    label = stringResource(R.string.store_traffic),
                    value = if (plan.isUnlimitedTraffic) {
                        stringResource(R.string.store_unlimited)
                    } else {
                        Formatting.bytes(plan.trafficLimitBytes)
                    },
                )
                StatBlock(
                    label = stringResource(R.string.store_duration),
                    value = stringResource(R.string.store_days, plan.durationDays),
                )
                StatBlock(
                    label = stringResource(R.string.store_devices),
                    value = if (plan.isUnlimitedDevices) {
                        stringResource(R.string.store_unlimited)
                    } else {
                        plan.deviceLimit.toString()
                    },
                )
            }

            Row(
                modifier = Modifier.fillMaxWidth().padding(top = Spacing.md),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = stringResource(
                        R.string.store_price,
                        Formatting.price(plan.price),
                        plan.currency,
                    ),
                    style = MaterialTheme.typography.titleLarge,
                    color = MaterialTheme.colorScheme.primary,
                )

                Button(onClick = onBuy, enabled = !isPurchasing) {
                    if (isPurchasing) {
                        InlineSpinner()
                    } else {
                        Text(stringResource(R.string.store_buy))
                    }
                }
            }
        }
    }
}
