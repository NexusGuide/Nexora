package com.nexora.vpn.data.remote.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Wire format. These mirror the backend's JSON exactly and are mapped into
 * domain models; no screen ever sees a DTO.
 *
 * Every field the backend might omit is nullable with a default, so adding a
 * field server-side cannot crash an installed app. An app in the wild cannot
 * be fixed as fast as a server can.
 */

// --- envelope (backend spec rules 44-45) ------------------------------------

@Serializable
data class ApiEnvelope<T>(
    val success: Boolean = false,
    val data: T? = null,
    val error: ApiError? = null,
    @SerialName("request_id") val requestId: String? = null,
)

@Serializable
data class ApiError(
    val code: String = "UNKNOWN",
    val message: String = "",
    val details: Map<String, kotlinx.serialization.json.JsonElement>? = null,
)

// --- auth -------------------------------------------------------------------

@Serializable
data class RegisterRequestDto(
    val username: String,
    val password: String,
    val email: String? = null,
    val phone: String? = null,
)

@Serializable
data class LoginRequestDto(
    val identifier: String,
    val password: String,
    @SerialName("device_id") val deviceId: String? = null,
    @SerialName("device_name") val deviceName: String? = null,
    @SerialName("app_version") val appVersion: String? = null,
)

@Serializable
data class RefreshRequestDto(
    @SerialName("refresh_token") val refreshToken: String,
)

@Serializable
data class LogoutRequestDto(
    @SerialName("refresh_token") val refreshToken: String? = null,
    @SerialName("all_devices") val allDevices: Boolean = false,
)

@Serializable
data class ForgotPasswordRequestDto(val identifier: String)

@Serializable
data class ResetPasswordRequestDto(
    val token: String,
    @SerialName("new_password") val newPassword: String,
)

@Serializable
data class TokenPairDto(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("token_type") val tokenType: String = "bearer",
    @SerialName("expires_in") val expiresIn: Int = 1800,
)

@Serializable
data class UserDto(
    val id: String,
    val username: String,
    val email: String? = null,
    val phone: String? = null,
    val status: String = "ACTIVE",
    @SerialName("is_email_verified") val isEmailVerified: Boolean = false,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("last_login_at") val lastLoginAt: String? = null,
)

@Serializable
data class AuthResultDto(
    val user: UserDto,
    val tokens: TokenPairDto,
)

@Serializable
data class DeviceDto(
    val id: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
    @SerialName("app_version") val appVersion: String? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
)

// --- store ------------------------------------------------------------------

@Serializable
data class PlanDto(
    val id: String,
    val name: String,
    val description: String? = null,
    @SerialName("duration_days") val durationDays: Int,
    @SerialName("traffic_limit_bytes") val trafficLimitBytes: Long = 0,
    @SerialName("device_limit") val deviceLimit: Int = 1,
    // Sent as a decimal string, not a float: the backend stores money as
    // NUMERIC and a double would reintroduce the rounding it avoids.
    val price: String = "0",
    val currency: String = "IRT",
    val status: String = "ACTIVE",
)

@Serializable
data class OrderCreateDto(
    @SerialName("plan_id") val planId: String,
    @SerialName("idempotency_key") val idempotencyKey: String? = null,
    @SerialName("subscription_id") val subscriptionId: String? = null,
)

@Serializable
data class OrderDto(
    val id: String,
    @SerialName("plan_id") val planId: String,
    @SerialName("subscription_id") val subscriptionId: String? = null,
    val amount: String = "0",
    val currency: String = "IRT",
    val status: String = "PENDING",
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("completed_at") val completedAt: String? = null,
    @SerialName("failure_reason") val failureReason: String? = null,
)

@Serializable
data class SubscriptionDto(
    val id: String,
    @SerialName("plan_id") val planId: String,
    val status: String = "PENDING",
    @SerialName("start_at") val startAt: String? = null,
    @SerialName("expire_at") val expireAt: String? = null,
    @SerialName("traffic_limit_bytes") val trafficLimitBytes: Long = 0,
    @SerialName("traffic_used_bytes") val trafficUsedBytes: Long = 0,
    @SerialName("device_limit") val deviceLimit: Int = 1,
    @SerialName("days_remaining") val daysRemaining: Int? = null,
    @SerialName("traffic_limit_gb") val trafficLimitGb: Double? = null,
    @SerialName("traffic_used_gb") val trafficUsedGb: Double? = null,
)

@Serializable
data class ConfigDto(
    val id: String,
    @SerialName("subscription_id") val subscriptionId: String,
    val name: String,
    val protocol: String? = null,
    val host: String? = null,
    val port: Int? = null,
    @SerialName("config_data") val configData: String,
    @SerialName("latency_ms") val latencyMs: Int? = null,
    @SerialName("is_active") val isActive: Boolean = false,
)

@Serializable
data class MessageDto(val message: String = "")

@Serializable
data class QueuedDto(
    val queued: Boolean = false,
    @SerialName("job_id") val jobId: String? = null,
)
