package com.nexora.vpn.core.vpn

import java.net.URLDecoder
import java.nio.charset.StandardCharsets

/**
 * A connection the core can dial, parsed from the share link the panel hands
 * out (`vless://`, `vmess://`, `trojan://`, `ss://`).
 *
 * Plain Kotlin with no Android types, so every format quirk is covered by JVM
 * unit tests rather than discovered on a customer's phone.
 *
 * [toString] is overridden everywhere a credential lives: these objects hold
 * the user's own secret, and one will end up in a log eventually.
 */
sealed interface ProxyProfile {
    val name: String
    val address: String
    val port: Int
    val transport: Transport
    val security: Security

    data class Vless(
        override val name: String,
        override val address: String,
        override val port: Int,
        val id: String,
        val flow: String?,
        val encryption: String,
        override val transport: Transport,
        override val security: Security,
    ) : ProxyProfile {
        override fun toString() = "Vless($name, $address:$port, $transport, $security)"
    }

    data class Vmess(
        override val name: String,
        override val address: String,
        override val port: Int,
        val id: String,
        val cipher: String,
        override val transport: Transport,
        override val security: Security,
    ) : ProxyProfile {
        override fun toString() = "Vmess($name, $address:$port, $transport, $security)"
    }

    data class Trojan(
        override val name: String,
        override val address: String,
        override val port: Int,
        val password: String,
        override val transport: Transport,
        override val security: Security,
    ) : ProxyProfile {
        override fun toString() = "Trojan($name, $address:$port, $transport, $security)"
    }

    data class Shadowsocks(
        override val name: String,
        override val address: String,
        override val port: Int,
        val method: String,
        val password: String,
    ) : ProxyProfile {
        override val transport: Transport = Transport.Tcp()
        override val security: Security = Security.None
        override fun toString() = "Shadowsocks($name, $address:$port, $method)"
    }
}

sealed interface Transport {
    /** `headerType=http` disguises the stream as HTTP/1.1; `none` is raw TCP. */
    data class Tcp(val httpHost: String? = null, val httpPath: String? = null) : Transport
    data class Ws(val path: String, val host: String?) : Transport
    data class Grpc(val serviceName: String, val multiMode: Boolean, val authority: String?) :
        Transport
    data class HttpUpgrade(val path: String, val host: String?) : Transport
    data class Xhttp(val path: String, val host: String?, val mode: String?, val extra: String?) :
        Transport
}

sealed interface Security {
    data object None : Security

    data class Tls(
        val serverName: String?,
        val fingerprint: String?,
        val alpn: List<String>,
    ) : Security

    data class Reality(
        val serverName: String?,
        val fingerprint: String?,
        val publicKey: String,
        val shortId: String?,
        val spiderX: String?,
        val mldsa65Verify: String?,
    ) : Security {
        override fun toString() = "Reality(sni=$serverName)"
    }
}

/** Why a link could not be used. The message is for logs, never for the customer. */
class UnsupportedProfileException(message: String) : IllegalArgumentException(message)

object ProxyUri {

    /** Parses a share link. Throws [UnsupportedProfileException] with a reason. */
    fun parse(uri: String): ProxyProfile {
        val trimmed = uri.trim()
        val scheme = trimmed.substringBefore("://", "").lowercase()
        return when (scheme) {
            "vless" -> parseVless(trimmed)
            "vmess" -> parseVmess(trimmed)
            "trojan" -> parseTrojan(trimmed)
            "ss" -> parseShadowsocks(trimmed)
            "" -> throw UnsupportedProfileException("not a share link")
            else -> throw UnsupportedProfileException("unsupported scheme: $scheme")
        }
    }

    // --- vless / trojan: scheme://credential@host:port?params#name ----------

    private fun parseVless(uri: String): ProxyProfile.Vless {
        val link = Link.parse(uri)
        val id = link.userInfo?.takeIf { it.isNotBlank() }
            ?: throw UnsupportedProfileException("vless link has no id")
        return ProxyProfile.Vless(
            name = link.name,
            address = link.host,
            port = link.port,
            id = id,
            flow = link.params["flow"]?.takeIf { it.isNotBlank() },
            encryption = link.params["encryption"]?.takeIf { it.isNotBlank() } ?: "none",
            transport = transportOf(link.params, link.params["type"]),
            security = securityOf(link.params, link.params["security"], link.host),
        )
    }

    private fun parseTrojan(uri: String): ProxyProfile.Trojan {
        val link = Link.parse(uri)
        val password = link.userInfo?.takeIf { it.isNotEmpty() }
            ?: throw UnsupportedProfileException("trojan link has no password")
        return ProxyProfile.Trojan(
            name = link.name,
            address = link.host,
            port = link.port,
            password = password,
            transport = transportOf(link.params, link.params["type"]),
            // Trojan without TLS is not trojan; a link that omits `security`
            // still means TLS.
            security = securityOf(link.params, link.params["security"] ?: "tls", link.host),
        )
    }

