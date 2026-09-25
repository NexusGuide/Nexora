package com.nexora.vpn.feature.wallet

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.ui.DangerRed
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.InlineSpinner
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.core.ui.messageRes
import com.nexora.vpn.domain.model.TopUp
import com.nexora.vpn.domain.model.TopUpMethod
import com.nexora.vpn.domain.model.TopUpStatus
import com.nexora.vpn.domain.model.WalletTransaction
import com.nexora.vpn.domain.model.WalletTxKind
import java.text.DateFormat
import java.util.Date

@Composable
fun WalletScreen(
    onBack: () -> Unit,
    onTopUp: () -> Unit,
    viewModel: WalletViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // Back from the top-up screen, or back in the app after paying in the
    // bank app: show what changed without a manual refresh.
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { viewModel.load() }
    }

    SubScreen(
        title = stringResource(R.string.wallet_title),
        onBack = onBack,
        actions = {
            IconButton(onClick = viewModel::load) {
                Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.action_refresh))
            }
        },
    ) { padding ->
        when (val wallet = state.wallet) {
            is UiState.Loading -> LoadingState(Modifier.padding(padding))
            is UiState.Empty -> LoadingState(Modifier.padding(padding))
            is UiState.Failed -> ErrorState(
                error = wallet.error,
                onRetry = viewModel::load,
                modifier = Modifier.padding(padding),
            )
            is UiState.Content -> LazyColumn(
                modifier = Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(Spacing.md),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                item {
                    NexoraCard(Modifier.fillMaxWidth()) {
                        Text(
                            stringResource(R.string.wallet_balance),
                            style = MaterialTheme.typography.labelLarge,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(
                            stringResource(R.string.money_toman, Formatting.price(wallet.data.balance)),
                            style = MaterialTheme.typography.headlineMedium,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(vertical = Spacing.xs),
                        )
                        Button(onClick = onTopUp, modifier = Modifier.fillMaxWidth()) {
                            Icon(Icons.Filled.Add, contentDescription = null)
                            Text(stringResource(R.string.wallet_top_up), Modifier.padding(start = Spacing.xs))
                        }
                    }
                }

                val open = state.topups.filter { it.status == TopUpStatus.PENDING || it.status == TopUpStatus.REJECTED }
                if (open.isNotEmpty()) {
                    item { SectionTitle(stringResource(R.string.wallet_requests)) }
                    items(open, key = { "t-" + it.id }) { topup ->
                        TopUpRow(
                            topup = topup,
                            cancelling = state.cancelling == topup.id,
                            onCancel = { viewModel.cancel(topup.id) },
                        )
                    }
                }

                item { SectionTitle(stringResource(R.string.wallet_history)) }
                if (wallet.data.transactions.isEmpty()) {
                    item {
                        Text(
                            stringResource(R.string.wallet_history_empty),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                items(wallet.data.transactions, key = { "x-" + it.id }) { TransactionRow(it) }
            }
        }
    }

    state.error?.let { error ->
        AlertDialog(
            onDismissRequest = viewModel::dismissError,
            text = { Text(stringResource(error.messageRes())) },
            confirmButton = {
                TextButton(onClick = viewModel::dismissError) { Text(stringResource(R.string.action_close)) }
            },
        )
    }
}

@Composable
private fun TopUpRow(topup: TopUp, cancelling: Boolean, onCancel: () -> Unit) {
    NexoraCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(
                    stringResource(R.string.money_toman, Formatting.price(topup.amount)),
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    stringResource(
                        if (topup.method == TopUpMethod.CRYPTO) R.string.topup_method_crypto else R.string.topup_method_card,
                    ) + (topup.network?.let { " · ${topup.asset} $it" } ?: ""),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            val (label, colour) = when (topup.status) {
                TopUpStatus.PENDING -> R.string.topup_status_pending to MaterialTheme.colorScheme.tertiary
                TopUpStatus.APPROVED -> R.string.topup_status_approved to MaterialTheme.colorScheme.primary
                TopUpStatus.REJECTED -> R.string.topup_status_rejected to DangerRed
                else -> R.string.topup_status_cancelled to MaterialTheme.colorScheme.onSurfaceVariant
            }
            Text(stringResource(label), color = colour, style = MaterialTheme.typography.labelLarge)
        }
        if (topup.status == TopUpStatus.PENDING) {
            Text(
                stringResource(R.string.topup_pending_body),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = Spacing.xs),
            )
            TextButton(onClick = onCancel, enabled = !cancelling) {
                if (cancelling) InlineSpinner() else Text(stringResource(R.string.topup_cancel), color = DangerRed)
            }
        }
        topup.rejectReason?.takeIf { topup.status == TopUpStatus.REJECTED }?.let { reason ->
            Text(
                stringResource(R.string.topup_rejected_reason, reason),
                style = MaterialTheme.typography.bodySmall,
                color = DangerRed,
                modifier = Modifier.padding(top = Spacing.xs),
            )
        }
    }
}

@Composable
private fun TransactionRow(tx: WalletTransaction) {
    val title = when (tx.kind) {
        WalletTxKind.TOPUP -> R.string.wallet_tx_topup
        WalletTxKind.PURCHASE -> R.string.wallet_tx_purchase
        WalletTxKind.ADJUSTMENT -> R.string.wallet_tx_adjustment
        WalletTxKind.UNKNOWN -> R.string.wallet_tx_other
    }
    val positive = tx.amount >= 0
    NexoraCard(Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(title), style = MaterialTheme.typography.titleSmall)
                val sub = listOfNotNull(
                    tx.createdAtEpochMs?.let {
                        DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT).format(Date(it))
                    },
                    tx.note,
                ).joinToString(" · ")
                if (sub.isNotEmpty()) {
                    Text(
                        sub,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            Text(
                (if (positive) "+" else "−") + Formatting.price(kotlin.math.abs(tx.amount)),
                style = MaterialTheme.typography.titleMedium,
                color = if (positive) MaterialTheme.colorScheme.primary else Color.Unspecified,
            )
        }
    }
}
