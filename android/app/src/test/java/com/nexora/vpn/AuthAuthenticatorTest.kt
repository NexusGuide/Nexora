package com.nexora.vpn

import com.nexora.vpn.core.network.AuthAuthenticator
import com.nexora.vpn.core.security.TokenStore
import io.mockk.every
import io.mockk.mockk
import io.mockk.verify
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import javax.inject.Provider
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.Response
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/**
 * The authenticator is the one piece of the app that can sign a user out of
 * every device by accident, so it gets the most testing.
 *
 * The backend revokes a whole token family when it sees a refresh token used
 * twice. These tests exist to prove the app never causes that.
 */
class AuthAuthenticatorTest {

    private lateinit var server: MockWebServer
    private lateinit var tokenStore: TokenStore
    private var sessionLostCount = AtomicInteger(0)

    private val json = Json { ignoreUnknownKeys = true }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
        tokenStore = mockk(relaxed = true)
        sessionLostCount.set(0)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    private fun authenticator() = AuthAuthenticator(
        tokenStore = tokenStore,
        clientProvider = Provider { OkHttpClient() },
        baseUrl = server.url("/").toString(),
        json = json,
        onSessionLost = { sessionLostCount.incrementAndGet() },
    )

    private fun response(withToken: String?, priorCount: Int = 0): Response {
        val request = Request.Builder()
            .url(server.url("/api/v1/subscriptions"))
            .apply { withToken?.let { header("Authorization", "Bearer $it") } }
            .build()

        var built = Response.Builder()
            .request(request)
            .protocol(Protocol.HTTP_1_1)
            .code(401)
            .message("Unauthorized")
            .build()

        // Chain prior responses to simulate repeated 401s on one request.
        repeat(priorCount) {
            built = Response.Builder()
                .request(request)
                .protocol(Protocol.HTTP_1_1)
                .code(401)
                .message("Unauthorized")
                .priorResponse(built)
                .build()
        }
        return built
    }

    private fun enqueueRefreshSuccess(access: String, refresh: String) {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """
                    {"success":true,"data":{"access_token":"$access",
                    "refresh_token":"$refresh","token_type":"bearer",
                    "expires_in":1800},"request_id":"r-1"}
                    """.trimIndent(),
                ),
        )
    }

    // --- the happy path -----------------------------------------------------

    @Test
    fun `refreshes and retries with the new token`() {
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns "refresh-1"
        every { tokenStore.userId() } returns "user-1"
        enqueueRefreshSuccess("fresh-access", "refresh-2")

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNotNull(retry)
        assertEquals("Bearer fresh-access", retry!!.header("Authorization"))
        verify {
            tokenStore.save(
                accessToken = "fresh-access",
                refreshToken = "refresh-2",
                expiresInSeconds = 1800,
                userId = "user-1",
            )
        }
    }

    // --- the bug this class exists to prevent -------------------------------

    @Test
    fun `parallel 401s produce exactly one refresh`() {
        // The backend treats a reused refresh token as theft and revokes the
        // whole family. Eight threads hitting 401 at once must not send eight
        // refreshes with the same token.
        val currentAccess = java.util.concurrent.atomic.AtomicReference("stale-access")
        every { tokenStore.accessToken() } answers { currentAccess.get() }
        every { tokenStore.refreshToken() } returns "refresh-1"
        every { tokenStore.userId() } returns "user-1"
        every {
            tokenStore.save(any(), any(), any(), any())
        } answers { currentAccess.set(firstArg()) }

        enqueueRefreshSuccess("fresh-access", "refresh-2")

        val threads = 8
        val start = CountDownLatch(1)
        val done = CountDownLatch(threads)
        val auth = authenticator()
        val retries = mutableListOf<Response?>()

        repeat(threads) {
            Thread {
                start.await()
                val result = auth.authenticate(null, response("stale-access"))
                synchronized(retries) { retries.add(result?.let { null }) }
                done.countDown()
            }.start()
        }

        start.countDown()
        done.await(10, TimeUnit.SECONDS)

        // One refresh request reached the server; the other seven threads saw
        // the already-renewed token and retried with it.
        assertEquals(1, server.requestCount)
        assertEquals(0, sessionLostCount.get())
    }

    @Test
    fun `a thread that waited retries with the token the winner fetched`() {
        // Simulates arriving after another thread already refreshed.
        every { tokenStore.accessToken() } returns "already-renewed"
        every { tokenStore.refreshToken() } returns "refresh-1"

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNotNull(retry)
        assertEquals("Bearer already-renewed", retry!!.header("Authorization"))
        // No refresh was needed at all.
        assertEquals(0, server.requestCount)
    }

    // --- rejected vs unreachable --------------------------------------------

    @Test
    fun `a rejected refresh token ends the session`() {
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns "dead-refresh"
        server.enqueue(
            MockResponse().setResponseCode(401).setBody(
                """{"success":false,"error":{"code":"TOKEN_REUSE_DETECTED",
                "message":"revoked"},"request_id":"r-2"}""",
            ),
        )

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNull(retry)
        verify { tokenStore.clear() }
        assertEquals(1, sessionLostCount.get())
    }

    @Test
    fun `a network failure keeps the session`() {
        // The critical distinction: not reaching the server says nothing about
        // whether the session is valid. Clearing it here would sign users out
        // whenever they walk into a lift.
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns "refresh-1"
        server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START))

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNull(retry)
        verify(exactly = 0) { tokenStore.clear() }
        assertEquals(0, sessionLostCount.get())
    }

    @Test
    fun `a malformed refresh body keeps the session`() {
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns "refresh-1"
        server.enqueue(MockResponse().setResponseCode(200).setBody("<html>proxy error</html>"))

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNull(retry)
        verify(exactly = 0) { tokenStore.clear() }
    }

    // --- giving up ----------------------------------------------------------

    @Test
    fun `gives up after repeated 401s rather than looping`() {
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns "refresh-1"

        val retry = authenticator().authenticate(null, response("stale-access", priorCount = 2))

        assertNull(retry)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `no refresh token means the session is already over`() {
        every { tokenStore.accessToken() } returns "stale-access"
        every { tokenStore.refreshToken() } returns null

        val retry = authenticator().authenticate(null, response("stale-access"))

        assertNull(retry)
        assertEquals(1, sessionLostCount.get())
        assertEquals(0, server.requestCount)
    }

    // --- sign-in is not a session -------------------------------------------

    @Test
    fun `a 401 on a request without a token does not end the session`() {
        // Sign-in with a wrong password answers 401. That request carried no
        // token, so there is no session to renew or to lose.
        every { tokenStore.accessToken() } returns null
        every { tokenStore.refreshToken() } returns null

        val retry = authenticator().authenticate(null, response(withToken = null))

        assertNull(retry)
        assertEquals(0, sessionLostCount.get())
        assertEquals(0, server.requestCount)
    }
}
