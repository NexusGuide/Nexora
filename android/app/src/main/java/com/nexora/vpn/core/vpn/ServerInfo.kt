package com.nexora.vpn.core.vpn

/**
 * What the server list shows about a config, derived from what the panel
 * actually sent — never invented.
 *
 * Panels name configs like "Speed 🇳🇱HollandXM": the country is the flag
 * emoji, when there is one. Region filters are built from that, and a config
 * without a flag is simply "other" rather than guessed at from its IP.
 */
data class ServerInfo(
    /** ISO 3166-1 alpha-2, upper case, or null when the name carries no flag. */
    val countryCode: String?,
    /** The flag itself, for display, or null. */
    val flag: String?,
    /** The name with the flag removed and spacing tidied. */
    val label: String,
    val region: Region,
) {
    enum class Region { EUROPE, ASIA, AMERICA, OTHER }

    companion object {
        /** Regional indicator symbols: U+1F1E6 ('A') … U+1F1FF ('Z'). */
        private const val INDICATOR_A = 0x1F1E6
        private const val INDICATOR_Z = 0x1F1FF

        fun from(name: String): ServerInfo {
            val codePoints = name.codePoints().toArray()
            var code: String? = null
            var flag: String? = null
            val rest = StringBuilder()
            var i = 0
            while (i < codePoints.size) {
                val cp = codePoints[i]
                val next = codePoints.getOrNull(i + 1)
                if (code == null && cp in INDICATOR_A..INDICATOR_Z &&
                    next != null && next in INDICATOR_A..INDICATOR_Z
                ) {
                    code = charArrayOf(
                        ('A' + (cp - INDICATOR_A)),
                        ('A' + (next - INDICATOR_A)),
                    ).concatToString()
                    flag = String(intArrayOf(cp, next), 0, 2)
                    i += 2
                    continue
                }
                rest.appendCodePoint(cp)
                i++
            }
            val label = rest.toString().replace(Regex("\\s+"), " ").trim().ifEmpty { name.trim() }
            return ServerInfo(code, flag, label, regionOf(code))
        }

        private val EUROPE = setOf(
            "AL", "AD", "AT", "BY", "BE", "BA", "BG", "HR", "CY", "CZ", "DK", "EE", "FI",
            "FR", "DE", "GR", "HU", "IS", "IE", "IT", "LV", "LI", "LT", "LU", "MT", "MD",
            "MC", "ME", "NL", "MK", "NO", "PL", "PT", "RO", "RU", "SM", "RS", "SK", "SI",
            "ES", "SE", "CH", "UA", "GB", "UK", "VA", "XK",
        )
        private val AMERICA = setOf(
            "US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "EC", "BO", "PY", "UY",
            "CR", "PA", "GT", "HN", "SV", "NI", "CU", "DO", "JM", "PR", "BS", "TT",
        )
        private val ASIA = setOf(
            "AE", "AF", "AM", "AZ", "BH", "BD", "BT", "BN", "KH", "CN", "GE", "HK", "IN",
            "ID", "IR", "IQ", "IL", "JP", "JO", "KZ", "KW", "KG", "LA", "LB", "MO", "MY",
            "MV", "MN", "MM", "NP", "KP", "OM", "PK", "PS", "PH", "QA", "SA", "SG", "KR",
            "LK", "SY", "TW", "TJ", "TH", "TL", "TR", "TM", "UZ", "VN", "YE",
        )

        fun regionOf(code: String?): Region = when (code) {
            null -> Region.OTHER
            in EUROPE -> Region.EUROPE
            in ASIA -> Region.ASIA
            in AMERICA -> Region.AMERICA
            else -> Region.OTHER
        }
    }
}
