package com.nexora.vpn.feature.wallet

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import android.widget.Toast
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.common.Payments
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.InlineSpinner
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen
import com.nexora.vpn.core.ui.UiState
import com.nexora.vpn.core.ui.messageRes
import com.nexora.vpn.domain.model.PaymentMethods

@Composable
fun TopUpScreen(
    onBack: () -> Unit,
    onDone: () -> Unit,
    viewModel: TopUpViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    SubScreen(title = stringResource(R.string.topup_title), onBack = onBack) { padding ->
        when (val methods = state.methods) {
            is UiState.Loading -> LoadingState(Modifier.padding(padding))
            is UiState.Failed -> ErrorState(methods.error, onRetry = viewModel::load, modifier = Modifier.padding(padding))
            // Nothing configured by the operator yet: say so rather than show an empty form.
            is UiState.Empty -> EmptyState(
                title = stringResource(R.string.topup_unavailable),
                body = stringResource(R.string.topup_unavailable_body),
                modifier = Modifier.padding(padding),
            )
            is UiState.Content -> {
                val submitted = state.submitted
                if (submitted != null) {
                    Submitted(
                        forOrder = submitted.orderId != null,
                        onDone = onDone,
                        modifier = Modifier.padding(padding),
                    )
                } else {
                    Form(state, methods.data, viewModel, Modifier.padding(padding))
                }
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
private fun Form(
    state: TopUpUiState,
    methods: PaymentMethods,
    viewModel: TopUpViewModel,
    modifier: Modifier,
) {
    val isCrypto = state.target is PayTarget.Crypto

    Column(
        modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .imePadding()
            .padding(horizontal = Spacing.md),
    ) {
        if (state.orderId != null) {
            Text(
                stringResource(R.string.topup_for_order),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = Spacing.sm),
            )
        }

        // 1. Amount
        SectionTitle(stringResource(R.string.topup_step_amount))
        OutlinedTextField(
            value = state.amountText,
            onValueChange = viewModel::setAmount,
            label = { Text(stringResource(R.string.topup_amount_label)) },
            supportingText = {
                Text(
                    when (state.fieldError) {
                        TopUpFieldError.AMOUNT_INVALID -> stringResource(R.string.topup_amount_invalid)
                        TopUpFieldError.AMOUNT_RANGE -> stringResource(
                            R.string.topup_amount_range,
                            Formatting.price(methods.minTopup),
                            Formatting.price(methods.maxTopup),
                        )
                        else -> state.amount?.let { stringResource(R.string.money_toman, Formatting.price(it)) }
                            ?: stringResource(
                                R.string.topup_amount_range,
                                Formatting.price(methods.minTopup),
                                Formatting.price(methods.maxTopup),
                            )
                    },
                )
            },
            isError = state.fieldError == TopUpFieldError.AMOUNT_INVALID ||
                state.fieldError == TopUpFieldError.AMOUNT_RANGE,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )

        // 2. Method
        SectionTitle(stringResource(R.string.topup_step_method))
        Row(
            Modifier.horizontalScroll(rememberScrollState()),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            if (methods.card != null) {
                FilterChip(
                    selected = state.target == PayTarget.Card,
                    onClick = { viewModel.select(PayTarget.Card) },
                    label = { Text(stringResource(R.string.topup_method_card)) },
                )
            }
            methods.crypto.forEachIndexed { i, w ->
                FilterChip(
                    selected = state.target == PayTarget.Crypto(i),
                    onClick = { viewModel.select(PayTarget.Crypto(i)) },
                    label = { Text("${w.asset} · ${w.network}") },
                )
            }
        }

        // 3. Where to send it
        SectionTitle(stringResource(R.string.topup_step_send))
        NexoraCard(Modifier.fillMaxWidth()) {
            val card = methods.card
            val wallet = state.selectedWallet
            when {
                state.target == PayTarget.Card && card != null -> {
                    Text(
                        stringResource(R.string.topup_send_card, Formatting.price(state.amount ?: 0)),
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    CopyLine(
                        label = stringResource(R.string.topup_card_number),
                        shown = Payments.groupCard(card.number),
                        copied = card.number,
                    )
                    InfoLine(stringResource(R.string.topup_card_holder), card.holder)
                    if (card.bank.isNotBlank()) InfoLine(stringResource(R.string.topup_card_bank), card.bank)
                    card.instructions.takeIf { it.isNotBlank() }?.let { Note(it) }
                }
                wallet != null -> {
                    val quote = state.cryptoQuote
                    Text(
                        if (quote != null) {
                            stringResource(R.string.topup_send_crypto, quote, wallet.asset, wallet.network)
                        } else {
                            stringResource(R.string.topup_enter_amount_first)
                        },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    if (quote != null) {
                        CopyLine(stringResource(R.string.topup_crypto_amount), "$quote ${wallet.asset}", quote)
                    }
                    CopyLine(stringResource(R.string.topup_crypto_address), wallet.address, wallet.address)
                    InfoLine(
                        stringResource(R.string.topup_crypto_rate),
                        stringResource(R.string.topup_rate_value, wallet.asset, Formatting.price(wallet.rate.substringBefore('.').toLongOrNull() ?: 0)),
                    )
                    Note(stringResource(R.string.topup_crypto_warning, wallet.network))
                    methods.cryptoInstructions.takeIf { it.isNotBlank() }?.let { Note(it) }
                }
            }
        }

        // 4. Receipt
        SectionTitle(stringResource(R.string.topup_step_receipt))
        OutlinedTextField(
            value = state.reference,
            onValueChange = viewModel::setReference,
            label = {
                Text(stringResource(if (isCrypto) R.string.topup_reference_crypto else R.string.topup_reference_card))
            },
            supportingText = {
                Text(
                    stringResource(
                        when {
                            state.fieldError == TopUpFieldError.REFERENCE && isCrypto -> R.string.topup_reference_crypto_invalid
                            state.fieldError == TopUpFieldError.REFERENCE -> R.string.topup_reference_card_invalid
                            isCrypto -> R.string.topup_reference_crypto_hint
                            else -> R.string.topup_reference_card_hint
                        },
                    ),
                )
            },
            isError = state.fieldError == TopUpFieldError.REFERENCE,
            singleLine = !isCrypto,
            textStyle = MaterialTheme.typography.bodyLarge.copy(textDirection = TextDirection.Ltr),
            keyboardOptions = KeyboardOptions(keyboardType = if (isCrypto) KeyboardType.Ascii else KeyboardType.Number),
            modifier = Modifier.fillMaxWidth(),
        )
        if (!isCrypto) {
            OutlinedTextField(
                value = state.payerNote,
                onValueChange = viewModel::setNote,
                label = { Text(stringResource(R.string.topup_note_label)) },
                supportingText = { Text(stringResource(R.string.topup_note_hint)) },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
        }

        Gap()
        Button(
            onClick = viewModel::submit,
            enabled = !state.submitting && state.target != null,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (state.submitting) InlineSpinner() else Text(stringResource(R.string.topup_submit))
        }
        Text(
            stringResource(R.string.topup_review_note),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(vertical = Spacing.sm),
        )
        Gap(Spacing.lg)
    }
}

@Composable
private fun CopyLine(label: String, shown: String, copied: String) {
    val clipboard = LocalClipboardManager.current
    val context = LocalContext.current
    val copiedText = stringResource(R.string.action_copied)
    Row(Modifier.fillMaxWidth().padding(top = Spacing.sm), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(
                shown,
                style = MaterialTheme.typography.titleMedium.copy(textDirection = TextDirection.Ltr),
                fontFamily = FontFamily.Monospace,
            )
        }
        IconButton(onClick = {
            clipboard.setText(AnnotatedString(copied))
            Toast.makeText(context, copiedText, Toast.LENGTH_SHORT).show()
        }) {
            Icon(Icons.Filled.ContentCopy, contentDescription = stringResource(R.string.action_copy))
        }
    }
}

@Composable
private fun InfoLine(label: String, value: String) {
    Column(Modifier.fillMaxWidth().padding(top = Spacing.sm)) {
        Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, style = MaterialTheme.typography.titleMedium)
    }
}

@Composable
private fun Note(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = Spacing.sm),
    )
}

@Composable
private fun Submitted(forOrder: Boolean, onDone: () -> Unit, modifier: Modifier) {
    Column(
        modifier.fillMaxSize().padding(Spacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            Icons.Filled.CheckCircle,
            contentDescription = null,
            tint = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(bottom = Spacing.md).size(72.dp),
        )
        Text(
            stringResource(R.string.topup_submitted_title),
            style = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center,
        )
        Text(
            stringResource(if (forOrder) R.string.topup_submitted_order else R.string.topup_submitted_body),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
            modifier = Modifier.padding(vertical = Spacing.md),
        )
        Button(onClick = onDone, modifier = Modifier.fillMaxWidth().padding(top = 8.dp)) {
            Text(stringResource(R.string.topup_go_wallet))
        }
    }
}
