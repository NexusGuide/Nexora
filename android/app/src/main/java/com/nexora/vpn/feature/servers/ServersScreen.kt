@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.servers

import android.app.Activity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.StarBorder
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.ui.ConnectedGreen
import com.nexora.vpn.core.ui.ConnectingAmber
import com.nexora.vpn.core.ui.DangerRed
import com.nexora.vpn.core.ui.EmptyState
import com.nexora.vpn.core.ui.ErrorState
import com.nexora.vpn.core.ui.FlagBadge
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.LoadingState
import com.nexora.vpn.core.ui.NexoraCard
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen
import com.nexora.vpn.core.vpn.ServerInfo

@Composable
fun ServersScreen(
    onOpenServer: (String) -> Unit,
    viewModel: ServersViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.servers_title)) },
                actions = {
                    IconButton(onClick = viewModel::pingAll) {
                        Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.servers_test_all))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background),
            )
        },
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            val catalog = state.catalog
            when {
                catalog.isLoading && catalog.servers.isEmpty() -> LoadingState()
                catalog.error != null && catalog.servers.isEmpty() ->
                    ErrorState(error = catalog.error, onRetry = viewModel::refresh)
                catalog.servers.isEmpty() -> EmptyState(
                    title = stringResource(R.string.servers_empty),
                    body = stringResource(R.string.servers_empty_body),
                )
                else -> ServerList(state, viewModel, onOpenServer)
            }
        }
    }
}

@Composable
private fun ServerList(state: ServersUiState, viewModel: ServersViewModel, onOpenServer: (String) -> Unit) {
    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = state.query,
            onValueChange = viewModel::onQuery,
            placeholder = { Text(stringResource(R.string.servers_search)) },
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            singleLine = true,
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.fillMaxWidth().padding(horizontal = Spacing.md),
        )

        val filters = buildList<ServerFilter> {
            add(ServerFilter.All)
            if (state.favourites.isNotEmpty()) add(ServerFilter.Favourites)
            // A region chip only when there is more than one region to choose.
            if (state.regions.size > 1) state.regions.forEach { add(ServerFilter.InRegion(it)) }
        }
        LazyRow(
            contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.sm),
            horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            items(filters) { f ->
                FilterChip(
                    selected = state.filter == f,
                    onClick = { viewModel.onFilter(f) },
                    label = { Text(filterLabel(f)) },
                )
            }
        }

        LazyColumn(
            contentPadding = PaddingValues(start = Spacing.md, end = Spacing.md, bottom = Spacing.lg),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            items(state.visible, key = { it.id }) { server ->
                ServerRow(
                    server = server,
                    ping = state.catalog.pings[server.id],
                    isSelected = server.id == state.selectedId,
                    isConnected = server.config.name == state.connectedName,
                    isFavourite = server.id in state.favourites,
                    onSelect = { viewModel.select(server) },
                    onFavourite = { viewModel.toggleFavourite(server) },
                    onDetails = { onOpenServer(server.id) },
                )
            }
        }
    }
}

@Composable
private fun filterLabel(filter: ServerFilter): String = when (filter) {
    ServerFilter.All -> stringResource(R.string.servers_all)
    ServerFilter.Favourites -> stringResource(R.string.servers_favourites)
    is ServerFilter.InRegion -> stringResource(regionLabel(filter.region))
}

internal fun regionLabel(region: ServerInfo.Region): Int = when (region) {
    ServerInfo.Region.EUROPE -> R.string.region_europe
    ServerInfo.Region.ASIA -> R.string.region_asia
    ServerInfo.Region.AMERICA -> R.string.region_america
    ServerInfo.Region.OTHER -> R.string.region_other
}

