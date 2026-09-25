@file:OptIn(ExperimentalMaterial3Api::class)

package com.nexora.vpn.feature.settings

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings as AndroidSettings
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Apps
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Palette
import androidx.compose.material.icons.filled.PlayCircle
import androidx.compose.material.icons.filled.Directions
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.automirrored.filled.Subject
import androidx.compose.material.icons.filled.SupportAgent
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.R
import com.nexora.vpn.core.settings.AppRoutingMode
import com.nexora.vpn.core.settings.DnsMode
import com.nexora.vpn.core.settings.RoutingMode
import com.nexora.vpn.core.settings.ThemeMode
import com.nexora.vpn.core.ui.Accents
import com.nexora.vpn.core.ui.ChoiceRow
import com.nexora.vpn.core.ui.Gap
import com.nexora.vpn.core.ui.SectionTitle
import com.nexora.vpn.core.ui.SettingsRow
import com.nexora.vpn.core.ui.Spacing
import com.nexora.vpn.core.ui.SubScreen

/** Where the settings screens can go. */
data class SettingsNav(
    val onBack: () -> Unit,
    val openAppearance: () -> Unit,
    val openDns: () -> Unit,
    val openRouting: () -> Unit,
    val openAppRouting: () -> Unit,
    val openHistory: () -> Unit,
    val openLogs: () -> Unit,
    val openHelp: () -> Unit,
    val openAbout: () -> Unit,
)

@Composable
fun SettingsScreen(nav: SettingsNav, viewModel: SettingsViewModel = hiltViewModel()) {
    val s by viewModel.settings.collectAsStateWithLifecycle()
    val context = LocalContext.current

    SubScreen(title = stringResource(R.string.settings_title), onBack = nav.onBack) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
        ) {
            SectionTitle(stringResource(R.string.settings_connection))
            SettingsRow(
                icon = Icons.Filled.PlayCircle,
                title = stringResource(R.string.settings_auto_connect),
                subtitle = stringResource(R.string.settings_auto_connect_body),
                trailing = { Switch(checked = s.autoConnect, onCheckedChange = viewModel::setAutoConnect) },
            )
            // Android enforces a kill switch only system-wide ("Always-on VPN"
            // + "Block connections without VPN"). An app cannot turn it on
            // itself, so this opens the page where the customer can.
            SettingsRow(
                icon = Icons.Filled.Shield,
                title = stringResource(R.string.settings_kill_switch),
                subtitle = stringResource(R.string.settings_kill_switch_body),
                onClick = {
                    runCatching {
                        context.startActivity(
                            Intent(AndroidSettings.ACTION_VPN_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    }
                },
            )
            SettingsRow(
                icon = Icons.Filled.Dns,
                title = stringResource(R.string.settings_dns),
                subtitle = stringResource(dnsLabel(s.dns)),
                onClick = nav.openDns,
            )
            SettingsRow(
                icon = Icons.Filled.Directions,
                title = stringResource(R.string.settings_routing),
                subtitle = stringResource(routingLabel(s.routing)),
                onClick = nav.openRouting,
            )
            SettingsRow(
                icon = Icons.Filled.Apps,
                title = stringResource(R.string.settings_app_routing),
                subtitle = stringResource(appRoutingLabel(s.appRouting)),
                onClick = nav.openAppRouting,
            )

            SectionTitle(stringResource(R.string.settings_app))
            SettingsRow(
                icon = Icons.Filled.Palette,
                title = stringResource(R.string.settings_appearance),
                subtitle = stringResource(themeLabel(s.theme)),
                onClick = nav.openAppearance,
            )
            // Per-app language is a system feature from Android 13; the app
            // follows the phone's language before that.
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                SettingsRow(
                    icon = Icons.Filled.Language,
                    title = stringResource(R.string.settings_language),
                    subtitle = stringResource(R.string.settings_language_body),
                    onClick = {
                        runCatching {
                            context.startActivity(
                                Intent(AndroidSettings.ACTION_APP_LOCALE_SETTINGS)
                                    .setData(Uri.fromParts("package", context.packageName, null))
                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                            )
                        }
                    },
                )
            }
            SettingsRow(
                icon = Icons.Filled.History,
                title = stringResource(R.string.history_title),
                onClick = nav.openHistory,
            )
            SettingsRow(
                icon = Icons.AutoMirrored.Filled.Subject,
                title = stringResource(R.string.logs_title),
                onClick = nav.openLogs,
            )

            SectionTitle(stringResource(R.string.settings_more))
            SettingsRow(
                icon = Icons.Filled.SupportAgent,
                title = stringResource(R.string.help_title),
                onClick = nav.openHelp,
            )
            SettingsRow(
                icon = Icons.Filled.Info,
                title = stringResource(R.string.about_title),
                onClick = nav.openAbout,
            )
            Gap(Spacing.lg)
        }
    }
}

