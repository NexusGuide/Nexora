package com.nexora.vpn

import com.nexora.vpn.core.vpn.MiniJson
import com.nexora.vpn.core.vpn.ProxyProfile
import com.nexora.vpn.core.vpn.ProxyUri
import com.nexora.vpn.core.vpn.Security
import com.nexora.vpn.core.vpn.Transport
import com.nexora.vpn.core.vpn.UnsupportedProfileException
import com.nexora.vpn.core.vpn.XrayConfigBuilder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Share links in the shapes panels actually emit, and the Xray configuration
 * each must become. Every id, key and host here is invented.
 */
class XrayConfigTest {

    private val uuid = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"

    // --- vless ------------------------------------------------------------------

    @Test
    fun `vless over websocket and tls`() {
        val p = ProxyUri.parse(
            "vless://$uuid@203.0.113.6:443?encryption=none&security=tls&sni=edge.example.com" +
                "&fp=chrome&alpn=h2%2Chttp%2F1.1&type=ws&host=edge.example.com&path=%2Fws%3Fed%3D2048" +
                "#Speed%20%F0%9F%87%B3%F0%9F%87%B1HollandXM",
        ) as ProxyProfile.Vless

        assertEquals("Speed 🇳🇱HollandXM", p.name)
        assertEquals("203.0.113.6", p.address)
        assertEquals(443, p.port)
        assertEquals(uuid, p.id)
        assertEquals(Transport.Ws(path = "/ws?ed=2048", host = "edge.example.com"), p.transport)
        val tls = p.security as Security.Tls
        assertEquals("edge.example.com", tls.serverName)
        assertEquals(listOf("h2", "http/1.1"), tls.alpn)
    }

    @Test
    fun `vless reality with flow`() {
        val p = ProxyUri.parse(
            "vless://$uuid@r.example.net:8443?security=reality&sni=www.example.org&fp=firefox" +
                "&pbk=Zm9vYmFyYmF6cXV4&sid=6ba85179e30d4fc2&spx=%2F&type=tcp" +
                "&flow=xtls-rprx-vision#reality",
        ) as ProxyProfile.Vless

        assertEquals("xtls-rprx-vision", p.flow)
        val r = p.security as Security.Reality
        assertEquals("www.example.org", r.serverName)
        assertEquals("Zm9vYmFyYmF6cXV4", r.publicKey)
        assertEquals("6ba85179e30d4fc2", r.shortId)
        assertEquals("firefox", r.fingerprint)
    }

    @Test
    fun `reality without a public key is refused`() {
        assertUnsupported("vless://$uuid@r.example.net:443?security=reality&sni=a.b#x")
    }

    @Test
    fun `vless grpc and xhttp and httpupgrade transports`() {
        val grpc = ProxyUri.parse(
            "vless://$uuid@h.example:443?type=grpc&serviceName=svc&mode=multi&security=tls#g",
        )
        assertEquals(Transport.Grpc("svc", multiMode = true, authority = null), grpc.transport)

        val xhttp = ProxyUri.parse(
            "vless://$uuid@h.example:443?type=xhttp&path=%2Fx&host=cdn.example&mode=auto" +
                "&security=tls#x",
        )
        assertEquals(Transport.Xhttp("/x", "cdn.example", "auto", null), xhttp.transport)

        val upgrade = ProxyUri.parse("vless://$uuid@h.example:80?type=httpupgrade&path=%2Fu#u")
        assertEquals(Transport.HttpUpgrade("/u", null), upgrade.transport)
        assertEquals(Security.None, upgrade.security)
    }

    @Test
    fun `ipv6 literal host`() {
        val p = ProxyUri.parse("vless://$uuid@[2001:db8::7]:443?security=tls&sni=a.example#v6")
        assertEquals("2001:db8::7", p.address)
        assertEquals(443, p.port)
    }

    @Test
    fun `a missing name falls back to the host`() {
        assertEquals("h.example", ProxyUri.parse("vless://$uuid@h.example:443").name)
    }

    // --- vmess ------------------------------------------------------------------

    @Test
    fun `vmess base64 json with numeric port`() {
        val json = """{"v":"2","ps":"vm test","add":"v.example","port":8080,"id":"$uuid",""" +
            """"aid":"0","scy":"auto","net":"ws","type":"none","host":"v.example",""" +
            """"path":"/vm","tls":"tls","sni":"v.example","fp":"chrome"}"""
        val p = ProxyUri.parse("vmess://" + base64(json)) as ProxyProfile.Vmess

        assertEquals("vm test", p.name)
        assertEquals(8080, p.port)
        assertEquals(Transport.Ws("/vm", "v.example"), p.transport)
        assertEquals("v.example", (p.security as Security.Tls).serverName)
    }