    // --- vmess: base64 of a JSON object ---------------------------------------

    private fun parseVmess(uri: String): ProxyProfile.Vmess {
        val body = uri.substringAfter("://").substringBefore('#')
        val decoded = decodeBase64(body)
            ?: throw UnsupportedProfileException("vmess link is not base64")
        val obj = runCatching { MiniJson.parse(decoded) as? Map<*, *> }.getOrNull()
            ?: throw UnsupportedProfileException("vmess link is not JSON")

        // Panels disagree on whether port and aid are numbers or strings.
        fun field(key: String): String? = when (val v = obj[key]) {
            is String -> v.trim().takeIf { it.isNotEmpty() }
            is Number -> v.toLong().toString()
            else -> null
        }

        val address = field("add") ?: throw UnsupportedProfileException("vmess has no address")
        val port = field("port")?.toIntOrNull()?.takeIf { it in 1..65535 }
            ?: throw UnsupportedProfileException("vmess has no valid port")
        val id = field("id") ?: throw UnsupportedProfileException("vmess has no id")

        // vmess keeps the same settings under different names; map them onto
        // the query-parameter vocabulary the shared helpers speak.
        val params = buildMap {
            field("host")?.let { put("host", it) }
            field("path")?.let { put("path", it) }
            field("sni")?.let { put("sni", it) }
            field("fp")?.let { put("fp", it) }
            field("alpn")?.let { put("alpn", it) }
            field("type")?.let { put("headerType", it) }
            // For grpc, vmess puts the service name in `path`.
            if (field("net") == "grpc") field("path")?.let { put("serviceName", it) }
            field("type")?.takeIf { field("net") == "grpc" }?.let { put("mode", it) }
        }
        val tls = field("tls")?.lowercase()
        return ProxyProfile.Vmess(
            name = field("ps") ?: address,
            address = address,
            port = port,
            id = id,
            cipher = field("scy") ?: "auto",
            transport = transportOf(params, field("net")),
            security = securityOf(params, if (tls == "tls") "tls" else "none", address),
        )
    }

    // --- shadowsocks: three historical encodings -----------------------------

    private fun parseShadowsocks(uri: String): ProxyProfile.Shadowsocks {
        val afterScheme = uri.substringAfter("://")
        val name = afterScheme.substringAfter('#', "").let(::decodeComponent)
        val withoutName = afterScheme.substringBefore('#')

        if (withoutName.substringAfter('?', "").contains("plugin=")) {
            throw UnsupportedProfileException("shadowsocks plugins are not supported")
        }
        val core = withoutName.substringBefore('?').trimEnd('/')

        // ss://BASE64(method:password@host:port) — the whole thing encoded.
        val whole = if ('@' !in core) {
            decodeBase64(core) ?: throw UnsupportedProfileException("ss link is not base64")
        } else {
            core
        }

        val credential = whole.substringBeforeLast('@')
        val hostPort = whole.substringAfterLast('@')

        // SIP002 encodes the credential as base64; the older plain form
        // percent-encodes "method:password".
        val plain = if (':' in decodeComponent(credential)) {
            decodeComponent(credential)
        } else {
            decodeBase64(credential)
                ?: throw UnsupportedProfileException("ss credential is not base64")
        }
        val method = plain.substringBefore(':').trim()
        val password = plain.substringAfter(':', "")
        if (method.isEmpty() || password.isEmpty()) {
            throw UnsupportedProfileException("ss credential has no method or password")
        }
        val (host, port) = splitHostPort(hostPort)
        return ProxyProfile.Shadowsocks(
            name = name.ifBlank { host },
            address = host,
            port = port,
            method = method.lowercase(),
            password = password,
        )
    }

    // --- shared ------------------------------------------------------------------

    private fun transportOf(params: Map<String, String>, type: String?): Transport =
        when (type?.lowercase()?.ifBlank { null } ?: "tcp") {
            "tcp", "raw" -> if (params["headerType"] == "http") {
                Transport.Tcp(httpHost = params["host"], httpPath = params["path"])
            } else {
                Transport.Tcp()
            }
            "ws" -> Transport.Ws(path = params["path"] ?: "/", host = params["host"])
            "grpc" -> Transport.Grpc(
                serviceName = params["serviceName"] ?: params["path"] ?: "",
                multiMode = params["mode"] == "multi",
                authority = params["authority"],
            )
            "httpupgrade" ->
                Transport.HttpUpgrade(path = params["path"] ?: "/", host = params["host"])
            "xhttp", "splithttp" -> Transport.Xhttp(
                path = params["path"] ?: "/",
                host = params["host"],
                mode = params["mode"],
                extra = params["extra"],
            )
            else -> throw UnsupportedProfileException("unsupported transport: $type")
        }

