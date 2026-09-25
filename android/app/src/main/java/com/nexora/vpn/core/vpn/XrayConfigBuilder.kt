package com.nexora.vpn.core.vpn

/**
 * Turns a [ProxyProfile] into the JSON configuration Xray runs.
 *
 * Written against Xray's own configuration reference, not copied from another
 * client. Three decisions shape it:
 *
 * - **No local SOCKS or HTTP port.** Traffic enters through the `tun` inbound
 *   only. A listening proxy port on 127.0.0.1 can be used by any other app on
 *   the phone to find out the VPN's exit address, or to send traffic through
 *   it — a known leak in VPN clients that open one.
 * - **DNS goes through the tunnel.** Queries the phone sends are answered by
 *   Xray's DNS module, whose own lookups leave through the proxy, so the ISP
 *   sees neither the lookups nor their answers.
 * - **Iranian destinations go direct**, when [Options.bypassIran] is on.
 *   Domestic sites are often unreachable from foreign IPs, and sending them
 *   through the tunnel costs the customer traffic for nothing.
 */
object XrayConfigBuilder {

    data class Options(
        /** Route `.ir` domains and Iranian IP ranges around the tunnel. */
        val bypassIran: Boolean = true,
        /** Resolvers used through the tunnel. */
        val dnsServers: List<String> = listOf("1.1.1.1", "8.8.8.8"),
        val mtu: Int = DEFAULT_MTU,
        val logLevel: String = "warning",
    )

    const val DEFAULT_MTU = 1500
    const val TAG_PROXY = "proxy"
    const val TAG_DIRECT = "direct"
    const val TAG_BLOCK = "block"
    const val TAG_DNS_OUT = "dns-out"
    const val TAG_TUN = "tun"
    private const val TAG_DNS_QUERY = "dns-query"

    /** The full configuration for a connection through the VPN interface. */
    fun forTunnel(profile: ProxyProfile, options: Options = Options()): String =
        MiniJson.write(
            linkedMapOf(
                "log" to mapOf("loglevel" to options.logLevel),
                "stats" to emptyMap<String, Any>(),
                "policy" to mapOf(
                    "system" to mapOf(
                        "statsOutboundUplink" to true,
                        "statsOutboundDownlink" to true,
                    ),
                ),
                "inbounds" to listOf(tunInbound(options.mtu)),
                "outbounds" to listOf(
                    outbound(profile),
                    directOutbound(),
                    blockOutbound(),
                    mapOf("tag" to TAG_DNS_OUT, "protocol" to "dns"),
                ),
                "dns" to dns(options),
                "routing" to routing(options),
            ),
        )

    /**
     * A configuration with no inbound, for measuring a server's delay before
     * connecting. The core dials the outbound directly.
     */
    fun forDelayTest(profile: ProxyProfile): String =
        MiniJson.write(
            linkedMapOf(
                "log" to mapOf("loglevel" to "none"),
                "outbounds" to listOf(outbound(profile), directOutbound()),
            ),
        )

    // --- inbound ----------------------------------------------------------------

    private fun tunInbound(mtu: Int) = linkedMapOf(
        "tag" to TAG_TUN,
        "protocol" to "tun",
        // The core reads the file descriptor of the VPN interface from the
        // environment; the name is only a label.
        "settings" to mapOf("name" to "xray0", "MTU" to mtu),
        "sniffing" to mapOf(
            "enabled" to true,
            "destOverride" to listOf("http", "tls", "quic"),
            // Sniffed domains are used for routing only; the connection still
            // goes to the IP the app asked for, so nothing breaks when a
            // domain resolves differently at the server.
            "routeOnly" to true,
        ),
    )

    // --- outbounds ---------------------------------------------------------------

    private fun outbound(profile: ProxyProfile): Map<String, Any?> {
        val base = linkedMapOf<String, Any?>(
            "tag" to TAG_PROXY,
            "protocol" to protocolName(profile),
            "settings" to settings(profile),
        )
        if (profile !is ProxyProfile.Shadowsocks) {
            base["streamSettings"] = streamSettings(profile.transport, profile.security)
        }
        return base
    }

    private fun protocolName(profile: ProxyProfile) = when (profile) {
        is ProxyProfile.Vless -> "vless"
        is ProxyProfile.Vmess -> "vmess"
        is ProxyProfile.Trojan -> "trojan"
        is ProxyProfile.Shadowsocks -> "shadowsocks"
    }

    private fun settings(profile: ProxyProfile): Map<String, Any?> = when (profile) {
        is ProxyProfile.Vless -> mapOf(
            "vnext" to listOf(
                mapOf(
                    "address" to profile.address,
                    "port" to profile.port,
                    "users" to listOf(
                        linkedMapOf<String, Any?>(
                            "id" to profile.id,
                            "encryption" to profile.encryption,
                        ).apply { profile.flow?.let { put("flow", it) } },
                    ),
                ),
            ),
        )
        is ProxyProfile.Vmess -> mapOf(
            "vnext" to listOf(
                mapOf(
                    "address" to profile.address,
                    "port" to profile.port,
                    "users" to listOf(mapOf("id" to profile.id, "security" to profile.cipher)),
                ),
            ),
        )
        is ProxyProfile.Trojan -> mapOf(
            "servers" to listOf(
                mapOf(
                    "address" to profile.address,
                    "port" to profile.port,
                    "password" to profile.password,
                ),
            ),
        )
        is ProxyProfile.Shadowsocks -> mapOf(
            "servers" to listOf(
                mapOf(
                    "address" to profile.address,
                    "port" to profile.port,
                    "method" to profile.method,
                    "password" to profile.password,
                ),
            ),
        )
    }

