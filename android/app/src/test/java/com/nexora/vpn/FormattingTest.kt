package com.nexora.vpn

import com.nexora.vpn.core.common.Formatting
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

private const val GB = 1024L * 1024 * 1024

/**
 * These run on the JVM without an emulator, which is why the formatting and
 * validation logic was kept free of Android types.
 */
class FormattingTest {

    @Test
    fun `bytes uses binary units so a 100 GB plan reads as 100 GB`() {
        // Decimal units would render this as 107.4 GB, which looks like a bug
        // to a customer who bought "100 GB".
        assertEquals("100 GB", Formatting.bytes(100 * GB))
        assertEquals("1 GB", Formatting.bytes(GB))
        assertEquals("1.5 GB", Formatting.bytes(GB * 3 / 2))
    }

    @Test
    fun `bytes clamps a negative value rather than rendering it`() {
        assertEquals("0 B", Formatting.bytes(-1))
    }

    @Test
    fun `trafficRatio shows one unit once`() {
        assertEquals(
            "32.4 / 100 GB",
            Formatting.trafficRatio((32.4 * GB).toLong(), 100 * GB),
        )
    }

    @Test
    fun `trafficRatio falls back to a plain size when the plan is unlimited`() {
        assertEquals("5 GB", Formatting.trafficRatio(5 * GB, 0))
    }

    @Test
    fun `price groups thousands`() {
        assertEquals("249,000", Formatting.price(249_000))
        assertEquals("1,234,567", Formatting.price(1_234_567))
        assertEquals("999", Formatting.price(999))
        assertEquals("0", Formatting.price(0))
    }

    @Test
    fun `price accepts a locale separator`() {
        assertEquals("249٬000", Formatting.price(249_000, separator = "٬"))
    }

    @Test
    fun `duration switches format at an hour`() {
        assertEquals("1:30", Formatting.duration(90))
        assertEquals("1:01:01", Formatting.duration(3661))
        assertEquals("0:00", Formatting.duration(0))
    }

    @Test
    fun `latency renders a placeholder when unmeasured`() {
        assertEquals("—", Formatting.latency(null))
        assertEquals("—", Formatting.latency(-1))
        assertEquals("42 ms", Formatting.latency(42))
        assertEquals("1.2 s", Formatting.latency(1200))
    }

    @Test
    fun `speed appends a rate suffix`() {
        assertEquals("1 MB/s", Formatting.speed(1024L * 1024))
    }

    @Test
    fun `trailing zero is dropped`() {
        // "100 GB" reads better than "100.0 GB".
        assertNull(Formatting.bytes(100 * GB).firstOrNull { it == '.' }?.let { null })
        assertEquals("100 GB", Formatting.bytes(100 * GB))
    }
}
