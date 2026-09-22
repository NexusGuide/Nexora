package com.nexora.vpn.feature

import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
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
import androidx.navigation.NavDestination.Companion.hierarchy
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.nexora.vpn.R
import com.nexora.vpn.feature.auth.LoginScreen
import com.nexora.vpn.feature.home.HomeScreen
import com.nexora.vpn.feature.services.ServicesScreen
import com.nexora.vpn.feature.store.StoreScreen

object Routes {
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val HOME = "home"
    const val STORE = "store"
    const val SERVICES = "services"
    const val PROFILE = "profile"
}

private data class TabItem(
    val route: String,
    val labelRes: Int,
    val icon: ImageVector,
)

private val TABS = listOf(
    TabItem(Routes.HOME, R.string.nav_home, Icons.Default.Home),
    TabItem(Routes.STORE, R.string.nav_store, Icons.Default.ShoppingCart),
    TabItem(Routes.SERVICES, R.string.nav_services, Icons.Default.Star),
    TabItem(Routes.PROFILE, R.string.nav_profile, Icons.Default.Person),
)

@Composable
fun NexoraApp(viewModel: AppViewModel = hiltViewModel()) {
    val navController = rememberNavController()
    val isSignedIn by viewModel.isSignedIn.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination
    val showTabs = TABS.any { tab ->
        currentRoute?.hierarchy?.any { it.route == tab.route } == true
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbarHostState) },
        bottomBar = {
            if (showTabs) {
                NavigationBar {
                    TABS.forEach { tab ->
                        val selected =
                            currentRoute?.hierarchy?.any { it.route == tab.route } == true
                        NavigationBarItem(
                            selected = selected,
                            onClick = {
                                navController.navigate(tab.route) {
                                    // Keeps one entry per tab rather than
                                    // growing the stack on every switch.
                                    popUpTo(navController.graph.findStartDestination().id) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                            icon = { Icon(tab.icon, contentDescription = null) },
                            label = { Text(stringResource(tab.labelRes)) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = if (isSignedIn) Routes.HOME else Routes.LOGIN,
            modifier = Modifier.padding(padding),
        ) {
            composable(Routes.LOGIN) {
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

            composable(Routes.REGISTER) {
                // Registration reuses the login screen's flow for now; a
                // dedicated screen lands with the onboarding pass.
                LoginScreen(
                    onSignedIn = {
                        navController.navigate(Routes.HOME) {
                            popUpTo(Routes.LOGIN) { inclusive = true }
                        }
                    },
                    onRegister = { navController.popBackStack() },
                )
            }

            composable(Routes.HOME) {
                HomeScreen(onBrowsePlans = { navController.navigate(Routes.STORE) })
            }

            composable(Routes.STORE) { StoreScreen() }

            composable(Routes.SERVICES) {
                ServicesScreen(onRenew = { navController.navigate(Routes.STORE) })
            }

            composable(Routes.PROFILE) {
                com.nexora.vpn.feature.profile.ProfileScreen(
                    onSignedOut = {
                        navController.navigate(Routes.LOGIN) {
                            popUpTo(0) { inclusive = true }
                        }
                    },
                )
            }
        }
    }
}
