package com.nexora.vpn.core.network

import com.nexora.vpn.core.security.DeviceIdentity
import com.nexora.vpn.core.security.TokenStore
import javax.inject.Inject
import javax.inject.Singleton
import okhttp3.Interceptor
import okhttp3.Response

/**
 * Attaches the access token and device identity to outgoing requests.
 *
 * Public paths are listed explicitly rather than inferred. Sending a stale
 * token to `/auth/login` would be harmless but pointless, and a written list
 * makes it obvious which routes are meant to work while signed out.
 */
@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenStore: TokenStore,
    private val deviceIdentity: DeviceIdentity,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val path = request.url.encodedPath

        val builder = request.newBuilder()
            .header("Accept", "application/json")
            .header("X-Device-Id", deviceIdentity.deviceId())

        if (PUBLIC_PATHS.none { path.endsWith(it) }) {
            tokenStore.accessToken()?.takeIf { it.isNotEmpty() }?.let { token ->
                builder.header("Authorization", "Bearer $token")
            }
        }

        return chain.proceed(builder.build())
    }

    private companion object {
        val PUBLIC_PATHS = listOf(
            "/auth/login",
            "/auth/register",
            "/auth/refresh",
            "/auth/forgot-password",
            "/auth/reset-password",
        )
    }
}
