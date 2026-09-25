package com.nexora.vpn.core.ui

import androidx.annotation.StringRes
import com.nexora.vpn.R
import com.nexora.vpn.core.common.AppError

/**
 * The five states every data-bearing screen can be in (spec rule 46).
 *
 * Modelled as one type so a screen cannot forget one — the `when` is
 * exhaustive, and "loading" and "empty" stop being afterthoughts that ship as
 * a blank rectangle.
 */
sealed interface UiState<out T> {
    data object Loading : UiState<Nothing>
    data object Empty : UiState<Nothing>
    data class Content<T>(val data: T, val isRefreshing: Boolean = false) : UiState<T>
    data class Failed(val error: AppError, val cached: Any? = null) : UiState<Nothing>
}

/**
 * Maps an error to the message the user reads.
 *
 * Kept out of the ViewModels so the same failure never gets two different
 * explanations on two different screens. Backend codes are matched where they
 * carry a meaning the user can act on; everything else falls back to the
 * category.
 */
@StringRes
fun AppError.messageRes(): Int = when (this) {
    is AppError.Network -> R.string.error_network
    is AppError.Timeout -> R.string.error_timeout
    is AppError.Server -> R.string.error_server
    is AppError.Unauthorized -> R.string.error_session_expired
    is AppError.Unknown -> R.string.error_unknown
    is AppError.Rejected -> when (code) {
        "AUTHENTICATION_FAILED" -> R.string.auth_invalid_credentials
        "ACCOUNT_LOCKED" -> R.string.auth_account_locked
        "ACCOUNT_INACTIVE" -> R.string.auth_account_inactive
        "ACCOUNT_EXISTS" -> R.string.auth_account_exists
        "VALIDATION_ERROR" -> R.string.auth_details_rejected
        "DEVICE_LIMIT_REACHED" -> R.string.device_limit_title
        else -> R.string.error_unknown
    }
}

/**
 * The devices the backend listed on a device-limit refusal, so the screen can
 * offer to remove one instead of leaving the user stuck.
 */
fun AppError.deviceLimitDevices(): List<DeviceSummary> {
    if (this !is AppError.Rejected || code != "DEVICE_LIMIT_REACHED") return emptyList()

    @Suppress("UNCHECKED_CAST")
    val raw = details["devices"] as? List<Map<String, Any?>> ?: return emptyList()

    return raw.mapNotNull { entry ->
        val id = entry["id"] as? String ?: return@mapNotNull null
        DeviceSummary(
            id = id,
            name = entry["name"] as? String ?: "",
            platform = entry["platform"] as? String ?: "",
        )
    }
}

fun AppError.deviceLimit(): Int? =
    (this as? AppError.Rejected)?.details?.get("limit").let { value ->
        when (value) {
            is Long -> value.toInt()
            is Int -> value
            else -> null
        }
    }

data class DeviceSummary(val id: String, val name: String, val platform: String)
