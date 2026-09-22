package com.nexora.vpn.core.common

/**
 * Input validation, mirroring the backend's rules.
 *
 * The server validates regardless — client validation is never a security
 * control, only a courtesy that saves a round trip. These rules therefore
 * match `backend/app/schemas/auth.py` exactly; a client rule *stricter* than
 * the server's would reject input the server would accept, which is the worse
 * failure because the user cannot tell why.
 */
object Validation {

    const val MIN_PASSWORD_LENGTH = 8
    const val MAX_PASSWORD_LENGTH = 128
    const val MIN_USERNAME_LENGTH = 3
    const val MAX_USERNAME_LENGTH = 32

    private val USERNAME = Regex("^[a-zA-Z0-9_]{3,32}$")
    private val EMAIL = Regex("^[^@\\s]+@[^@\\s.]+\\.[^@\\s]+$")
    private val PHONE = Regex("^\\+?[0-9]{10,15}$")

    sealed interface Check {
        data object Valid : Check
        data class Invalid(val reason: Reason) : Check
    }

    enum class Reason {
        EMPTY,
        USERNAME_TOO_SHORT,
        USERNAME_TOO_LONG,
        USERNAME_CHARSET,
        PASSWORD_TOO_SHORT,
        PASSWORD_TOO_LONG,
        PASSWORD_NEEDS_LOWERCASE,
        PASSWORD_NEEDS_UPPERCASE,
        PASSWORD_NEEDS_DIGIT,
        PASSWORDS_DO_NOT_MATCH,
        EMAIL_INVALID,
        PHONE_INVALID,
    }

    fun username(value: String): Check {
        val trimmed = value.trim()
        return when {
            trimmed.isEmpty() -> Check.Invalid(Reason.EMPTY)
            trimmed.length < MIN_USERNAME_LENGTH ->
                Check.Invalid(Reason.USERNAME_TOO_SHORT)
            trimmed.length > MAX_USERNAME_LENGTH ->
                Check.Invalid(Reason.USERNAME_TOO_LONG)
            !USERNAME.matches(trimmed) -> Check.Invalid(Reason.USERNAME_CHARSET)
            else -> Check.Valid
        }
    }

    fun password(value: String): Check = when {
        value.isEmpty() -> Check.Invalid(Reason.EMPTY)
        value.length < MIN_PASSWORD_LENGTH ->
            Check.Invalid(Reason.PASSWORD_TOO_SHORT)
        value.length > MAX_PASSWORD_LENGTH ->
            Check.Invalid(Reason.PASSWORD_TOO_LONG)
        !value.any { it.isLowerCase() } ->
            Check.Invalid(Reason.PASSWORD_NEEDS_LOWERCASE)
        !value.any { it.isUpperCase() } ->
            Check.Invalid(Reason.PASSWORD_NEEDS_UPPERCASE)
        !value.any { it.isDigit() } -> Check.Invalid(Reason.PASSWORD_NEEDS_DIGIT)
        else -> Check.Valid
    }

    fun passwordConfirmation(password: String, confirmation: String): Check = when {
        confirmation.isEmpty() -> Check.Invalid(Reason.EMPTY)
        password != confirmation -> Check.Invalid(Reason.PASSWORDS_DO_NOT_MATCH)
        else -> Check.Valid
    }

    /** Optional at registration, so a blank value is acceptable. */
    fun email(value: String): Check {
        val trimmed = value.trim()
        return when {
            trimmed.isEmpty() -> Check.Valid
            !EMAIL.matches(trimmed) -> Check.Invalid(Reason.EMAIL_INVALID)
            else -> Check.Valid
        }
    }

    fun phone(value: String): Check {
        val trimmed = value.trim().replace(" ", "")
        return when {
            trimmed.isEmpty() -> Check.Valid
            !PHONE.matches(trimmed) -> Check.Invalid(Reason.PHONE_INVALID)
            else -> Check.Valid
        }
    }

    /** Login accepts a username, an email or a phone number. */
    fun loginIdentifier(value: String): Check {
        val trimmed = value.trim()
        return if (trimmed.isEmpty()) Check.Invalid(Reason.EMPTY) else Check.Valid
    }

    /**
     * A coarse strength score, 0..4, for the meter on the register screen.
     * Deliberately simple: a score that disagrees with the rules above would
     * confuse more than it helps.
     */
    fun passwordStrength(value: String): Int {
        if (value.length < MIN_PASSWORD_LENGTH) return 0
        var score = 1
        if (value.length >= 12) score++
        if (value.any { it.isUpperCase() } && value.any { it.isLowerCase() }) score++
        if (value.any { it.isDigit() } && value.any { !it.isLetterOrDigit() }) score++
        return score.coerceIn(0, 4)
    }
}
