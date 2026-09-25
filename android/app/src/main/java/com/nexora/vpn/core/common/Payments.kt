package com.nexora.vpn.core.common

import java.math.BigDecimal
import java.math.RoundingMode

/**
 * Small, pure payment helpers, kept free of Android types so they are tested
 * on the JVM.
 */
object Payments {

    /**
     * How much crypto to send for [toman] at [rate] Toman per unit. Rounded
     * up to 6 places, exactly as the server quotes it, so the customer never
     * sends a fraction short. Null when the rate is not a positive number.
     */
    fun cryptoQuote(toman: Long, rate: String): String? {
        val r = rate.trim().toBigDecimalOrNull() ?: return null
        if (r.signum() <= 0 || toman <= 0) return null
        return BigDecimal.valueOf(toman)
            .divide(r, 6, RoundingMode.UP)
            .stripTrailingZeros()
            .toPlainString()
    }

    /** "6037991234567890" → "6037 9912 3456 7890", for reading aloud and checking. */
    fun groupCard(number: String): String =
        number.filter(Char::isDigit).chunked(4).joinToString(" ")

    /**
     * What to top up so an order of [price] can be paid from [balance]: the
     * shortfall, raised to the minimum top-up and capped at the maximum.
     * Zero when the balance already covers it.
     */
    fun topUpFor(price: Long, balance: Long, min: Long, max: Long): Long {
        val missing = price - balance
        if (missing <= 0) return 0
        val atLeast = if (min > 0) maxOf(missing, min) else missing
        return if (max > 0) minOf(atLeast, max) else atLeast
    }

    /**
     * The amount typed by the customer, as whole Toman: Persian and Arabic
     * digits accepted, thousands separators ignored. Null for anything else.
     */
    fun parseToman(input: String): Long? {
        val digits = buildString {
            for (c in input) {
                when (c) {
                    in '0'..'9' -> append(c)
                    in '۰'..'۹' -> append('0' + (c - '۰'))
                    in '٠'..'٩' -> append('0' + (c - '٠'))
                    ',', '،', '٬', ' ', ' ' -> Unit
                    else -> return null
                }
            }
        }
        if (digits.isEmpty() || digits.length > 13) return null
        return digits.toLongOrNull()
    }

    /** A bank tracking number or a TXID as typed: digits normalised, spaces trimmed. */
    fun normaliseReference(input: String): String = buildString {
        for (c in input.trim()) {
            when (c) {
                in '۰'..'۹' -> append('0' + (c - '۰'))
                in '٠'..'٩' -> append('0' + (c - '٠'))
                ' ', ' ', '\n' -> Unit
                else -> append(c)
            }
        }
    }

    private val TRACKING = Regex("^[0-9A-Za-z-]{4,40}$")
    private val TX_HASH = Regex("^(0x)?[0-9a-fA-F]{64}$|^[A-Za-z0-9+/_=-]{43,44}$")

    /** Mirrors the server's check, so a typo is caught before it is sent. */
    fun isValidReference(crypto: Boolean, reference: String): Boolean =
        if (crypto) TX_HASH.matches(reference) else TRACKING.matches(reference)
}
