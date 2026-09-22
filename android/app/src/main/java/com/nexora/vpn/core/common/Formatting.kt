package com.nexora.vpn.core.common

import kotlin.math.abs
import kotlin.math.roundToLong

/**
 * Value formatting shared by every screen.
 *
 * Kept here, and kept pure, so it can be unit-tested without an emulator —
 * these are the functions a user sees constantly, and an off-by-1024 in a
 * traffic figure erodes trust faster than a crash.
 */
object Formatting {

    private const val KB = 1024.0
    private const val MB = KB * 1024
    private const val GB = MB * 1024
    private const val TB = GB * 1024

    /**
     * Human traffic size. Uses binary units (1 GB = 1024 MB) to match how
     * panels and plans express quotas — showing 107.4 GB for a "100 GB" plan
     * because of decimal units would look like a bug to the customer.
     */
    fun bytes(value: Long, decimals: Int = 1): String {
        if (value < 0) return bytes(0)
        val absolute = value.toDouble()
        return when {
            absolute < KB -> "$value B"
            absolute < MB -> "${round(absolute / KB, 0)} KB"
            absolute < GB -> "${round(absolute / MB, decimals)} MB"
            absolute < TB -> "${round(absolute / GB, decimals)} GB"
            else -> "${round(absolute / TB, decimals)} TB"
        }
    }

    /** "32.4 / 100 GB" — one unit, shown once, as in the spec's Home screen. */
    fun trafficRatio(usedBytes: Long, limitBytes: Long, decimals: Int = 1): String {
        if (limitBytes <= 0L) return bytes(usedBytes, decimals)

        val limitDouble = limitBytes.toDouble()
        val unit: String
        val divisor: Double
        when {
            limitDouble < MB -> { unit = "KB"; divisor = KB }
            limitDouble < GB -> { unit = "MB"; divisor = MB }
            limitDouble < TB -> { unit = "GB"; divisor = GB }
            else -> { unit = "TB"; divisor = TB }
        }
        val used = round(usedBytes.coerceAtLeast(0L) / divisor, decimals)
        val limit = round(limitDouble / divisor, decimals)
        return "$used / $limit $unit"
    }

    /**
     * Price with thousands separators. Toman amounts run to six or seven
     * digits, and an unseparated "249000" is genuinely hard to read.
     *
     * The separator is passed in so a Persian locale can use its own.
     */
    fun price(amount: Long, separator: String = ","): String {
        val negative = amount < 0
        val digits = abs(amount).toString()
        val grouped = StringBuilder()

        for ((index, char) in digits.withIndex()) {
            if (index > 0 && (digits.length - index) % 3 == 0) {
                grouped.append(separator)
            }
            grouped.append(char)
        }
        return if (negative) "-$grouped" else grouped.toString()
    }

    /** Latency, or a placeholder when it has not been measured yet. */
    fun latency(ms: Int?): String = when {
        ms == null || ms < 0 -> "—"
        ms >= 1000 -> "${round(ms / 1000.0, 1)} s"
        else -> "$ms ms"
    }

    /** Connected-session duration as H:MM:SS, or M:SS under an hour. */
    fun duration(seconds: Long): String {
        if (seconds <= 0) return "0:00"
        val hours = seconds / 3600
        val minutes = (seconds % 3600) / 60
        val secs = seconds % 60
        return if (hours > 0) {
            "$hours:${pad(minutes)}:${pad(secs)}"
        } else {
            "$minutes:${pad(secs)}"
        }
    }

    /** Transfer rate, for the connected screen's up/down readout. */
    fun speed(bytesPerSecond: Long): String = "${bytes(bytesPerSecond, 1)}/s"

    private fun pad(value: Long): String = value.toString().padStart(2, '0')

    private fun round(value: Double, decimals: Int): String {
        if (decimals <= 0) return value.roundToLong().toString()
        var factor = 1.0
        repeat(decimals) { factor *= 10 }
        val rounded = (value * factor).roundToLong() / factor

        // Drop a trailing ".0": "100 GB" reads better than "100.0 GB".
        return if (rounded == rounded.toLong().toDouble()) {
            rounded.toLong().toString()
        } else {
            rounded.toString()
        }
    }
}
