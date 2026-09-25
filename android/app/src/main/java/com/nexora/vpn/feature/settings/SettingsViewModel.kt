package com.nexora.vpn.feature.settings

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.core.graphics.drawable.toBitmap
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.nexora.vpn.core.settings.AppRoutingMode
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.settings.DnsMode
import com.nexora.vpn.core.settings.RoutingMode
import com.nexora.vpn.core.settings.Settings
import com.nexora.vpn.core.settings.ThemeMode
import com.nexora.vpn.core.vpn.VpnController
import com.nexora.vpn.core.vpn.VpnState
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** An installed app a person can open, for the per-app routing list. */
data class InstalledApp(val packageName: String, val label: String, val icon: ImageBitmap?)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val appSettings: AppSettings,
    private val vpn: VpnController,
) : ViewModel() {

    val settings: StateFlow<Settings> = appSettings.settings

    private val _apps = MutableStateFlow<List<InstalledApp>?>(null)
    val apps: StateFlow<List<InstalledApp>?> = _apps.asStateFlow()

    /** True while connected: changes to DNS/routing/apps apply on the next connection. */
    val isConnected: Boolean get() = vpn.state.value is VpnState.Connected

    fun setTheme(value: ThemeMode) = appSettings.setTheme(value)
    fun setAccent(value: Int) = appSettings.setAccent(value)
    fun setAutoConnect(value: Boolean) = appSettings.setAutoConnect(value)
    fun setDns(value: DnsMode) = appSettings.setDns(value)
    fun setCustomDns(raw: String): Boolean {
        val parsed = AppSettings.parseDnsList(raw)
        if (parsed.isEmpty()) return false
        appSettings.setCustomDns(raw)
        return true
    }
    fun setRouting(value: RoutingMode) = appSettings.setRouting(value)
    fun setAppRouting(value: AppRoutingMode) = appSettings.setAppRouting(value)

    fun toggleApp(packageName: String) {
        val current = settings.value.selectedApps
        appSettings.setSelectedApps(
            if (packageName in current) current - packageName else current + packageName,
        )
    }

    /**
     * Apps with a launcher icon — what a person thinks of as "their apps".
     * Declared in the manifest's <queries> so Android 11+ shows them; no
     * QUERY_ALL_PACKAGES, which Play reserves for apps that need everything.
     */
    fun loadApps() {
        if (_apps.value != null) return
        viewModelScope.launch {
            _apps.value = withContext(Dispatchers.IO) {
                val pm = context.packageManager
                val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
                @Suppress("DEPRECATION")
                pm.queryIntentActivities(intent, PackageManager.MATCH_ALL)
                    .asSequence()
                    .map { it.activityInfo.applicationInfo }
                    .filter { it.packageName != context.packageName }
                    .distinctBy { it.packageName }
                    .map { info ->
                        InstalledApp(
                            packageName = info.packageName,
                            label = pm.getApplicationLabel(info).toString(),
                            icon = runCatching {
                                pm.getApplicationIcon(info).toBitmap(96, 96).asImageBitmap()
                            }.getOrNull(),
                        )
                    }
                    .sortedBy { it.label.lowercase() }
                    .toList()
            }
        }
    }
}
