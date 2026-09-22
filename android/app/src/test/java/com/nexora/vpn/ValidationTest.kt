package com.nexora.vpn

import com.nexora.vpn.core.common.Validation
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The client rules must match `backend/app/schemas/auth.py`.
 *
 * A client rule *stricter* than the server's is the dangerous direction: it
 * rejects input the server would accept, and the user has no way to find out
 * why. These tests pin the boundaries.
 */
class ValidationTest {

    @Test
    fun `password matching the backend rules is accepted`() {
        assertEquals(Validation.Check.Valid, Validation.password("CorrectHorse1"))
    }

    @Test
    fun `password boundary is exactly eight characters`() {
        assertEquals(Validation.Check.Valid, Validation.password("Abcdefg1"))
        assertTrue(Validation.password("Abcdef1") is Validation.Check.Invalid)
    }

    @Test
    fun `each character class is required`() {
        assertEquals(
            Validation.Reason.PASSWORD_NEEDS_DIGIT,
            (Validation.password("CorrectHorse") as Validation.Check.Invalid).reason,
        )
        assertEquals(
            Validation.Reason.PASSWORD_NEEDS_UPPERCASE,
            (Validation.password("correcthorse1") as Validation.Check.Invalid).reason,
        )
        assertEquals(
            Validation.Reason.PASSWORD_NEEDS_LOWERCASE,
            (Validation.password("CORRECTHORSE1") as Validation.Check.Invalid).reason,
        )
    }

    @Test
    fun `username charset matches the backend regex`() {
        assertEquals(Validation.Check.Valid, Validation.username("rend_1"))
        assertEquals(Validation.Check.Valid, Validation.username("ABC"))
        assertTrue(Validation.username("bad name") is Validation.Check.Invalid)
        assertTrue(Validation.username("bad-name") is Validation.Check.Invalid)
        assertTrue(Validation.username("ab") is Validation.Check.Invalid)
    }

    @Test
    fun `email is optional so blank is valid`() {
        // Registration allows no email; rejecting blank here would block a
        // signup the server would accept.
        assertEquals(Validation.Check.Valid, Validation.email(""))
        assertEquals(Validation.Check.Valid, Validation.email("   "))
    }

    @Test
    fun `email format is checked when present`() {
        assertEquals(Validation.Check.Valid, Validation.email("a@b.co"))
        assertTrue(Validation.email("nope") is Validation.Check.Invalid)
        assertTrue(Validation.email("a@b") is Validation.Check.Invalid)
    }

    @Test
    fun `phone accepts an optional leading plus`() {
        assertEquals(Validation.Check.Valid, Validation.phone("+989121234567"))
        assertEquals(Validation.Check.Valid, Validation.phone("09121234567"))
        assertEquals(Validation.Check.Valid, Validation.phone(""))
        assertTrue(Validation.phone("123") is Validation.Check.Invalid)
    }

    @Test
    fun `confirmation must match`() {
        assertEquals(
            Validation.Check.Valid,
            Validation.passwordConfirmation("CorrectHorse1", "CorrectHorse1"),
        )
        assertEquals(
            Validation.Reason.PASSWORDS_DO_NOT_MATCH,
            (
                Validation.passwordConfirmation("CorrectHorse1", "Different1")
                    as Validation.Check.Invalid
                ).reason,
        )
    }

    @Test
    fun `strength never contradicts acceptance`() {
        // Anything the rules accept must score above zero, or the meter would
        // tell the user their valid password is unusable.
        val accepted = listOf("Abcdefg1", "CorrectHorse1", "L0ngerPassphrase!")
        accepted.forEach { password ->
            assertEquals(Validation.Check.Valid, Validation.password(password))
            assertTrue(
                "strength should be > 0 for an accepted password: $password",
                Validation.passwordStrength(password) > 0,
            )
        }
        assertEquals(0, Validation.passwordStrength("short"))
    }
}
