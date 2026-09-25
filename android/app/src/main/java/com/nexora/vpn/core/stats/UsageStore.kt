package com.nexora.vpn.core.stats

import com.nexora.vpn.core.vpn.MiniJson
import java.io.File
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId

/** One connection, from connect to disconnect. */
data class SessionRecord(
    val serverName: String,
    val startedAtMs: Long,
    val durationMs: Long,
    val bytesDown: Long,
    val bytesUp: Long,
) {
    val totalBytes: Long get() = bytesDown + bytesUp
}

/** Traffic through the VPN on one calendar day, in the phone's time zone. */
data class DailyUsage(val day: LocalDate, val bytesDown: Long, val bytesUp: Long) {
    val totalBytes: Long get() = bytesDown + bytesUp
}

/**
 * Connection history and per-day traffic, kept on the phone only.
 *
 * Nothing here is sent anywhere: this is what the customer's own phone
 * measured, for the Statistics and History screens. It records server names
 * and byte counts — never destinations — and it is bounded, so it cannot grow
 * into a log of someone's life.
 *
 * Plain JSON in one file, written through a temporary file and a rename so a
 * crash mid-write leaves the previous version intact. Pure Kotlin, so its
 * rules are tested on the JVM.
 */
class UsageStore(
    private val file: File,
    private val zone: ZoneId = ZoneId.systemDefault(),
    private val maxSessions: Int = 200,
    private val maxDays: Int = 90,
) {
    private val lock = Any()
    private var sessions: MutableList<SessionRecord> = mutableListOf()
    private var daily: MutableMap<LocalDate, DailyUsage> = sortedMapOf()
    private var loaded = false

    fun sessions(): List<SessionRecord> = synchronized(lock) {
        load()
        sessions.sortedByDescending { it.startedAtMs }
    }

    fun daily(): List<DailyUsage> = synchronized(lock) {
        load()
        daily.values.sortedBy { it.day }
    }

    /** Adds traffic measured at [atMs] to that day's total. */
    fun addTraffic(atMs: Long, bytesDown: Long, bytesUp: Long) {
        if (bytesDown <= 0 && bytesUp <= 0) return
        synchronized(lock) {
            load()
            val day = Instant.ofEpochMilli(atMs).atZone(zone).toLocalDate()
            val current = daily[day] ?: DailyUsage(day, 0, 0)
            daily[day] = current.copy(
                bytesDown = current.bytesDown + bytesDown.coerceAtLeast(0),
                bytesUp = current.bytesUp + bytesUp.coerceAtLeast(0),
            )
            trimDays()
            save()
        }
    }

    fun addSession(record: SessionRecord) {
        synchronized(lock) {
            load()
            sessions.add(record)
            if (sessions.size > maxSessions) {
                sessions = sessions.sortedByDescending { it.startedAtMs }
                    .take(maxSessions).toMutableList()
            }
            save()
        }
    }

    fun clear() {
        synchronized(lock) {
            sessions.clear()
            daily.clear()
            loaded = true
            save()
        }
    }

    private fun trimDays() {
        if (daily.size <= maxDays) return
        val keep = daily.keys.sorted().takeLast(maxDays).toSet()
        daily.keys.retainAll(keep)
    }

    // --- persistence -------------------------------------------------------------

    private fun load() {
        if (loaded) return
        loaded = true
        if (!file.exists()) return
        val root = runCatching { MiniJson.parse(file.readText()) as? Map<*, *> }.getOrNull()
            ?: return // A damaged file is dropped, not a crash on every screen.
        (root["sessions"] as? List<*>)?.forEach { entry ->
            val m = entry as? Map<*, *> ?: return@forEach
            sessions.add(
                SessionRecord(
                    serverName = m["server"] as? String ?: return@forEach,
                    startedAtMs = (m["start"] as? Number)?.toLong() ?: return@forEach,
                    durationMs = (m["duration"] as? Number)?.toLong() ?: 0,
                    bytesDown = (m["down"] as? Number)?.toLong() ?: 0,
                    bytesUp = (m["up"] as? Number)?.toLong() ?: 0,
                ),
            )
        }
        (root["daily"] as? List<*>)?.forEach { entry ->
            val m = entry as? Map<*, *> ?: return@forEach
            val day = runCatching { LocalDate.parse(m["day"] as? String) }.getOrNull()
                ?: return@forEach
            daily[day] = DailyUsage(
                day,
                (m["down"] as? Number)?.toLong() ?: 0,
                (m["up"] as? Number)?.toLong() ?: 0,
            )
        }
    }

    private fun save() {
        val json = MiniJson.write(
            mapOf(
                "version" to 1,
                "sessions" to sessions.map {
                    mapOf(
                        "server" to it.serverName,
                        "start" to it.startedAtMs,
                        "duration" to it.durationMs,
                        "down" to it.bytesDown,
                        "up" to it.bytesUp,
                    )
                },
                "daily" to daily.values.sortedBy { it.day }.map {
                    mapOf("day" to it.day.toString(), "down" to it.bytesDown, "up" to it.bytesUp)
                },
            ),
        )
        file.parentFile?.mkdirs()
        val tmp = File(file.path + ".tmp")
        tmp.writeText(json)
        if (!tmp.renameTo(file)) {
            file.writeText(json)
            tmp.delete()
        }
    }
}