    @Test
    fun `vmess without tls`() {
        val json = """{"ps":"plain","add":"1.2.3.4","port":"80","id":"$uuid","net":"tcp"}"""
        val p = ProxyUri.parse("vmess://" + base64(json))
        assertEquals(Security.None, p.security)
        assertEquals(Transport.Tcp(), p.transport)
    }

    // --- trojan -----------------------------------------------------------------

    @Test
    fun `trojan defaults to tls and keeps a plus in the password`() {
        val p = ProxyUri.parse(
            "trojan://pa+ss%40word@t.example:443?sni=t.example#tr",
        ) as ProxyProfile.Trojan
        assertEquals("pa+ss@word", p.password)
        assertTrue(p.security is Security.Tls)
    }

    // --- shadowsocks ------------------------------------------------------------

    @Test
    fun `shadowsocks in all three encodings`() {
        val sip002 = ProxyUri.parse(
            "ss://" + base64("chacha20-ietf-poly1305:s3cret") + "@s.example:8388#one",
        ) as ProxyProfile.Shadowsocks
        assertEquals("chacha20-ietf-poly1305", sip002.method)
        assertEquals("s3cret", sip002.password)
        assertEquals("one", sip002.name)

        val whole = ProxyUri.parse(
            "ss://" + base64("aes-256-gcm:pw:with:colons@s.example:443") + "#two",
        ) as ProxyProfile.Shadowsocks
        assertEquals("pw:with:colons", whole.password)
        assertEquals(443, whole.port)

        val plain = ProxyUri.parse(
            "ss://2022-blake3-aes-128-gcm:a2V5%3D%3D@s.example:443#three",
        ) as ProxyProfile.Shadowsocks
        assertEquals("2022-blake3-aes-128-gcm", plain.method)
        assertEquals("a2V5==", plain.password)
    }

    @Test
    fun `the panel's placeholder entries parse but point at loopback`() {
        // What PasarGuard sends; the backend drops these, but the app must not
        // crash if one arrives.
        val p = ProxyUri.parse(
            "ss://" + base64("chacha20-ietf-poly1305:x") + "@127.0.0.1:1080#nx_abc%20%7C%201",
        )
        assertEquals("127.0.0.1", p.address)
    }

    @Test
    fun `shadowsocks plugins are refused`() {
        assertUnsupported("ss://" + base64("aes-128-gcm:pw") + "@s.example:443?plugin=obfs-local#p")
    }

    // --- refusals ---------------------------------------------------------------

    @Test
    fun `links the core cannot run are refused with a reason`() {
        assertUnsupported("hysteria2://pw@h.example:443#h")
        assertUnsupported("vless://$uuid@h.example:443?type=kcp#k")
        assertUnsupported("vless://$uuid@h.example#noport")
        assertUnsupported("vless://@h.example:443#noid")
        assertUnsupported("not a link")
        assertUnsupported("vmess://%%%")
    }

    // --- the generated configuration ---------------------------------------------

    @Test
    fun `tunnel config has a tun inbound and no listening proxy port`() {
        val config = parsed(XrayConfigBuilder.forTunnel(sampleVless()))

        val inbounds = config["inbounds"] as List<*>
        assertEquals(1, inbounds.size)
        val tun = inbounds[0] as Map<*, *>
        assertEquals("tun", tun["protocol"])
        // A socks or http inbound would be reachable by every app on the phone.
        assertFalse(inbounds.any { (it as Map<*, *>)["protocol"] in setOf("socks", "http") })
    }

    @Test
    fun `the proxy is the first outbound so unmatched traffic takes it`() {
        val config = parsed(XrayConfigBuilder.forTunnel(sampleVless()))
        val first = (config["outbounds"] as List<*>)[0] as Map<*, *>
        assertEquals("proxy", first["tag"])
        assertEquals("vless", first["protocol"])

        val stream = first["streamSettings"] as Map<*, *>
        assertEquals("ws", stream["network"])
        assertEquals("tls", stream["security"])
        assertEquals("/ws", (stream["wsSettings"] as Map<*, *>)["path"])
        assertEquals("edge.example.com", (stream["tlsSettings"] as Map<*, *>)["serverName"])

        val user = (((first["settings"] as Map<*, *>)["vnext"] as List<*>)[0] as Map<*, *>)
            .let { (it["users"] as List<*>)[0] as Map<*, *> }
        assertEquals(uuid, user["id"])
        assertEquals("none", user["encryption"])
        assertNull(user["flow"])
    }

    @Test
    fun `dns is answered by the core and its lookups use the proxy`() {
        val rules = rules(XrayConfigBuilder.forTunnel(sampleVless()))
        assertTrue(rules.any { it["port"] == "53" && it["outboundTag"] == "dns-out" })
        assertTrue(
            rules.any {
                it["inboundTag"] == listOf("dns-query") && it["outboundTag"] == "proxy"
            },
        )
    }

