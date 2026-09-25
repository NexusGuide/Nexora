package com.nexora.vpn

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.nexora.vpn.core.settings.AppSettings
import com.nexora.vpn.core.ui.NexoraTheme
import com.nexora.vpn.feature.NexoraApp
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject

@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    @Inject lateinit var appSettings: AppSettings

    override fun onCreate(savedInstanceState: Bundle?) {
        // Held until the stored settings are read, so the first screen and the
        // theme are the right ones. The condition runs after super.onCreate,
        // by which time Hilt has injected appSettings.
        installSplashScreen().setKeepOnScreenCondition {
            !::appSettings.isInitialized || !appSettings.isLoaded.value
        }
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        setContent {
            val settings by appSettings.settings.collectAsStateWithLifecycle()
            NexoraTheme(mode = settings.theme, accentIndex = settings.accent) {
                NexoraApp()
            }
        }
    }
}
