package com.nexora.vpn

import com.nexora.vpn.core.stats.ConnectionLog
import com.nexora.vpn.core.stats.SessionRecord
import com.nexora.vpn.core.stats.UsageStore
import com.nexora.vpn.core.vpn.ServerInfo
import com.nexora.vpn.core.vpn.TrafficCounters
import java.io.File
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class UsageAndServerInfoTest {

    private val tehran = ZoneId.of("Asia/Tehran")

    // --- server info ---------------------------------------------------------------

    @Test
    fun `flag in a panel name gives country and region`() {
        val info = ServerInfo.from("Speed 🇳🇱HollandXM")
        assertEquals("NL", info.countryCode)
        assertEquals("🇳🇱", info.flag)
        assertEquals("Speed HollandXM", info.label)
        assertEquals(ServerInfo.Region.EUROPE, info.region)
    }

    @Test
    fun `regions for asia and america`() {
        assertEquals(ServerInfo.Region.ASIA, ServerInfo.from("🇸🇬Singapore").region)
        assertEquals(ServerInfo.Region.AMERICA, ServerInfo.from("🇺🇸 US").region)
    }

    @Test
    fun `a name without a flag is other and keeps its text`() {
        val info = ServerInfo.from("  Plain   server ")
        assertNull(info.countryCode)
        assertEquals("Plain server", info.label)
        assertEquals(ServerInfo.Region.OTHER, info.region)
    }

    // --- traffic counters ------------------------------------------------------------

    @Test
    fun `counters split proxy from direct and ignore the rest`() {
        val c = TrafficCounters.parse(
            "proxy,uplink,100;proxy,downlink,2000;direct,downlink,50;dns-out,uplink,9;junk;",
        )
        assertEquals(TrafficCounters(proxyUp = 100, proxyDown = 2000, directDown = 50), c)
        assertEquals(TrafficCounters(), TrafficCounters.parse(""))
        assertEquals(TrafficCounters(), TrafficCounters.parse(null))
    }

    // --- usage store -------------------------------------------------------------------

    @Test
    fun `traffic is totalled per local day and survives a reload`() {
        val file = File.createTempFile("usage", ".json").apply { delete() }
        val store = UsageStore(file, tehran)
        val lateNight = ZonedDateTime.of(2026, 9, 24, 23, 50, 0, 0, tehran).toInstant().toEpochMilli()
        val nextMorning = lateNight + 20 * 60_000L

        store.addTraffic(lateNight, bytesDown = 1000, bytesUp = 10)
        store.addTraffic(lateNight + 1000, bytesDown = 500, bytesUp = 5)
        store.addTraffic(nextMorning, bytesDown = 7, bytesUp = 0)

        val reloaded = UsageStore(file, tehran).daily()
        assertEquals(2, reloaded.size)
        assertEquals(LocalDate.of(2026, 9, 24), reloaded[0].day)
        assertEquals(1500L, reloaded[0].bytesDown)
        assertEquals(15L, reloaded[0].bytesUp)
        assertEquals(7L, reloaded[1].bytesDown)
    }

    @Test
    fun `history is newest first and bounded`() {
        val file = File.createTempFile("usage", ".json").apply { delete() }
        val store = UsageStore(file, tehran, maxSessions = 3)
        for (i in 1..5) store.addSession(SessionRecord("s$i", startedAtMs = i * 1000L, 60_000, 1, 1))

        val sessions = UsageStore(file, tehran, maxSessions = 3).sessions()
        assertEquals(listOf("s5", "s4", "s3"), sessions.map { it.serverName })
    }

    @Test
    fun `a damaged file is dropped instead of crashing`() {
        val file = File.createTempFile("usage", ".json").apply { writeText("{not json") }
        val store = UsageStore(file, tehran)
        assertTrue(store.sessions().isEmpty())
        store.addSession(SessionRecord("ok", 1, 1, 1, 1))
        assertEquals(1, UsageStore(file, tehran).sessions().size)
    }

    // --- connection log ------------------------------------------------------------------

    @Test
    fun `log is bounded, filterable and clearable`() {
        val log = ConnectionLog(capacity = 2)
        log.info("a", 1)
        log.warning("b", 2)
        log.error("c", 3)
        assertEquals(listOf("b", "c"), log.entries().map { it.message })
        assertEquals(listOf("c"), log.entries(ConnectionLog.Level.ERROR).map { it.message })
        assertTrue(log.export { it.toString() }.contains("[WARNING] b"))
        log.clear()
        assertFalse(log.entries().isNotEmpty())
    }
}
