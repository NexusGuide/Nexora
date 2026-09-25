package com.nexora.vpn.core.vpn

/**
 * Bytes moved since the previous reading, as the core reports them.
 *
 * The core's counters are read-and-reset: each query returns what passed
 * since the last one, in the form `tag,direction,value;…`. Only the proxy
 * outbound counts as "through the VPN"; traffic routed direct (the local
 * network, Iranian sites) is reported separately so the numbers the customer
 * sees match what their plan is charged for.
 */
data class TrafficCounters(
    val proxyUp: Long = 0,
    val proxyDown: Long = 0,
    val directUp: Long = 0,
    val directDown: Long = 0,
) {
    operator fun plus(other: TrafficCounters) = TrafficCounters(
        proxyUp + other.proxyUp,
        proxyDown + other.proxyDown,
        directUp + other.directUp,
        directDown + other.directDown,
    )

    companion object {
        fun parse(raw: String?): TrafficCounters {
            if (raw.isNullOrBlank()) return TrafficCounters()
            var result = TrafficCounters()
            for (entry in raw.split(';')) {
                val parts = entry.split(',')
                if (parts.size != 3) continue
                val value = parts[2].trim().toLongOrNull()?.takeIf { it > 0 } ?: continue
                val up = parts[1].trim() == "uplink"
                val down = parts[1].trim() == "downlink"
                result = when (parts[0].trim()) {
                    XrayConfigBuilder.TAG_PROXY -> when {
                        up -> result.copy(proxyUp = result.proxyUp + value)
                        down -> result.copy(proxyDown = result.proxyDown + value)
                        else -> result
                    }
                    XrayConfigBuilder.TAG_DIRECT -> when {
                        up -> result.copy(directUp = result.directUp + value)
                        down -> result.copy(directDown = result.directDown + value)
                        else -> result
                    }
                    else -> result
                }
            }
            return result
        }
    }
}
