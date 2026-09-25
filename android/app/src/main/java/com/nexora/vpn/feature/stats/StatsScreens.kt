@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.stats

import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDownward
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.common.Formatting
import com.nexora.vpn.core.stats.ConnectionLog
import com.nexora.vpn.core.ui.BarChart
import com.nexora.vpn.core.ui.ConnectingAmber
import com.nexora.vpn.core.ui.DangerRed
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.EvenRow
import com.nexora.vpn.core.ui.FlagBadge
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.MetricCard
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen
import com.nexora.vpn.core.ui.TrafficBar
import com.nexora.vpn.core.vpn.ServerInfo
import java.text.DateFormat
import java.time.format.TextStyle
import java.util.Date
import java.util.Locale
import kotlinx.coroutines.delay

@Composable
fun StatsScreen(onOpenHistory: () -> Unit, viewModel: StatsViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.stats_title)) },
                actions = {
                    IconButton(onClick = onOpenHistory) {
                        Icon(Icons.Filled.History, contentDescription = stringResource(R.string.history_title))
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
            LazyRow(horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
                items(StatsPeriod.entries.toList()) { p ->
                    FilterChip(
                        selected = state.period == p,
                        onClick = { viewModel.setPeriod(p) },
                        label = { Text(stringResource(periodLabel(p))) },
                    )
                }
            }
            Gap(Spacing.sm)

            NexoraCard(Modifier.fillMaxWidth()) {
                Text(
                    stringResource(R.string.stats_total),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    Formatting.bytes(state.totalDown + state.totalUp),
                    style = MaterialTheme.typography.headlineMedium,
                )
                Text(
                    stringResource(R.string.stats_on_this_phone),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (state.period != StatsPeriod.TODAY) {
                    Gap(Spacing.md)
                    BarChart(
                        values = state.days.map { it.totalBytes },
                        labels = state.days.mapIndexed { i, d ->
                            // Every day for a week; every fifth day for a month.
                            if (state.period == StatsPeriod.WEEK || i % 5 == 0) {
                                if (state.period == StatsPeriod.WEEK) {
                                    d.day.dayOfWeek.getDisplayName(TextStyle.SHORT, Locale.getDefault())
                                } else {
                                    d.day.dayOfMonth.toString()
                                }
                            } else {
                                ""
                            }
                        },
                    )
                }
            }
            Gap(Spacing.sm)
            EvenRow {
                MetricCard(
                    Icons.Filled.ArrowDownward,
                    stringResource(R.string.home_download),
                    Formatting.bytes(state.totalDown),
                    Modifier.weight(1f),
                )
                MetricCard(
                    Icons.Filled.ArrowUpward,
                    stringResource(R.string.home_upload),
                    Formatting.bytes(state.totalUp),
                    Modifier.weight(1f),
                )
            }

            state.subscription?.let { sub ->
                SectionTitle(stringResource(R.string.stats_plan))
                NexoraCard(Modifier.fillMaxWidth()) {
                    Text(
                        if (sub.isUnlimitedTraffic) {
                            stringResource(R.string.store_unlimited)
                        } else {
                            Formatting.trafficRatio(sub.trafficUsedBytes, sub.trafficLimitBytes)
                        },
                        style = MaterialTheme.typography.titleMedium,
                    )
                    TrafficBar(fraction = sub.trafficFraction)
                    Text(
                        stringResource(R.string.stats_plan_note),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = Spacing.sm),
                    )
                }
            }

            if (state.topServers.isNotEmpty()) {
                SectionTitle(stringResource(R.string.stats_top_servers))
                state.topServers.forEach { usage ->
                    val info = remember(usage.serverName) { ServerInfo.from(usage.serverName) }
                    NexoraCard(Modifier.fillMaxWidth().padding(bottom = Spacing.sm)) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            FlagBadge(info.flag)
                            Column(Modifier.weight(1f).padding(horizontal = Spacing.md)) {
                                Text(info.label, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(
                                    stringResource(R.string.stats_sessions, usage.sessions),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Text(Formatting.bytes(usage.bytes), style = MaterialTheme.typography.titleSmall)
                        }
                    }
                }
            }
            Gap(Spacing.lg)
        }
    }
}

private fun periodLabel(p: StatsPeriod): Int = when (p) {
    StatsPeriod.TODAY -> R.string.stats_today
    StatsPeriod.WEEK -> R.string.stats_week
    StatsPeriod.MONTH -> R.string.stats_month
}