    @Test
    fun `iranian destinations bypass the tunnel only when asked`() {
        val on = rules(XrayConfigBuilder.forTunnel(sampleVless()))
        assertTrue(on.any { (it["ip"] as? List<*>)?.contains("geoip:ir") == true })
        assertTrue(on.any { (it["domain"] as? List<*>)?.contains("geosite:category-ir") == true })

        val off = rules(
            XrayConfigBuilder.forTunnel(
                sampleVless(),
                XrayConfigBuilder.Options(bypassIran = false),
            ),
        )
        assertFalse(off.any { (it["ip"] as? List<*>)?.contains("geoip:ir") == true })
        // The local network stays direct either way.
        assertTrue(off.any { (it["ip"] as? List<*>)?.contains("geoip:private") == true })
    }

    @Test
    fun `reality settings reach the config`() {
        val p = ProxyUri.parse(
            "vless://$uuid@r.example.net:443?security=reality&sni=www.example.org" +
                "&pbk=Zm9vYmFy&sid=ab&type=tcp&flow=xtls-rprx-vision#r",
        )
        val out = (parsed(XrayConfigBuilder.forTunnel(p))["outbounds"] as List<*>)[0] as Map<*, *>
        val reality = (out["streamSettings"] as Map<*, *>)["realitySettings"] as Map<*, *>
        assertEquals("Zm9vYmFy", reality["publicKey"])
        assertEquals("www.example.org", reality["serverName"])
        assertEquals("ab", reality["shortId"])
    }

    @Test
    fun `shadowsocks outbound carries no stream settings`() {
        val p = ProxyUri.parse("ss://" + base64("aes-256-gcm:pw") + "@s.example:443#s")
        val out = (parsed(XrayConfigBuilder.forTunnel(p))["outbounds"] as List<*>)[0] as Map<*, *>
        assertEquals("shadowsocks", out["protocol"])
        assertNull(out["streamSettings"])
    }

    @Test
    fun `delay test config has no inbound at all`() {
        val config = parsed(XrayConfigBuilder.forDelayTest(sampleVless()))
        assertNull(config["inbounds"])
        assertEquals("proxy", ((config["outbounds"] as List<*>)[0] as Map<*, *>)["tag"])
    }

    @Test
    fun `profiles never print their credentials`() {
        val p = sampleVless()
        assertFalse(p.toString().contains(uuid))
        val trojan = ProxyUri.parse("trojan://topsecret@t.example:443#t")
        assertFalse(trojan.toString().contains("topsecret"))
    }

    // --- json round trip ---------------------------------------------------------

    @Test
    fun `json escapes control characters and quotes`() {
        val text = MiniJson.write(mapOf("a" to "q\"b\\\n\u0001", "n" to 5, "l" to listOf(true, null)))
        assertEquals("""{"a":"q\"b\\\n\u0001","n":5,"l":[true,null]}""", text)
        assertEquals(mapOf("a" to "q\"b\\\n\u0001", "n" to 5L, "l" to listOf(true, null)), MiniJson.parse(text))
    }

    // --- helpers -----------------------------------------------------------------

    private fun sampleVless() = ProxyUri.parse(
        "vless://$uuid@203.0.113.6:443?security=tls&sni=edge.example.com&type=ws" +
            "&host=edge.example.com&path=%2Fws#sample",
    )

    private fun parsed(json: String) = MiniJson.parse(json) as Map<*, *>

    @Suppress("UNCHECKED_CAST")
    private fun rules(json: String): List<Map<*, *>> =
        (parsed(json)["routing"] as Map<*, *>)["rules"] as List<Map<*, *>>

    private fun assertUnsupported(link: String) {
        try {
            ProxyUri.parse(link)
            fail("expected $link to be refused")
        } catch (expected: UnsupportedProfileException) {
            // refused, as it should be
        }
    }

    private fun base64(text: String): String {
        val table = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        val bytes = text.toByteArray(Charsets.UTF_8)
        val out = StringBuilder()
        var i = 0
        while (i < bytes.size) {
            val b0 = bytes[i].toInt() and 0xFF
            val b1 = if (i + 1 < bytes.size) bytes[i + 1].toInt() and 0xFF else -1
            val b2 = if (i + 2 < bytes.size) bytes[i + 2].toInt() and 0xFF else -1
            out.append(table[b0 shr 2])
            out.append(table[((b0 and 3) shl 4) or (if (b1 < 0) 0 else b1 shr 4)])
            out.append(if (b1 < 0) '=' else table[((b1 and 15) shl 2) or (if (b2 < 0) 0 else b2 shr 6)])
            out.append(if (b2 < 0) '=' else table[b2 and 63])
            i += 3
        }
        return out.toString()
    }
}
