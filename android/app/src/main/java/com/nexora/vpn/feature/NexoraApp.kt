package com.nexora.vpn.feature

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.BarChart
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavController
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.nexora.vpn.R
import com.nexora.vpn.core.ui.SubScreen
import com.nexora.vpn.core.vpn.XrayCore
import com.nexora.vpn.feature.auth.LoginScreen
import com.nexora.vpn.feature.auth.RegisterScreen
import com.nexora.vpn.feature.home.HomeScreen
import com.nexora.vpn.feature.info.AboutScreen
import com.nexora.vpn.feature.info.HelpScreen
import com.nexora.vpn.feature.info.LicensesScreen
import com.nexora.vpn.feature.onboarding.OnboardingScreen
import com.nexora.vpn.feature.profile.ProfileScreen
import com.nexora.vpn.feature.servers.ServerDetailScreen
import com.nexora.vpn.feature.servers.ServersScreen
import com.nexora.vpn.feature.services.ServicesScreen
import com.nexora.vpn.feature.settings.AppRoutingScreen
import com.nexora.vpn.feature.settings.AppearanceScreen
import com.nexora.vpn.feature.settings.DnsScreen
import com.nexora.vpn.feature.settings.RoutingScreen
import com.nexora.vpn.feature.settings.SettingsNav
import com.nexora.vpn.feature.settings.SettingsScreen
import com.nexora.vpn.feature.stats.HistoryScreen
import com.nexora.vpn.feature.stats.LogsScreen
import com.nexora.vpn.feature.stats.StatsScreen
import com.nexora.vpn.feature.store.StoreScreen
import com.nexora.vpn.feature.wallet.TopUpScreen
import com.nexora.vpn.feature.wallet.WalletScreen

object Routes {
    const val ONBOARDING = "onboarding"
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val HOME = "home"
    const val SERVERS = "servers"
    const val SERVER_DETAIL = "server/{id}"
    const val STATS = "stats"
    const val PROFILE = "profile"
    const val STORE = "store"
    const val SERVICES = "services"
    const val SETTINGS = "settings"
    const val APPEARANCE = "settings/appearance"
    const val DNS = "settings/dns"
    const val ROUTING = "settings/routing"
    const val APP_ROUTING = "settings/apps"
    const val HISTORY = "history"
    const val LOGS = "logs"
    const val HELP = "help"
    const val ABOUT = "about"
    const val LICENSES = "licenses"
    const val WALLET = "wallet"
    const val TOPUP = "topup?amount={amount}&order={order}"

    /** The top-up form, optionally prefilled to cover an order and pay it on approval. */
    fun topUp(amount: Long? = null, orderId: String? = null): String =
        "topup?amount=${amount ?: ""}&order=${orderId.orEmpty()}"

    fun serverDetail(id: String) = "server/$id"
}

private data class TabItem(val route: String, val labelRes: Int, val icon: ImageVector)

private val TABS = listOf(
    TabItem(Routes.HOME, R.string.nav_home, Icons.Filled.Home),
    TabItem(Routes.SERVERS, R.string.nav_servers, Icons.Filled.Dns),
    TabItem(Routes.STATS, R.string.nav_stats, Icons.Filled.BarChart),
    TabItem(Routes.PROFILE, R.string.nav_profile, Icons.Filled.Person),
)

private fun NavController.switchTab(route: String) = navigate(route) {
    // One entry per tab rather than a growing stack on every switch.
    popUpTo(graph.findStartDestination().id) { saveState = true }
    launchSingleTop = true
    restoreState = true
}