@Composable
private fun ServerRow(
    server: Server,
    ping: Ping?,
    isSelected: Boolean,
    isConnected: Boolean,
    isFavourite: Boolean,
    onSelect: () -> Unit,
    onFavourite: () -> Unit,
    onDetails: () -> Unit,
) {
    NexoraCard(Modifier.fillMaxWidth(), onClick = onSelect) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            FlagBadge(server.info.flag)
            Column(
                Modifier
                    .weight(1f)
                    .padding(horizontal = Spacing.md)
                    .clickable(onClick = onDetails),
            ) {
                Text(
                    server.info.label,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    when {
                        isConnected -> stringResource(R.string.servers_connected)
                        isSelected -> stringResource(R.string.servers_selected)
                        else -> stringResource(R.string.servers_details)
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = if (isConnected) ConnectedGreen else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            PingText(ping)
            IconButton(onClick = onFavourite) {
                Icon(
                    if (isFavourite) Icons.Filled.Star else Icons.Filled.StarBorder,
                    contentDescription = stringResource(R.string.servers_favourite),
                    tint = if (isFavourite) ConnectingAmber else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (isSelected) {
                Icon(
                    Icons.Filled.CheckCircle,
                    contentDescription = stringResource(R.string.servers_selected),
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

/** Green under 150 ms, amber under 400, red above — and the number, always. */
@Composable
internal fun PingText(ping: Ping?) {
    when (ping) {
        null -> Text("—", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Ping.Measuring -> CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
        Ping.Timeout -> Text(
            stringResource(R.string.servers_timeout),
            style = MaterialTheme.typography.labelMedium,
            color = DangerRed,
        )
        is Ping.Ms -> Text(
            stringResource(R.string.servers_ms, ping.value),
            style = MaterialTheme.typography.labelMedium,
            color = when {
                ping.value < 150 -> ConnectedGreen
                ping.value < 400 -> ConnectingAmber
                else -> DangerRed
            },
        )
    }
}

@Composable
fun ServerDetailScreen(
    serverId: String,
    onBack: () -> Unit,
    viewModel: ServersViewModel = hiltViewModel(),
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val server = state.catalog.servers.firstOrNull { it.id == serverId }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK && server != null) {
            viewModel.connect(server)
            onBack()
        }
    }

    SubScreen(
        title = stringResource(R.string.server_details_title),
        onBack = onBack,
        actions = {
            if (server != null) {
                IconButton(onClick = { viewModel.toggleFavourite(server) }) {
                    Icon(
                        if (server.id in state.favourites) Icons.Filled.Star else Icons.Filled.StarBorder,
                        contentDescription = stringResource(R.string.servers_favourite),
                    )
                }
            }
        },
    ) { padding ->
        if (server == null) {
            LoadingState(Modifier.padding(padding))
            return@SubScreen
        }
        val spec = remember(server.id) { ServersViewModel.specOf(server) }
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(Spacing.md),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            FlagBadge(server.info.flag, size = 72.dp)
            Gap(Spacing.sm)
            Text(server.info.label, style = MaterialTheme.typography.headlineSmall)
            Text(
                stringResource(regionLabel(server.info.region)),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Gap()

            NexoraCard(Modifier.fillMaxWidth()) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        stringResource(R.string.home_ping),
                        style = MaterialTheme.typography.labelLarge,
                        modifier = Modifier.weight(1f),
                    )
                    PingText(state.catalog.pings[server.id])
                    IconButton(onClick = { viewModel.ping(server) }) {
                        Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.servers_test))
                    }
                }
            }
            Gap(Spacing.sm)

            if (spec == null) {
                NexoraCard(Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.vpn_error_unsupported), color = DangerRed)
                }
            } else {
                NexoraCard(Modifier.fillMaxWidth()) {
                    SpecRow(stringResource(R.string.server_protocol), spec.protocol)
                    SpecRow(stringResource(R.string.server_transport), spec.transport)
                    SpecRow(stringResource(R.string.server_security), spec.security)
                    SpecRow(stringResource(R.string.server_port), spec.port.toString())
                }
            }
            Gap(Spacing.lg)

            val isConnected = server.config.name == state.connectedName
            if (isConnected) {
                OutlinedButton(onClick = {}, enabled = false, modifier = Modifier.fillMaxWidth()) {
                    Text(stringResource(R.string.servers_connected))
                }
            } else {
                Button(
                    onClick = {
                        val intent = viewModel.permissionIntent()
                        if (intent != null) {
                            permissionLauncher.launch(intent)
                        } else {
                            viewModel.connect(server)
                            onBack()
                        }
                    },
                    enabled = spec != null,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(stringResource(R.string.home_connect)) }
            }
        }
    }
}

@Composable
private fun SpecRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth().padding(vertical = Spacing.xs)) {
        Text(
            label,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        Text(value, style = MaterialTheme.typography.titleSmall)
    }
}