    private fun securityOf(
        params: Map<String, String>,
        security: String?,
        host: String,
    ): Security {
        val sni = params["sni"]?.ifBlank { null } ?: params["peer"]?.ifBlank { null }
        val fingerprint = params["fp"]?.ifBlank { null }
        return when (security?.lowercase()?.ifBlank { null } ?: "none") {
            "none" -> Security.None
            "tls" -> Security.Tls(
                serverName = sni ?: params["host"]?.ifBlank { null } ?: host.takeUnless(::isIp),
                fingerprint = fingerprint ?: "chrome",
                alpn = params["alpn"]?.split(',')?.map { it.trim() }?.filter { it.isNotEmpty() }
                    ?: emptyList(),
            )
            "reality" -> Security.Reality(
                serverName = sni,
                fingerprint = fingerprint ?: "chrome",
                publicKey = params["pbk"]?.ifBlank { null }
                    ?: throw UnsupportedProfileException("reality link has no public key"),
                shortId = params["sid"]?.ifBlank { null },
                spiderX = params["spx"]?.ifBlank { null },
                mldsa65Verify = params["pqv"]?.ifBlank { null },
            )
            else -> throw UnsupportedProfileException("unsupported security: $security")
        }
    }

    /** `scheme://userinfo@host:port/path?query#fragment`, tolerant of what panels emit. */
    private class Link(
        val userInfo: String?,
        val host: String,
        val port: Int,
        val params: Map<String, String>,
        val name: String,
    ) {
        companion object {
            fun parse(uri: String): Link {
                val afterScheme = uri.substringAfter("://")
                val fragment = afterScheme.substringAfter('#', "")
                val withoutFragment = afterScheme.substringBefore('#')
                val query = withoutFragment.substringAfter('?', "")
                val authorityAndPath = withoutFragment.substringBefore('?')
                val authority = authorityAndPath.substringBefore('/')

                val userInfo = if ('@' in authority) {
                    decodeComponent(authority.substringBeforeLast('@'))
                } else {
                    null
                }
                val (host, port) = splitHostPort(authority.substringAfterLast('@'))

                val params = query.split('&')
                    .filter { it.isNotEmpty() }
                    .associate { pair ->
                        decodeComponent(pair.substringBefore('=')) to
                            decodeComponent(pair.substringAfter('=', ""))
                    }
                val name = decodeComponent(fragment).ifBlank { host }
                return Link(userInfo, host, port, params, name)
            }
        }
    }

    private fun splitHostPort(value: String): Pair<String, Int> {
        val host: String
        val portText: String
        if (value.startsWith('[')) {
            // IPv6 literal: [2001:db8::1]:443
            host = value.substringAfter('[').substringBefore(']')
            portText = value.substringAfter("]:", "")
        } else {
            host = value.substringBeforeLast(':')
            portText = value.substringAfterLast(':', "")
        }
        val port = portText.toIntOrNull()?.takeIf { it in 1..65535 }
            ?: throw UnsupportedProfileException("no valid port")
        if (host.isBlank()) throw UnsupportedProfileException("no host")
        return host to port
    }

    /** Percent-decoding that leaves `+` alone: in a path or password it is a plus. */
    private fun decodeComponent(value: String): String =
        runCatching {
            URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8.name())
        }.getOrDefault(value)

    /**
     * Standard or URL-safe base64, with or without padding and line breaks.
     *
     * Hand-written because `java.util.Base64` needs API 26 and this app runs
     * on 24, and `android.util.Base64` does not exist in JVM unit tests.
     */
    internal fun decodeBase64(value: String): String? {
        val clean = value.filterNot { it.isWhitespace() }.trimEnd('=')
        if (clean.isEmpty()) return null
        val out = java.io.ByteArrayOutputStream(clean.length * 3 / 4)
        var buffer = 0
        var bits = 0
        for (c in clean) {
            val v = when (c) {
                in 'A'..'Z' -> c - 'A'
                in 'a'..'z' -> c - 'a' + 26
                in '0'..'9' -> c - '0' + 52
                '+', '-' -> 62
                '/', '_' -> 63
                else -> return null
            }
            buffer = (buffer shl 6) or v
            bits += 6
            if (bits >= 8) {
                bits -= 8
                out.write((buffer shr bits) and 0xFF)
            }
        }
        // A lone trailing 6-bit group cannot encode a byte: not base64.
        if (clean.length % 4 == 1) return null
        val bytes = out.toByteArray()
        val text = String(bytes, StandardCharsets.UTF_8)
        // Decoding plain text as base64 "succeeds" into garbage; a real
        // payload decodes to valid UTF-8 without replacement characters.
        return text.takeUnless { '\uFFFD' in it }
    }

    private fun isIp(host: String): Boolean =
        ':' in host || host.all { it.isDigit() || it == '.' }
}