@Composable
fun HistoryScreen(onBack: () -> Unit, viewModel: StatsViewModel = hiltViewModel()) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    var confirmClear by remember { mutableStateOf(false) }
    val dateFormat = remember { DateFormat.getDateTimeInstance(DateFormat.MEDIUM, DateFormat.SHORT) }

    SubScreen(
        title = stringResource(R.string.history_title),
        onBack = onBack,
        actions = {
            if (state.sessions.isNotEmpty()) {
                IconButton(onClick = { confirmClear = true }) {
                    Icon(Icons.Filled.DeleteOutline, contentDescription = stringResource(R.string.history_clear))
                }
            }
        },
    ) { padding ->
        if (state.sessions.isEmpty()) {
            Column(Modifier.padding(padding)) {
                EmptyState(title = stringResource(R.string.history_empty))
            }
        } else {
            LazyColumn(
                Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(Spacing.md),
                verticalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                items(state.sessions) { record ->
                    val info = remember(record.serverName) { ServerInfo.from(record.serverName) }
                    NexoraCard(Modifier.fillMaxWidth()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            FlagBadge(info.flag)
                            Column(Modifier.weight(1f).padding(horizontal = Spacing.md)) {
                                Text(info.label, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text(
                                    dateFormat.format(Date(record.startedAtMs)),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Column(horizontalAlignment = Alignment.End) {
                                Text(Formatting.duration(record.durationMs / 1000), style = MaterialTheme.typography.titleSmall)
                                Text(
                                    Formatting.bytes(record.totalBytes),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    if (confirmClear) {
        AlertDialog(
            onDismissRequest = { confirmClear = false },
            title = { Text(stringResource(R.string.history_clear)) },
            text = { Text(stringResource(R.string.history_clear_body)) },
            confirmButton = {
                TextButton(onClick = {
                    confirmClear = false
                    viewModel.clearHistory()
                }) { Text(stringResource(R.string.history_clear)) }
            },
            dismissButton = {
                TextButton(onClick = { confirmClear = false }) { Text(stringResource(R.string.action_cancel)) }
            },
        )
    }
}

@Composable
fun LogsScreen(onBack: () -> Unit, viewModel: StatsViewModel = hiltViewModel()) {
    val log = viewModel.log
    var level by remember { mutableStateOf<ConnectionLog.Level?>(null) }
    // The log is a plain object; poll its version so the list follows it.
    var version by remember { mutableLongStateOf(log.version) }
    LaunchedEffect(Unit) {
        while (true) {
            version = log.version
            delay(1_000)
        }
    }
    val entries = remember(version, level) { log.entries(level).asReversed() }
    val context = LocalContext.current
    val timeFormat = remember { DateFormat.getTimeInstance(DateFormat.MEDIUM) }

    SubScreen(
        title = stringResource(R.string.logs_title),
        onBack = onBack,
        actions = {
            IconButton(onClick = {
                val text = log.export { timeFormat.format(Date(it)) }
                val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
                runCatching { context.startActivity(Intent.createChooser(send, null)) }
            }) { Icon(Icons.Filled.Share, contentDescription = stringResource(R.string.logs_share)) }
            IconButton(onClick = { log.clear(); version = log.version }) {
                Icon(Icons.Filled.DeleteOutline, contentDescription = stringResource(R.string.logs_clear))
            }
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            LazyRow(
                contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.sm),
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                val levels = listOf<ConnectionLog.Level?>(null) + ConnectionLog.Level.entries
                items(levels) { l ->
                    FilterChip(
                        selected = level == l,
                        onClick = { level = l },
                        label = { Text(stringResource(levelLabel(l))) },
                    )
                }
            }
            if (entries.isEmpty()) {
                EmptyState(title = stringResource(R.string.logs_empty))
            } else {
                LazyColumn(
                    contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.sm),
                    verticalArrangement = Arrangement.spacedBy(Spacing.xs),
                ) {
                    items(entries) { entry ->
                        Row {
                            Text(
                                timeFormat.format(Date(entry.atMs)),
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.padding(end = Spacing.sm),
                            )
                            Text(
                                entry.message,
                                style = MaterialTheme.typography.bodySmall,
                                color = when (entry.level) {
                                    ConnectionLog.Level.INFO -> MaterialTheme.colorScheme.onSurface
                                    ConnectionLog.Level.WARNING -> ConnectingAmber
                                    ConnectionLog.Level.ERROR -> DangerRed
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}

private fun levelLabel(level: ConnectionLog.Level?): Int = when (level) {
    null -> R.string.servers_all
    ConnectionLog.Level.INFO -> R.string.logs_info
    ConnectionLog.Level.WARNING -> R.string.logs_warning
    ConnectionLog.Level.ERROR -> R.string.logs_error
}
