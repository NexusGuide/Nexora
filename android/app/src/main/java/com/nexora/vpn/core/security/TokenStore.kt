package com.nexora.vpn.core.security

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Where the session tokens live.
 *
 * Backed by [EncryptedSharedPreferences], whose master key sits in the Android
 * Keystore — hardware-backed where the device has a TEE or StrongBox. Spec
 * rule 36 requires this, and the reason is concrete: a plain preferences file
 * is readable text to anyone with root, or to an `adb backup` on older
 * Android.
 *
 * ## The fallback, and why it is visible
 *
 * Keystore initialisation genuinely fails on some devices — a corrupted
 * keystore, an OEM bug, a user who changed their lock screen. The options are
 * to crash on launch, to sign the user out forever, or to fall back to
 * unencrypted storage. This falls back, but records it in
 * [isUsingInsecureFallback] so the app can tell the user rather than quietly
 * downgrading their security.
 */
@Singleton
class TokenStore @Inject constructor(
    @ApplicationContext private val context: Context,
) {

    private var prefs: SharedPreferences? = null

    /**
     * True when tokens are being kept in ordinary preferences because the
     * encrypted store could not be opened. Surfaced in the UI — a silent
     * downgrade would be worse than the failure itself.
     */
    @Volatile
    var isUsingInsecureFallback: Boolean = false
        private set

    private val _isSignedIn = MutableStateFlow(false)

    /** Drives navigation: when this goes false, the app shows sign-in. */
    val isSignedIn: StateFlow<Boolean> = _isSignedIn.asStateFlow()

    /**
     * Opens the store. Called once from [com.nexora.vpn.NexoraApplication]
     * because Keystore work is slow enough to be worth doing deliberately
     * rather than on whichever thread happens to touch a token first.
     */
    fun initialise() {
        if (prefs != null) return
        synchronized(this) {
            if (prefs != null) return

            prefs = openEncrypted() ?: openEncryptedAfterReset() ?: openPlain()
            _isSignedIn.value = !refreshToken().isNullOrEmpty()
        }
    }

    private fun requirePrefs(): SharedPreferences {
        prefs?.let { return it }
        initialise()
        return prefs ?: openPlain()
    }

    private fun openEncrypted(): SharedPreferences? = runCatching {
        EncryptedSharedPreferences.create(
            context,
            FILE,
            MasterKey.Builder(context, MASTER_KEY_ALIAS)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build(),
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }.getOrNull()

    /**
     * Second attempt, after throwing the existing file away.
     *
     * A keystore key can be invalidated while its ciphertext remains — that
     * data is unrecoverable, so deleting it and asking the user to sign in
     * again is the only way forward.
     */
    private fun openEncryptedAfterReset(): SharedPreferences? = runCatching {
        Log.w(TAG, "Encrypted store unreadable; recreating")
        context.deleteSharedPreferences(FILE)
        openEncrypted()
    }.getOrNull()

    private fun openPlain(): SharedPreferences {
        Log.e(TAG, "Falling back to unencrypted token storage")
        isUsingInsecureFallback = true
        return context.getSharedPreferences(FALLBACK_FILE, Context.MODE_PRIVATE)
    }

    fun accessToken(): String? = requirePrefs().getString(KEY_ACCESS, null)

    fun refreshToken(): String? = requirePrefs().getString(KEY_REFRESH, null)

    fun userId(): String? = requirePrefs().getString(KEY_USER_ID, null)

    /**
     * When the access token expires, as epoch milliseconds.
     *
     * Advisory only. The authenticator refreshes on a 401 rather than on this
     * clock, because a device's clock can be wrong and the server's answer
     * cannot.
     */
    fun accessTokenExpiresAt(): Long = requirePrefs().getLong(KEY_EXPIRES_AT, 0L)

    fun save(
        accessToken: String,
        refreshToken: String,
        expiresInSeconds: Int,
        userId: String?,
    ) {
        // One commit for the whole set, so a crash between writes cannot pair
        // a new access token with the previous refresh token.
        requirePrefs().edit()
            .putString(KEY_ACCESS, accessToken)
            .putString(KEY_REFRESH, refreshToken)
            .putLong(KEY_EXPIRES_AT, System.currentTimeMillis() + expiresInSeconds * 1000L)
            .apply { if (userId != null) putString(KEY_USER_ID, userId) }
            .apply()

        _isSignedIn.value = true
    }

    fun clear() {
        requirePrefs().edit()
            .remove(KEY_ACCESS)
            .remove(KEY_REFRESH)
            .remove(KEY_EXPIRES_AT)
            .remove(KEY_USER_ID)
            .apply()

        _isSignedIn.value = false
    }

    private companion object {
        const val TAG = "TokenStore"
        const val FILE = "nexora_secure_session"
        const val FALLBACK_FILE = "nexora_session_fallback"
        const val MASTER_KEY_ALIAS = "nexora_master_key"
        const val KEY_ACCESS = "access_token"
        const val KEY_REFRESH = "refresh_token"
        const val KEY_EXPIRES_AT = "access_expires_at"
        const val KEY_USER_ID = "user_id"
    }
}
