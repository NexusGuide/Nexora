package com.nexora.vpn

import android.app.Application
import com.nexora.vpn.core.security.TokenStore
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject

@HiltAndroidApp
class NexoraApplication : Application() {

    @Inject lateinit var tokenStore: TokenStore

    override fun onCreate() {
        super.onCreate()
        // Decides the first screen without a flash of the wrong one.
        tokenStore.initialise()
    }
}
