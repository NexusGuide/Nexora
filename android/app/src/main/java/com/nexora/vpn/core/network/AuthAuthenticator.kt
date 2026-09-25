package com.nexora.vpn.core.network

import android.util.Log
import com.nexora.vpn.core.security.TokenStore
import com.nexora.vpn.data.remote.dto.ApiEnvelope
import com.nexora.vpn.data.remote.dto.RefreshRequestDto
import com.nexora.vpn.data.remote.dto.TokenPairDto
import java.io.IOException
import javax.inject.Provider
import kotlinx.serialization.json.Json
import okhttp3.Authenticator
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.Route

/**
 * Renews the session when a request comes back 401.
 *
 * ## Why the refresh is serialised
 *
 * The backend rotates refresh tokens and treats a reused one as theft: it
 * revokes the **whole token family**, signing the user out everywhere.
 *
 * That makes the naive implementation actively harmful. If the home screen
 * fires three requests and all three get 401, three refreshes go out with the
 * same refresh token. The first rotates it; the other two look exactly like a
 * stolen token being replayed, and the backend does the right thing — it kills
 * every session. The user is signed out for no reason, and it would happen
 * most on a slow network, when several screens load at once.
 *
 * Two rules prevent it:
 *
 * 1. **Serialised.** The refresh happens inside a lock, so a burst of 401s
 *    produces one refresh.
 * 2. **Re-checked inside the lock.** A request that waited finds the token
 *    already renewed and retries with it instead of refreshing again.
 *
 * ## Why a failed refresh does not always sign out
 *
 * A refresh can fail for two very different reasons, and treating them alike
 * is its own bug:
 *
 * - The server said **401/403**: the refresh token is spent, revoked, or its
 *   family was killed. The session is genuinely over — clear it.
 * - The request **never got an answer** (no connection, timeout, TLS failure).
 *   That says nothing about the session. Clearing it here would sign users out
 *   every time they walk into a lift. The request fails; the session survives
 *   and the next attempt can succeed.
 */
class AuthAuthenticator(
    private val tokenStore: TokenStore,
    // Provider, not the client itself: this authenticator is installed *in*
    // that client, so injecting it directly would be a dependency cycle.
    private val clientProvider: Provider<OkHttpClient>,
    private val baseUrl: String,
    private val json: Json,
    private val onSessionLost: () -> Unit,
) : Authenticator {

    private val lock = Any()

    override fun authenticate(route: Route?, response: Response): Request? {
        if (responseCount(response) >= MAX_RETRIES) {
            Log.w(TAG, "Giving up after repeated 401s")
            return null
        }

        val failedToken = response.request.header(HEADER_AUTH)?.removePrefix(BEARER)

        // A request that carried no token was never signed in: sign-in and
        // sign-up themselves. Their 401 is the answer — wrong password — not
        // an expired session. Treating it as one ended the session and told
        // the user "your session ended" for a mistyped password.
        if (failedToken.isNullOrEmpty()) return null

        synchronized(lock) {
            val current = tokenStore.accessToken()

            // Another request refreshed while this one waited for the lock.
            if (!current.isNullOrEmpty() && current != failedToken) {
                return response.request.retryWith(current)
            }

            val refreshToken = tokenStore.refreshToken()
            if (refreshToken.isNullOrEmpty()) {
                onSessionLost()
                return null
            }

            return when (val result = performRefresh(refreshToken)) {
                is RefreshResult.Renewed -> {
                    tokenStore.save(
                        accessToken = result.tokens.accessToken,
                        refreshToken = result.tokens.refreshToken,
                        expiresInSeconds = result.tokens.expiresIn,
                        userId = tokenStore.userId(),
                    )
                    response.request.retryWith(result.tokens.accessToken)
                }

                RefreshResult.Rejected -> {
                    // The server rejected the refresh token itself.
                    Log.w(TAG, "Refresh token rejected; ending session")
                    tokenStore.clear()
                    onSessionLost()
                    null
                }

                RefreshResult.Unreachable -> {
                    // Could not reach the server. The session may be perfectly
                    // fine, so it is left alone and this one request fails.
                    Log.w(TAG, "Refresh unreachable; keeping session")
                    null
                }
            }
        }
    }

    private sealed interface RefreshResult {
        data class Renewed(val tokens: TokenPairDto) : RefreshResult
        /** The server answered, and said no. */
        data object Rejected : RefreshResult
        /** No usable answer — network, timeout, TLS, or an unparseable body. */
        data object Unreachable : RefreshResult
    }

    private fun performRefresh(refreshToken: String): RefreshResult {
        val body = json
            .encodeToString(RefreshRequestDto.serializer(), RefreshRequestDto(refreshToken))
            .toRequestBody("application/json".toMediaType())

        val request = Request.Builder()
            .url("${baseUrl.trimEnd('/')}/api/v1/auth/refresh")
            .post(body)
            .build()

        return try {
            // Authenticator.NONE so a 401 on the refresh call cannot recurse
            // back into this authenticator.
            clientProvider.get().newBuilder()
                .authenticator(Authenticator.NONE)
                .build()
                .newCall(request)
                .execute()
                .use { refreshResponse ->
                    when {
                        refreshResponse.code in REJECTING_CODES -> RefreshResult.Rejected
                        !refreshResponse.isSuccessful -> RefreshResult.Unreachable
                        else -> {
                            val payload = refreshResponse.body?.string()
                            val tokens = payload
                                ?.takeIf { it.isNotBlank() }
                                ?.let { raw ->
                                    json.decodeFromString(
                                        ApiEnvelope.serializer(TokenPairDto.serializer()),
                                        raw,
                                    ).data
                                }
                            if (tokens != null) {
                                RefreshResult.Renewed(tokens)
                            } else {
                                RefreshResult.Unreachable
                            }
                        }
                    }
                }
        } catch (e: IOException) {
            Log.w(TAG, "Refresh request failed: ${e.javaClass.simpleName}")
            RefreshResult.Unreachable
        } catch (e: Exception) {
            // An unparseable body is not proof the session is dead either.
            Log.w(TAG, "Refresh response unusable: ${e.javaClass.simpleName}")
            RefreshResult.Unreachable
        }
    }

    private fun Request.retryWith(token: String): Request =
        newBuilder().header(HEADER_AUTH, "$BEARER$token").build()

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) {
            count++
            prior = prior.priorResponse
        }
        return count
    }

    private companion object {
        const val TAG = "AuthAuthenticator"
        const val HEADER_AUTH = "Authorization"
        const val BEARER = "Bearer "

        /** Give up after this many attempts, or a server that always answers
         *  401 would loop forever. */
        const val MAX_RETRIES = 2

        /** Codes that mean the refresh token itself was refused. */
        val REJECTING_CODES = setOf(400, 401, 403)
    }
}
