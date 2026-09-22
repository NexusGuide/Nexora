package com.nexora.vpn.core.common

/**
 * Every way a request can fail, in the terms the UI cares about.
 *
 * The distinction that matters is **retryable or not**. A timeout deserves a
 * "try again" button; a rejected order does not, and offering one would just
 * produce the same rejection.
 */
sealed interface AppError {

    /** True when trying the same thing again could plausibly work. */
    val isRetryable: Boolean

    /** No usable connection. */
    data class Network(val detail: String? = null) : AppError {
        override val isRetryable = true
    }

    data class Timeout(val detail: String? = null) : AppError {
        override val isRetryable = true
    }

    /**
     * The session is not valid. The authenticator has already tried to refresh
     * by the time this surfaces, so the UI's job is to send the user to
     * sign-in, not to retry.
     */
    data class Unauthorized(
        val code: String,
        val message: String? = null,
    ) : AppError {
        override val isRetryable = false
    }

    /**
     * The server understood and refused: a validation error, a conflict, a
     * device limit. [code] is the backend's stable error code, which is what
     * the UI should branch on — never the message, which is prose and may be
     * reworded.
     */
    data class Rejected(
        val code: String,
        val message: String? = null,
        val details: Map<String, Any?> = emptyMap(),
    ) : AppError {
        override val isRetryable = false
    }

    /** The server broke. [requestId] is what support needs to find it in the logs. */
    data class Server(
        val message: String? = null,
        val requestId: String? = null,
    ) : AppError {
        override val isRetryable = true
    }

    data class Unknown(val detail: String? = null) : AppError {
        override val isRetryable = true
    }

    companion object {
        // Backend error codes the UI branches on. Kept here so a typo is a
        // compile error rather than a silently unhandled case.
        const val CODE_DEVICE_LIMIT = "DEVICE_LIMIT_REACHED"
        const val CODE_AUTH_FAILED = "AUTHENTICATION_FAILED"
        const val CODE_ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
        const val CODE_ACCOUNT_INACTIVE = "ACCOUNT_INACTIVE"
        const val CODE_ACCOUNT_EXISTS = "ACCOUNT_EXISTS"
        const val CODE_VALIDATION = "VALIDATION_ERROR"
        const val CODE_NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
        const val CODE_NO_SERVER = "NO_SERVER_AVAILABLE"
        const val CODE_SUB_NOT_PROVISIONED = "SUBSCRIPTION_NOT_PROVISIONED"
    }
}