@Composable
fun NexoraApp(viewModel: AppViewModel = hiltViewModel()) {
    val navController = rememberNavController()
    val isSignedIn by viewModel.isSignedIn.collectAsStateWithLifecycle()
    val settings by viewModel.settings.collectAsStateWithLifecycle()
    val settingsLoaded by viewModel.settingsLoaded.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    // The splash screen stays up meanwhile (MainActivity); this is a few ms.
    if (!settingsLoaded) {
        Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background))
        return
    }

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination
    val showTabs = TABS.any { tab -> currentRoute?.hierarchy?.any { it.route == tab.route } == true }

    // Decided once, when the graph is built: first launch shows onboarding,
    // then sign-in or Home.
    val start = remember {
        when {
            !settings.onboardingDone && !isSignedIn -> Routes.ONBOARDING
            isSignedIn -> Routes.HOME
            else -> Routes.LOGIN
        }
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        // Each screen handles the status bar itself (its top bar, or
        // systemBarsPadding below); the navigation bar handles its own inset.
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        snackbarHost = { SnackbarHost(snackbarHostState) },
        bottomBar = {
            if (showTabs) {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    TABS.forEach { tab ->
                        val selected = currentRoute?.hierarchy?.any { it.route == tab.route } == true
                        NavigationBarItem(
                            selected = selected,
                            onClick = { navController.switchTab(tab.route) },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(stringResource(tab.labelRes)) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedIconColor = MaterialTheme.colorScheme.primary,
                                selectedTextColor = MaterialTheme.colorScheme.primary,
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                            ),
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = start,
            modifier = Modifier.padding(padding),
        ) {
            composable(Routes.ONBOARDING) {
                OnboardingScreen(modifier = Modifier.systemBarsPadding(), onDone = {
                    viewModel.finishOnboarding()
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(Routes.ONBOARDING) { inclusive = true }
                    }
                })
            }

            composable(Routes.LOGIN) {
                Box(Modifier.systemBarsPadding()) {
                LoginScreen(
                    onSignedIn = {
                        navController.navigate(Routes.HOME) {
                            // The login screen must not remain on the back
                            // stack, or Back returns to it while signed in.
                            popUpTo(Routes.LOGIN) { inclusive = true }
                        }
                    },
                    onRegister = { navController.navigate(Routes.REGISTER) },
                )
                }
            }

            composable(Routes.REGISTER) {
                Box(Modifier.systemBarsPadding()) {
                RegisterScreen(
                    onSignedIn = {
                        navController.navigate(Routes.HOME) {
                            popUpTo(Routes.LOGIN) { inclusive = true }
                        }
                    },
                    onBackToSignIn = { navController.popBackStack() },
                )
                }
            }

            // --- tabs ---------------------------------------------------------

            composable(Routes.HOME) {
                HomeScreen(
                    onBrowsePlans = { navController.navigate(Routes.STORE) },
                    onOpenServers = { navController.switchTab(Routes.SERVERS) },
                    onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                )
            }

            composable(Routes.SERVERS) {
                ServersScreen(onOpenServer = { id -> navController.navigate(Routes.serverDetail(id)) })
            }

            composable(Routes.STATS) {
                StatsScreen(onOpenHistory = { navController.navigate(Routes.HISTORY) })
            }

            composable(Routes.PROFILE) {
                ProfileScreen(
                    onSignedOut = {
                        navController.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }
                    },
                    onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                    onOpenStore = { navController.navigate(Routes.STORE) },
                    onOpenServices = { navController.navigate(Routes.SERVICES) },
                    onOpenWallet = { navController.navigate(Routes.WALLET) },
                )
            }

            // --- below the tabs ---------------------------------------------------

            composable(Routes.SERVER_DETAIL) { entry ->
                ServerDetailScreen(
                    serverId = entry.arguments?.getString("id").orEmpty(),
                    onBack = { navController.popBackStack() },
                )
            }

            composable(Routes.STORE) {
                SubScreen(
                    title = stringResource(R.string.nav_store),
                    onBack = { navController.popBackStack() },
                    actions = {
                        IconButton(onClick = { navController.navigate(Routes.WALLET) }) {
                            Icon(
                                Icons.Filled.AccountBalanceWallet,
                                contentDescription = stringResource(R.string.wallet_title),
                            )
                        }
                    },
                ) { p ->
                    Box(Modifier.padding(p)) {
                        StoreScreen(
                            onTopUp = { amount, orderId ->
                                navController.navigate(Routes.topUp(amount.takeIf { it > 0 }, orderId))
                            },
                            onPaid = { navController.switchTab(Routes.HOME) },
                        )
                    }
                }
            }

            composable(Routes.WALLET) {
                WalletScreen(
                    onBack = { navController.popBackStack() },
                    onTopUp = { navController.navigate(Routes.topUp()) },
                )
            }

            composable(
                Routes.TOPUP,
                arguments = listOf(
                    navArgument("amount") { type = NavType.StringType; defaultValue = "" },
                    navArgument("order") { type = NavType.StringType; defaultValue = "" },
                ),
            ) {
                TopUpScreen(
                    onBack = { navController.popBackStack() },
                    onDone = {
                        navController.navigate(Routes.WALLET) {
                            // The form is done with; Back from the wallet
                            // should not reopen a submitted form.
                            popUpTo(Routes.TOPUP) { inclusive = true }
                            launchSingleTop = true
                        }
                    },
                )
            }

            composable(Routes.SERVICES) {
                SubScreen(title = stringResource(R.string.nav_services), onBack = { navController.popBackStack() }) { p ->
                    Box(Modifier.padding(p)) {
                        ServicesScreen(onRenew = { navController.navigate(Routes.STORE) })
                    }
                }
            }

            composable(Routes.SETTINGS) {
                SettingsScreen(
                    SettingsNav(
                        onBack = { navController.popBackStack() },
                        openAppearance = { navController.navigate(Routes.APPEARANCE) },
                        openDns = { navController.navigate(Routes.DNS) },
                        openRouting = { navController.navigate(Routes.ROUTING) },
                        openAppRouting = { navController.navigate(Routes.APP_ROUTING) },
                        openHistory = { navController.navigate(Routes.HISTORY) },
                        openLogs = { navController.navigate(Routes.LOGS) },
                        openHelp = { navController.navigate(Routes.HELP) },
                        openAbout = { navController.navigate(Routes.ABOUT) },
                    ),
                )
            }
            composable(Routes.APPEARANCE) { AppearanceScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.DNS) { DnsScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.ROUTING) { RoutingScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.APP_ROUTING) { AppRoutingScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.HISTORY) { HistoryScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.LOGS) { LogsScreen(onBack = { navController.popBackStack() }) }
            composable(Routes.HELP) {
                HelpScreen(
                    onBack = { navController.popBackStack() },
                    onOpenLogs = { navController.navigate(Routes.LOGS) },
                )
            }
            composable(Routes.ABOUT) {
                val version = remember { XrayCore.version() }
                AboutScreen(
                    onBack = { navController.popBackStack() },
                    onOpenLicenses = { navController.navigate(Routes.LICENSES) },
                    coreVersion = version,
                )
            }
            composable(Routes.LICENSES) { LicensesScreen(onBack = { navController.popBackStack() }) }
        }
    }
}
