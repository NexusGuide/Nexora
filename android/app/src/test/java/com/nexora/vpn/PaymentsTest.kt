package com.nexora.vpn

import com.nexora.vpn.core.common.Payments
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PaymentsTest {

    @Test
    fun `crypto quote rounds up like the server so the customer never sends short`() {
        // 100000 / 60000 = 1.6666…: the server quotes 1.666667.
        assertEquals("1.666667", Payments.cryptoQuote(100_000, "60000"))
        assertEquals("5", Payments.cryptoQuote(300_000, "60000.00"))
        assertEquals("0.000001", Payments.cryptoQuote(1, "10000000"))
    }

    @Test
    fun `crypto quote refuses a missing or zero rate instead of dividing by it`() {
        assertNull(Payments.cryptoQuote(100_000, "0"))
        assertNull(Payments.cryptoQuote(100_000, ""))
        assertNull(Payments.cryptoQuote(100_000, "abc"))
        assertNull(Payments.cryptoQuote(0, "60000"))
    }

    @Test
    fun `card numbers are grouped in fours`() {
        assertEquals("6037 9900 0000 0006", Payments.groupCard("6037990000000006"))
        assertEquals("6037 9900 0000 0006", Payments.groupCard("6037-9900 0000-0006"))
    }

    @Test
    fun `top-up covers the shortfall within the operator's limits`() {
        // Balance covers the price: nothing to top up.
        assertEquals(0L, Payments.topUpFor(price = 50_000, balance = 60_000, min = 10_000, max = 0))
        // Short by 5000, but the minimum top-up is 10000.
        assertEquals(10_000L, Payments.topUpFor(price = 50_000, balance = 45_000, min = 10_000, max = 0))
        // Short by more than the minimum.
        assertEquals(40_000L, Payments.topUpFor(price = 50_000, balance = 10_000, min = 10_000, max = 0))
        // Capped at the maximum.
        assertEquals(30_000L, Payments.topUpFor(price = 50_000, balance = 0, min = 0, max = 30_000))
    }

    @Test
    fun `amounts typed with Persian digits and separators are understood`() {
        assertEquals(150_000L, Payments.parseToman("۱۵۰٬۰۰۰"))
        assertEquals(150_000L, Payments.parseToman("150,000"))
        assertEquals(150_000L, Payments.parseToman("١٥٠٠٠٠"))
        assertNull(Payments.parseToman("150.5"))
        assertNull(Payments.parseToman(""))
        assertNull(Payments.parseToman("-5"))
    }

    @Test
    fun `references are validated like the server does`() {
        assertTrue(Payments.isValidReference(crypto = false, reference = "738291"))
        assertFalse(Payments.isValidReference(crypto = false, reference = "12"))
        assertTrue(Payments.isValidReference(crypto = true, reference = "ab".repeat(32)))
        assertTrue(Payments.isValidReference(crypto = true, reference = "0x" + "ab".repeat(32)))
        // An address pasted where the hash belongs.
        assertFalse(Payments.isValidReference(crypto = true, reference = "T" + "A".repeat(33)))
        assertEquals("738291", Payments.normaliseReference(" ۷۳۸ ۲۹۱ "))
    }
}