/** Changes to the tunnel's settings take effect on the next connection. */
@Composable
private fun AppliesOnReconnect(connected: Boolean) {
    if (!connected) return
    Text(
        stringResource(R.string.settings_applies_on_reconnect),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(vertical = Spacing.sm),
    )
}

@Composable
fun AppearanceScreen(onBack: () -> Unit, viewModel: SettingsViewModel = hiltViewModel()) {
    val s by viewModel.settings.collectAsStateWithLifecycle()
    SubScreen(title = stringResource(R.string.settings_appearance), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            SectionTitle(stringResource(R.string.appearance_theme))
            ThemeMode.entries.forEach { mode ->
                ChoiceRow(
                    title = stringResource(themeLabel(mode)),
                    selected = s.theme == mode,
                    onClick = { viewModel.setTheme(mode) },
                )
            }
            SectionTitle(stringResource(R.string.appearance_accent))
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
                Accents.all.forEachIndexed { index, accent ->
                    val selected = s.accent == index
                    Box(
                        modifier = Modifier
                            .size(40.dp)
                            .clip(CircleShape)
                            .background(accent.dark)
                            .border(
                                width = if (selected) 3.dp else 0.dp,
                                color = MaterialTheme.colorScheme.onBackground,
                                shape = CircleShape,
                            )
                            .clickable { viewModel.setAccent(index) },
                        contentAlignment = Alignment.Center,
                    ) {
                        if (selected) Icon(Icons.Filled.Check, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimary)
                    }
                }
            }
        }
    }
}

@Composable
fun DnsScreen(onBack: () -> Unit, viewModel: SettingsViewModel = hiltViewModel()) {
    val s by viewModel.settings.collectAsStateWithLifecycle()
    var custom by rememberSaveable { mutableStateOf(s.customDns.joinToString(", ")) }
    var invalid by remember { mutableStateOf(false) }

    SubScreen(title = stringResource(R.string.settings_dns), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState())
                .padding(horizontal = Spacing.md),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            Text(
                stringResource(R.string.dns_body),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(vertical = Spacing.sm),
            )
            AppliesOnReconnect(viewModel.isConnected)
            DnsMode.entries.forEach { mode ->
                ChoiceRow(
                    title = stringResource(dnsLabel(mode)),
                    subtitle = if (mode == DnsMode.CUSTOM) null else mode.servers.joinToString(" / "),
                    selected = s.dns == mode,
                    onClick = { viewModel.setDns(mode) },
                )
            }
            if (s.dns == DnsMode.CUSTOM) {
                OutlinedTextField(
                    value = custom,
                    onValueChange = { custom = it; invalid = false },
                    label = { Text(stringResource(R.string.dns_custom_hint)) },
                    isError = invalid,
                    supportingText = if (invalid) {
                        { Text(stringResource(R.string.dns_custom_invalid)) }
                    } else {
                        null
                    },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Button(
                    onClick = { invalid = !viewModel.setCustomDns(custom) },
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(stringResource(R.string.action_save)) }
            }
        }
    }
}

@Composable
fun RoutingScreen(onBack: () -> Unit, viewModel: SettingsViewModel = hiltViewModel()) {
    val s by viewModel.settings.collectAsStateWithLifecycle()
    SubScreen(title = stringResource(R.string.settings_routing), onBack = onBack) { padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().padding(horizontal = Spacing.md),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            Gap(Spacing.sm)
            AppliesOnReconnect(viewModel.isConnected)
            ChoiceRow(
                title = stringResource(R.string.routing_bypass_iran),
                subtitle = stringResource(R.string.routing_bypass_iran_body),
                selected = s.routing == RoutingMode.BYPASS_IRAN,
                onClick = { viewModel.setRouting(RoutingMode.BYPASS_IRAN) },
            )
            ChoiceRow(
                title = stringResource(R.string.routing_global),
                subtitle = stringResource(R.string.routing_global_body),
                selected = s.routing == RoutingMode.GLOBAL,
                onClick = { viewModel.setRouting(RoutingMode.GLOBAL) },
            )
        }
    }
}

