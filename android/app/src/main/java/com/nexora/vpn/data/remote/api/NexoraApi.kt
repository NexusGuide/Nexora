package com.nexora.vpn.data.remote.api

import com.nexora.vpn.data.remote.dto.ApiEnvelope
import com.nexora.vpn.data.remote.dto.AuthResultDto
import com.nexora.vpn.data.remote.dto.ConfigDto
import com.nexora.vpn.data.remote.dto.DeviceDto
import com.nexora.vpn.data.remote.dto.ForgotPasswordRequestDto
import com.nexora.vpn.data.remote.dto.LoginRequestDto
import com.nexora.vpn.data.remote.dto.LogoutRequestDto
import com.nexora.vpn.data.remote.dto.MessageDto
import com.nexora.vpn.data.remote.dto.OrderCreateDto
import com.nexora.vpn.data.remote.dto.OrderDto
import com.nexora.vpn.data.remote.dto.PlanDto
import com.nexora.vpn.data.remote.dto.QueuedDto
import com.nexora.vpn.data.remote.dto.RefreshRequestDto
import com.nexora.vpn.data.remote.dto.RegisterRequestDto
import com.nexora.vpn.data.remote.dto.ResetPasswordRequestDto
import com.nexora.vpn.data.remote.dto.SubscriptionDto
import com.nexora.vpn.data.remote.dto.TokenPairDto
import com.nexora.vpn.data.remote.dto.UserDto
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.DELETE
import retrofit2.http.GET
import retrofit2.http.PATCH
import retrofit2.http.POST
import retrofit2.http.Path
import retrofit2.http.Query

/**
 * The backend, as the app sees it.
 *
 * Every method returns `Response<ApiEnvelope<T>>` rather than `T`: the error
 * envelope carries a code the UI branches on, and a raw `T` would throw it
 * away along with the request id that support needs.
 */
interface NexoraApi {

    // --- auth ---------------------------------------------------------------

    @POST("api/v1/auth/register")
    suspend fun register(
        @Body body: RegisterRequestDto,
    ): Response<ApiEnvelope<UserDto>>

    @POST("api/v1/auth/login")
    suspend fun login(
        @Body body: LoginRequestDto,
    ): Response<ApiEnvelope<AuthResultDto>>

    /**
     * Not called directly by repositories — [com.nexora.vpn.core.network.AuthAuthenticator]
     * owns refreshing, so that exactly one place decides when a session is
     * renewed and two parallel 401s cannot both rotate the token.
     */
    @POST("api/v1/auth/refresh")
    suspend fun refresh(
        @Body body: RefreshRequestDto,
    ): Response<ApiEnvelope<TokenPairDto>>

    @POST("api/v1/auth/logout")
    suspend fun logout(
        @Body body: LogoutRequestDto,
    ): Response<ApiEnvelope<Map<String, Boolean>>>

    @POST("api/v1/auth/forgot-password")
    suspend fun forgotPassword(
        @Body body: ForgotPasswordRequestDto,
    ): Response<ApiEnvelope<MessageDto>>

    @POST("api/v1/auth/reset-password")
    suspend fun resetPassword(
        @Body body: ResetPasswordRequestDto,
    ): Response<ApiEnvelope<MessageDto>>

    // --- profile ------------------------------------------------------------

    @GET("api/v1/me")
    suspend fun me(): Response<ApiEnvelope<UserDto>>

    @PATCH("api/v1/me")
    suspend fun updateMe(
        @Body body: Map<String, String?>,
    ): Response<ApiEnvelope<UserDto>>

    @GET("api/v1/me/devices")
    suspend fun devices(): Response<ApiEnvelope<List<DeviceDto>>>

    @DELETE("api/v1/me/devices/{id}")
    suspend fun revokeDevice(
        @Path("id") id: String,
    ): Response<ApiEnvelope<Map<String, String>>>

    // --- store --------------------------------------------------------------

    @GET("api/v1/plans")
    suspend fun plans(): Response<ApiEnvelope<List<PlanDto>>>

    @GET("api/v1/plans/{id}")
    suspend fun plan(@Path("id") id: String): Response<ApiEnvelope<PlanDto>>

    // --- orders -------------------------------------------------------------

    @POST("api/v1/orders")
    suspend fun createOrder(
        @Body body: OrderCreateDto,
    ): Response<ApiEnvelope<OrderDto>>

    @GET("api/v1/orders")
    suspend fun orders(
        @Query("limit") limit: Int = 20,
        @Query("offset") offset: Int = 0,
    ): Response<ApiEnvelope<List<OrderDto>>>

    @GET("api/v1/orders/{id}")
    suspend fun order(@Path("id") id: String): Response<ApiEnvelope<OrderDto>>

    @POST("api/v1/orders/{id}/cancel")
    suspend fun cancelOrder(
        @Path("id") id: String,
    ): Response<ApiEnvelope<OrderDto>>

    // --- subscriptions ------------------------------------------------------

    @GET("api/v1/subscriptions")
    suspend fun subscriptions(
        @Query("subscription_status") status: String? = null,
    ): Response<ApiEnvelope<List<SubscriptionDto>>>

    @GET("api/v1/subscriptions/{id}")
    suspend fun subscription(
        @Path("id") id: String,
    ): Response<ApiEnvelope<SubscriptionDto>>

    @POST("api/v1/subscriptions/{id}/renew")
    suspend fun renewSubscription(
        @Path("id") id: String,
        @Body body: OrderCreateDto,
    ): Response<ApiEnvelope<OrderDto>>

    @POST("api/v1/subscriptions/{id}/refresh")
    suspend fun refreshSubscriptionConfigs(
        @Path("id") id: String,
    ): Response<ApiEnvelope<QueuedDto>>

    // --- configs ------------------------------------------------------------

    @GET("api/v1/configs")
    suspend fun configs(
        @Query("subscription_id") subscriptionId: String? = null,
    ): Response<ApiEnvelope<List<ConfigDto>>>

    @POST("api/v1/configs/{id}/activate")
    suspend fun activateConfig(
        @Path("id") id: String,
    ): Response<ApiEnvelope<ConfigDto>>

    @DELETE("api/v1/configs/{id}")
    suspend fun deleteConfig(
        @Path("id") id: String,
    ): Response<ApiEnvelope<Map<String, String>>>
}
