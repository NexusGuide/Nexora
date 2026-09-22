package com.nexora.vpn.core.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.nexora.vpn.R
import com.nexora.vpn.core.common.AppError

/**
 * The shared pieces every screen uses for its non-content states.
 *
 * Centralised so "loading", "empty" and "failed" look and behave the same
 * everywhere — and so a retry is offered exactly when retrying can help.
 */

@Composable
fun LoadingState(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator()
    }
}

@Composable
fun EmptyState(
    title: String,
    body: String? = null,
    actionLabel: String? = null,
    onAction: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxSize().padding(Spacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.titleLarge,
            textAlign = TextAlign.Center,
        )
        if (body != null) {
            Text(
                text = body,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(top = Spacing.sm),
            )
        }
        if (actionLabel != null && onAction != null) {
            Button(
                onClick = onAction,
                modifier = Modifier.padding(top = Spacing.lg),
            ) {
                Text(actionLabel)
            }
        }
    }
}

@Composable
fun ErrorState(
    error: AppError,
    onRetry: (() -> Unit)? = null,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier.fillMaxSize().padding(Spacing.lg),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = stringResource(error.messageRes()),
            style = MaterialTheme.typography.bodyLarge,
            textAlign = TextAlign.Center,
        )

        // The request id is the only thing support can search on, so it is
        // shown rather than swallowed — quietly, in small type.
        (error as? AppError.Server)?.requestId?.let { requestId ->
            Text(
                text = stringResource(R.string.error_request_id, requestId),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(top = Spacing.xs),
            )
        }

        // Retry is offered only when retrying could actually work. A retry
        // button on a rejected request just invites the same rejection.
        if (onRetry != null && error.isRetryable) {
            OutlinedButton(
                onClick = onRetry,
                modifier = Modifier.padding(top = Spacing.lg),
            ) {
                Text(stringResource(R.string.action_retry))
            }
        }
    }
}

/** A labelled figure, as used across Home and the service cards. */
@Composable
fun StatBlock(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = MaterialTheme.colorScheme.onSurface,
) {
    Column(modifier) {
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(
            text = value,
            style = MaterialTheme.typography.titleMedium,
            color = valueColor,
        )
    }
}

/**
 * Traffic bar. Renders nothing when [fraction] is null, which is how an
 * unlimited plan is expressed — an empty bar would imply a quota that does
 * not exist.
 */
@Composable
fun TrafficBar(
    fraction: Float?,
    modifier: Modifier = Modifier,
) {
    if (fraction == null) return

    val colour = when {
        fraction >= 0.9f -> MaterialTheme.colorScheme.error
        fraction >= 0.75f -> ConnectingAmber
        else -> MaterialTheme.colorScheme.primary
    }

    LinearProgressIndicator(
        progress = { fraction },
        color = colour,
        trackColor = MaterialTheme.colorScheme.surfaceVariant,
        modifier = modifier.fillMaxWidth().padding(vertical = Spacing.sm),
    )
}

/** Small status pill — always paired with its label, never colour alone. */
@Composable
fun StatusPill(
    text: String,
    colour: Color,
    modifier: Modifier = Modifier,
) {
    Surface(
        color = colour.copy(alpha = 0.15f),
        contentColor = colour,
        shape = RoundedCornerShape(50),
        modifier = modifier,
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelLarge,
            modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.xs),
        )
    }
}

@Composable
fun InlineSpinner(modifier: Modifier = Modifier) {
    Row(
        modifier = modifier,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        CircularProgressIndicator(
            modifier = Modifier.size(16.dp),
            strokeWidth = 2.dp,
        )
    }
}
