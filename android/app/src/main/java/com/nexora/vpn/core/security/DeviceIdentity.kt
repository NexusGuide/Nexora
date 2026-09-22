package com.nexora.vpn.core.security

import android.content.Context
import android.os.Build
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.nexora.vpn.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * A stable per-installation identifier, for the backend's device limit.
 *
 * Deliberately **not** derived from any hardware identifier. `ANDROID_ID`,
 * IMEI and the advertising ID are either restricted on modern Android, shared
 * across apps, or survive uninstall — all of which make them tracking
 * identifiers rather than what this needs, which is "is this the same
 * installation as last time".
 *
 * A random UUID generated on first run and stored encrypted is enough: it is
 * unique, meaningless outside this app, and disappears when the app is
 * uninstalled.
 */
@Singleton
class DeviceIdentity @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private companion object {
        const val FILE = "nexora_device_identity"
        const val KEY_DEVICE_ID = "device_id"
    }

    private val prefs by lazy {
        runCatching {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                context,
                FILE,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        }.getOrNull()
    }

    @Volatile
    private var cached: String? = null

    fun deviceId(): String {
        cached?.let { return it }

        synchronized(this) {
            cached?.let { return it }

            val stored = runCatching { prefs?.getString(KEY_DEVICE_ID, null) }.getOrNull()
            val id = stored ?: UUID.randomUUID().toString().also { generated ->
                runCatching {
                    prefs?.edit()?.putString(KEY_DEVICE_ID, generated)?.apply()
                }
            }
            cached = id
            return id
        }
    }

    /**
     * What the user sees in their device list, so it must be recognisable:
     * "Pixel 8" is useful, "sdk_gphone64" is not, but either beats a UUID.
     */
    fun deviceName(): String {
        val manufacturer = Build.MANUFACTURER.orEmpty().replaceFirstChar(Char::uppercase)
        val model = Build.MODEL.orEmpty()
        return when {
            model.isEmpty() && manufacturer.isEmpty() -> "Android device"
            model.startsWith(manufacturer, ignoreCase = true) -> model
            manufacturer.isEmpty() -> model
            else -> "$manufacturer $model"
        }.take(128)
    }

    fun appVersion(): String = BuildConfig.VERSION_NAME
}
