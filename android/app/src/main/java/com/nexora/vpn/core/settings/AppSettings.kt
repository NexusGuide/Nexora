package com.nexora.vpn.core.settings

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

enum class ThemeMode { SYSTEM, LIGHT, DARK, AMOLED }

/**
 * Where DNS queries are resolved, through the tunnel. Addresses are the
 * providers' published ones; AUTOMATIC uses two providers for redundancy.
 */
enum class DnsMode(val servers: List<String>) {
    AUTOMATIC(listOf("1.1.1.1", "8.8.8.8")),
    CLOUDFLARE(listOf("1.1.1.1", "1.0.0.1")),
    GOOGLE(listOf("8.8.8.8", "8.8.4.4")),
    QUAD9(listOf("9.9.9.9", "149.112.112.112")),
    CUSTOM(emptyList()),
}

/**
 * GLOBAL sends everything through the VPN. BYPASS_IRAN sends Iranian sites
 * and addresses directly — they are often unreachable from abroad, and the
 * customer's plan should not pay for them.
 */
enum class RoutingMode { GLOBAL, BYPASS_IRAN }

/** Which apps use the VPN. */
enum class AppRoutingMode { ALL, ONLY_SELECTED, EXCEPT_SELECTED }

data class Settings(
    val theme: ThemeMode = ThemeMode.DARK,
    /** Index into [Accents.all]. */
    val accent: Int = 0,
    val autoConnect: Boolean = false,
    val dns: DnsMode = DnsMode.AUTOMATIC,
    val customDns: List<String> = emptyList(),
    val routing: RoutingMode = RoutingMode.BYPASS_IRAN,
    val appRouting: AppRoutingMode = AppRoutingMode.ALL,
    val selectedApps: Set<String> = emptySet(),
    val favouriteServers: Set<String> = emptySet(),
    /** The config the customer picked, by id; null means the backend's active one. */
    val selectedServerId: String? = null,
    val onboardingDone: Boolean = false,
) {
    /** The resolvers actually used: the custom list, or the mode's own. */
    val dnsServers: List<String>
        get() = if (dns == DnsMode.CUSTOM && customDns.isNotEmpty()) customDns
        else if (dns == DnsMode.CUSTOM) DnsMode.AUTOMATIC.servers
        else dns.servers
}

private val Context.settingsStore by preferencesDataStore(name = "settings")

/**
 * The customer's choices, kept on the phone. Preferences only — nothing here
 * is a secret, and nothing here is sent to the backend.
 */
@Singleton
class AppSettings @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private object Keys {
        val theme = stringPreferencesKey("theme")
        val accent = intPreferencesKey("accent")
        val autoConnect = booleanPreferencesKey("auto_connect")
        val dns = stringPreferencesKey("dns")
        val customDns = stringPreferencesKey("custom_dns")
        val routing = stringPreferencesKey("routing")
        val appRouting = stringPreferencesKey("app_routing")
        val selectedApps = stringSetPreferencesKey("selected_apps")
        val favourites = stringSetPreferencesKey("favourite_servers")
        val selectedServer = stringPreferencesKey("selected_server")
        val onboardingDone = booleanPreferencesKey("onboarding_done")
    }

    val settings: StateFlow<Settings> = context.settingsStore.data
        .map(::read)
        .stateIn(scope, SharingStarted.Eagerly, Settings())

    /**
     * False until the stored values have been read once. The first screen
     * depends on them (onboarding or not), and deciding from the defaults
     * would show onboarding again to someone who has already seen it.
     */
    val isLoaded: StateFlow<Boolean> = context.settingsStore.data
        .map { true }
        .stateIn(scope, SharingStarted.Eagerly, false)

    /** The latest value, for code that is not a coroutine (starting the tunnel). */
    fun current(): Settings = settings.value

    private fun read(p: Preferences) = Settings(
        theme = enumOr(p[Keys.theme], ThemeMode.DARK),
        accent = p[Keys.accent] ?: 0,
        autoConnect = p[Keys.autoConnect] ?: false,
        dns = enumOr(p[Keys.dns], DnsMode.AUTOMATIC),
        customDns = p[Keys.customDns].orEmpty().split(',').map { it.trim() }.filter { it.isNotEmpty() },
        routing = enumOr(p[Keys.routing], RoutingMode.BYPASS_IRAN),
        appRouting = enumOr(p[Keys.appRouting], AppRoutingMode.ALL),
        selectedApps = p[Keys.selectedApps].orEmpty(),
        favouriteServers = p[Keys.favourites].orEmpty(),
        selectedServerId = p[Keys.selectedServer],
        onboardingDone = p[Keys.onboardingDone] ?: false,
    )

    private inline fun <reified T : Enum<T>> enumOr(raw: String?, fallback: T): T =
        enumValues<T>().firstOrNull { it.name == raw } ?: fallback

    private fun update(block: (androidx.datastore.preferences.core.MutablePreferences) -> Unit) {
        scope.launch { context.settingsStore.edit { block(it) } }
    }

    fun setTheme(value: ThemeMode) = update { it[Keys.theme] = value.name }
    fun setAccent(value: Int) = update { it[Keys.accent] = value }
    fun setAutoConnect(value: Boolean) = update { it[Keys.autoConnect] = value }
    fun setDns(value: DnsMode) = update { it[Keys.dns] = value.name }

    /** Accepts IPv4/IPv6 addresses separated by commas or spaces; drops anything else. */
    fun setCustomDns(raw: String) = update { prefs ->
        prefs[Keys.customDns] = parseDnsList(raw).joinToString(",")
        prefs[Keys.dns] = DnsMode.CUSTOM.name
    }

    fun setRouting(value: RoutingMode) = update { it[Keys.routing] = value.name }
    fun setAppRouting(value: AppRoutingMode) = update { it[Keys.appRouting] = value.name }
    fun setSelectedApps(value: Set<String>) = update { it[Keys.selectedApps] = value }

    fun toggleFavourite(serverId: String) = update { prefs ->
        val current = prefs[Keys.favourites].orEmpty()
        prefs[Keys.favourites] = if (serverId in current) current - serverId else current + serverId
    }

    fun setSelectedServer(serverId: String?) = update { prefs ->
        if (serverId == null) prefs.remove(Keys.selectedServer) else prefs[Keys.selectedServer] = serverId
    }

    fun setOnboardingDone() = update { it[Keys.onboardingDone] = true }

    companion object {
        private val IPV4 = Regex("^((25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)\\.){3}(25[0-5]|2[0-4]\\d|1\\d\\d|[1-9]?\\d)$")
        private val IPV6 = Regex("^[0-9a-fA-F:]{2,39}$")

        /** Only literal addresses: a hostname here would need DNS to find DNS. */
        fun parseDnsList(raw: String): List<String> =
            raw.split(',', ' ', '\n', ';').map { it.trim() }
                .filter { it.isNotEmpty() && (IPV4.matches(it) || (':' in it && IPV6.matches(it))) }
                .distinct()
                .take(4)
    }
}