    private fun streamSettings(transport: Transport, security: Security): Map<String, Any?> {
        val stream = linkedMapOf<String, Any?>()
        when (transport) {
            is Transport.Tcp -> {
                stream["network"] = "tcp"
                if (transport.httpHost != null || transport.httpPath != null) {
                    stream["tcpSettings"] = mapOf(
                        "header" to mapOf(
                            "type" to "http",
                            "request" to linkedMapOf<String, Any?>(
                                "path" to listOf(transport.httpPath ?: "/"),
                            ).apply {
                                transport.httpHost?.let {
                                    put("headers", mapOf("Host" to it.split(',').map(String::trim)))
                                }
                            },
                        ),
                    )
                }
            }
            is Transport.Ws -> {
                stream["network"] = "ws"
                stream["wsSettings"] = linkedMapOf<String, Any?>("path" to transport.path)
                    .apply { transport.host?.let { put("host", it) } }
            }
            is Transport.Grpc -> {
                stream["network"] = "grpc"
                stream["grpcSettings"] = linkedMapOf<String, Any?>(
                    "serviceName" to transport.serviceName,
                    "multiMode" to transport.multiMode,
                ).apply { transport.authority?.let { put("authority", it) } }
            }
            is Transport.HttpUpgrade -> {
                stream["network"] = "httpupgrade"
                stream["httpupgradeSettings"] =
                    linkedMapOf<String, Any?>("path" to transport.path)
                        .apply { transport.host?.let { put("host", it) } }
            }
            is Transport.Xhttp -> {
                stream["network"] = "xhttp"
                stream["xhttpSettings"] = linkedMapOf<String, Any?>("path" to transport.path)
                    .apply {
                        transport.host?.let { put("host", it) }
                        transport.mode?.let { put("mode", it) }
                        // `extra` is a JSON object the panel passes through
                        // verbatim; one that does not parse is dropped rather
                        // than sent to the core as a string.
                        transport.extra
                            ?.let { runCatching { MiniJson.parse(it) }.getOrNull() }
                            ?.takeIf { it is Map<*, *> }
                            ?.let { put("extra", it) }
                    }
            }
        }

        when (security) {
            Security.None -> stream["security"] = "none"
            is Security.Tls -> {
                stream["security"] = "tls"
                stream["tlsSettings"] = linkedMapOf<String, Any?>().apply {
                    security.serverName?.let { put("serverName", it) }
                    security.fingerprint?.let { put("fingerprint", it) }
                    if (security.alpn.isNotEmpty()) put("alpn", security.alpn)
                }
            }
            is Security.Reality -> {
                stream["security"] = "reality"
                stream["realitySettings"] = linkedMapOf<String, Any?>(
                    "publicKey" to security.publicKey,
                ).apply {
                    security.serverName?.let { put("serverName", it) }
                    security.fingerprint?.let { put("fingerprint", it) }
                    security.shortId?.let { put("shortId", it) }
                    security.spiderX?.let { put("spiderX", it) }
                    security.mldsa65Verify?.let { put("mldsa65Verify", it) }
                }
            }
        }
        return stream
    }

    private fun directOutbound() = mapOf(
        "tag" to TAG_DIRECT,
        "protocol" to "freedom",
        // Where the current core expects it; the older `settings` location
        // is deprecated and logs a warning on every start.
        "streamSettings" to mapOf("sockopt" to mapOf("domainStrategy" to "UseIP")),
    )

    private fun blockOutbound() = mapOf(
        "tag" to TAG_BLOCK,
        "protocol" to "blackhole",
    )

    // --- dns and routing -------------------------------------------------------------

    private fun dns(options: Options) = linkedMapOf(
        "tag" to TAG_DNS_QUERY,
        "servers" to options.dnsServers,
        "queryStrategy" to "UseIPv4",
    )

    private fun routing(options: Options): Map<String, Any?> {
        val rules = mutableListOf<Map<String, Any?>>(
            // Queries the phone sends to the VPN's DNS address are answered by
            // Xray's DNS module rather than forwarded raw.
            mapOf(
                "type" to "field",
                "inboundTag" to listOf(TAG_TUN),
                "port" to "53",
                "outboundTag" to TAG_DNS_OUT,
            ),
            // The DNS module's own lookups leave through the proxy.
            mapOf(
                "type" to "field",
                "inboundTag" to listOf(TAG_DNS_QUERY),
                "outboundTag" to TAG_PROXY,
            ),
            // The local network stays local: the router, a printer, a NAS.
            mapOf(
                "type" to "field",
                "ip" to listOf("geoip:private"),
                "outboundTag" to TAG_DIRECT,
            ),
        )
        if (options.bypassIran) {
            rules += mapOf(
                "type" to "field",
                "domain" to listOf("geosite:category-ir", "domain:ir"),
                "outboundTag" to TAG_DIRECT,
            )
            rules += mapOf(
                "type" to "field",
                "ip" to listOf("geoip:ir"),
                "outboundTag" to TAG_DIRECT,
            )
        }
        // Everything else takes the first outbound, the proxy. AsIs: traffic
        // from the tunnel already carries an IP, and resolving every sniffed
        // domain just to test it against IP rules would add a lookup to each
        // new connection.
        return linkedMapOf(
            "domainStrategy" to "AsIs",
            "rules" to rules,
        )
    }
}
