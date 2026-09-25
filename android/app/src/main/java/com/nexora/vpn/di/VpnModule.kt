package com.nexora.vpn.di

import android.content.Context
import com.nexora.vpn.core.stats.ConnectionLog
import com.nexora.vpn.core.stats.UsageStore
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import java.io.File
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object VpnModule {

    /** History and per-day traffic, on the phone only (see UsageStore). */
    @Provides
    @Singleton
    fun usageStore(@ApplicationContext context: Context): UsageStore =
        UsageStore(File(context.filesDir, "usage.json"))

    @Provides
    @Singleton
    fun connectionLog(): ConnectionLog = ConnectionLog()
}
