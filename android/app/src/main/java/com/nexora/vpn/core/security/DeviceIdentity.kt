package com.nexora.vpn.core.security

import android.annotation.SuppressLint
import android.content.Context
import android.os.Build
import android.provider.Settings
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.nexora.vpn.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import java.security.MessageDigest
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The identifier the backend counts against a plan's device limit.
 *
 * It must survive an uninstall. It used to be a random UUID made on first
 * run, and a customer who reinstalled the app came back as a *second* device
 * — on a one-device plan, locked out by their own phone.
 *
 * So it is derived from `Settings.Secure.ANDROID_ID`, which on Android 8+ is
 * scoped to this app's signing key, the device and the user profile: stable
 * across reinstalls, different in every other app, reset by a factory reset.
 * It is never sent as is — only a SHA-256 of it with an app-specific prefix,
 * so what the backend stores cannot be matched against any other app's copy,
 * including on Android 7, where the raw value is shared between apps.
 *
 * IMEI, serial numbers and the advertising ID are not used: they need
 * permissions this app has no business asking for, or exist for tracking.
 *
 * When ANDROID_ID is unavailable, or is the one value an old Android bug gave
 * every device, it falls back to a random UUID kept in encrypted storage —
 * the old behaviour, with its reinstall problem, but only on such devices.
 */
@Singleton
class DeviceIdentity @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private companion object {
        const val FILE = "nexora_device_identity"
        const val KEY_DEVICE_ID = "device_id"
        const val HASH_PREFIX = "nexora-device-v1:"

        /** Returned by every device on some Android 2.2 builds; not an identity. */
        const val BROKEN_ANDROID_ID = "9774d56d682e549c"
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
            val id = fromAndroidId() ?: storedRandomId()
            cached = id
            return id
        }
    }

    @SuppressLint("HardwareIds") // Hashed, app-scoped, and the point: see the class doc.
    private fun fromAndroidId(): String? {
        val raw = runCatching {
            Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        }.getOrNull()
        if (raw.isNullOrBlank() || raw == BROKEN_ANDROID_ID) return null
        return sha256(HASH_PREFIX + raw)
    }

    private fun storedRandomId(): String {
        val stored = runCatching { prefs?.getString(KEY_DEVICE_ID, null) }.getOrNull()
        return stored ?: UUID.randomUUID().toString().also { generated ->
            runCatching { prefs?.edit()?.putString(KEY_DEVICE_ID, generated)?.apply() }
        }
    }

    private fun sha256(value: String): String =
        MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

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
