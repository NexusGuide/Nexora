package com.nexora.vpn.core.stats

/**
 * What the app did while connecting and disconnecting, for the Logs screen and
 * for a customer reporting a problem.
 *
 * App-level events only — "starting the core", "connected", "the core refused
 * the configuration: …". Never the core's traffic log, never a destination,
 * never the configuration: it is meant to be shared with support as is.
 *
 * In memory and bounded; it resets when the app process ends.
 */
class ConnectionLog(private val capacity: Int = 300) {

    enum class Level { INFO, WARNING, ERROR }

    data class Entry(val atMs: Long, val level: Level, val message: String)

    private val lock = Any()
    private val entries = ArrayDeque<Entry>()

    /** Incremented on every change, so a screen can observe it cheaply. */
    @Volatile
    var version: Long = 0
        private set

    fun info(message: String, atMs: Long = System.currentTimeMillis()) =
        add(Entry(atMs, Level.INFO, message))

    fun warning(message: String, atMs: Long = System.currentTimeMillis()) =
        add(Entry(atMs, Level.WARNING, message))

    fun error(message: String, atMs: Long = System.currentTimeMillis()) =
        add(Entry(atMs, Level.ERROR, message))

    private fun add(entry: Entry) = synchronized(lock) {
        entries.addLast(entry.copy(message = entry.message.take(500)))
        while (entries.size > capacity) entries.removeFirst()
        version++
    }

    fun entries(level: Level? = null): List<Entry> = synchronized(lock) {
        entries.filter { level == null || it.level == level }
    }

    fun clear() = synchronized(lock) {
        entries.clear()
        version++
    }

    /** Plain text for "Report a problem": one line per entry, oldest first. */
    fun export(formatTime: (Long) -> String): String = synchronized(lock) {
        entries.joinToString("\n") { "${formatTime(it.atMs)} [${it.level}] ${it.message}" }
    }
}