@Composable
fun AppRoutingScreen(onBack: () -> Unit, viewModel: SettingsViewModel = hiltViewModel()) {
    val s by viewModel.settings.collectAsStateWithLifecycle()
    val apps by viewModel.apps.collectAsStateWithLifecycle()
    var query by rememberSaveable { mutableStateOf("") }

    // The app list is only needed once a selection mode is chosen.
    LaunchedEffect(s.appRouting) {
        if (s.appRouting != AppRoutingMode.ALL) viewModel.loadApps()
    }

    SubScreen(title = stringResource(R.string.settings_app_routing), onBack = onBack) { padding ->
        LazyColumn(
            Modifier.padding(padding).fillMaxSize(),
            contentPadding = PaddingValues(horizontal = Spacing.md, vertical = Spacing.sm),
            verticalArrangement = Arrangement.spacedBy(Spacing.sm),
        ) {
            item { AppliesOnReconnect(viewModel.isConnected) }
            AppRoutingMode.entries.forEach { mode ->
                item {
                    ChoiceRow(
                        title = stringResource(appRoutingLabel(mode)),
                        subtitle = stringResource(appRoutingBody(mode)),
                        selected = s.appRouting == mode,
                        onClick = { viewModel.setAppRouting(mode) },
                    )
                }
            }
            if (s.appRouting != AppRoutingMode.ALL) {
                item {
                    SectionTitle(stringResource(R.string.app_routing_selected, s.selectedApps.size))
                    OutlinedTextField(
                        value = query,
                        onValueChange = { query = it },
                        placeholder = { Text(stringResource(R.string.servers_search)) },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
                val list = apps
                if (list == null) {
                    item {
                        Box(Modifier.fillMaxWidth().padding(Spacing.lg), contentAlignment = Alignment.Center) {
                            CircularProgressIndicator()
                        }
                    }
                } else {
                    val needle = query.trim().lowercase()
                    val shown = list.filter {
                        needle.isEmpty() || it.label.lowercase().contains(needle) ||
                            it.packageName.contains(needle)
                    }
                    items(shown, key = { it.packageName }) { app ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clip(RoundedCornerShape(12.dp))
                                .clickable { viewModel.toggleApp(app.packageName) }
                                .padding(vertical = Spacing.xs),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            val icon = app.icon
                            if (icon != null) {
                                Image(icon, contentDescription = null, modifier = Modifier.size(36.dp))
                            } else {
                                Box(Modifier.size(36.dp))
                            }
                            Column(Modifier.weight(1f).padding(horizontal = Spacing.md)) {
                                Text(app.label, style = MaterialTheme.typography.titleSmall)
                                Text(
                                    app.packageName,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Checkbox(
                                checked = app.packageName in s.selectedApps,
                                onCheckedChange = { viewModel.toggleApp(app.packageName) },
                            )
                        }
                    }
                }
            }
        }
    }
}

// --- labels -----------------------------------------------------------------------

internal fun themeLabel(mode: ThemeMode): Int = when (mode) {
    ThemeMode.SYSTEM -> R.string.theme_system
    ThemeMode.LIGHT -> R.string.theme_light
    ThemeMode.DARK -> R.string.theme_dark
    ThemeMode.AMOLED -> R.string.theme_amoled
}

internal fun dnsLabel(mode: DnsMode): Int = when (mode) {
    DnsMode.AUTOMATIC -> R.string.dns_automatic
    DnsMode.CLOUDFLARE -> R.string.dns_cloudflare
    DnsMode.GOOGLE -> R.string.dns_google
    DnsMode.QUAD9 -> R.string.dns_quad9
    DnsMode.CUSTOM -> R.string.dns_custom
}

internal fun routingLabel(mode: RoutingMode): Int = when (mode) {
    RoutingMode.BYPASS_IRAN -> R.string.routing_bypass_iran
    RoutingMode.GLOBAL -> R.string.routing_global
}

internal fun appRoutingLabel(mode: AppRoutingMode): Int = when (mode) {
    AppRoutingMode.ALL -> R.string.app_routing_all
    AppRoutingMode.ONLY_SELECTED -> R.string.app_routing_only
    AppRoutingMode.EXCEPT_SELECTED -> R.string.app_routing_except
}

internal fun appRoutingBody(mode: AppRoutingMode): Int = when (mode) {
    AppRoutingMode.ALL -> R.string.app_routing_all_body
    AppRoutingMode.ONLY_SELECTED -> R.string.app_routing_only_body
    AppRoutingMode.EXCEPT_SELECTED -> R.string.app_routing_except_body
}
