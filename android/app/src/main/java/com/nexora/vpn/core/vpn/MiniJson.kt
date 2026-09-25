package com.nexora.vpn.core.vpn

/**
 * The small amount of JSON the VPN layer needs: writing the Xray config, and
 * reading the flat object inside a `vmess://` link.
 *
 * Kept free of every dependency, kotlinx included, so this package compiles
 * and its tests run with nothing but the Kotlin standard library. The config
 * it writes is what decides whether a customer's connection works, and that
 * is worth being able to test anywhere.
 *
 * Values: `null`, [Boolean], [Number], [String], [Map] with string keys, and
 * [List]. Nothing else.
 */
internal object MiniJson {

    fun write(value: Any?): String = StringBuilder().also { append(it, value) }.toString()

    private fun append(out: StringBuilder, value: Any?) {
        when (value) {
            null -> out.append("null")
            is Boolean -> out.append(value)
            is Int, is Long, is Short, is Byte -> out.append(value)
            is Double, is Float -> {
                val d = value.toDouble()
                require(d.isFinite()) { "JSON cannot hold $d" }
                out.append(if (d == Math.floor(d) && kotlin.math.abs(d) < 1e15) d.toLong() else d)
            }
            is String -> appendString(out, value)
            is Map<*, *> -> {
                out.append('{')
                var first = true
                for ((key, item) in value) {
                    if (!first) out.append(',')
                    first = false
                    appendString(out, key as String)
                    out.append(':')
                    append(out, item)
                }
                out.append('}')
            }
            is List<*> -> {
                out.append('[')
                value.forEachIndexed { index, item ->
                    if (index > 0) out.append(',')
                    append(out, item)
                }
                out.append(']')
            }
            else -> throw IllegalArgumentException("cannot write ${value::class.simpleName}")
        }
    }

    private fun appendString(out: StringBuilder, value: String) {
        out.append('"')
        for (c in value) {
            when (c) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                else -> if (c < ' ') {
                    out.append("\\u").append(String.format("%04x", c.code))
                } else {
                    out.append(c)
                }
            }
        }
        out.append('"')
    }

    /** Parses any JSON value. Throws [IllegalArgumentException] on malformed input. */
    fun parse(text: String): Any? {
        val reader = Reader(text)
        val value = reader.value()
        reader.skipWhitespace()
        require(reader.atEnd()) { "trailing content at ${reader.pos}" }
        return value
    }

    private class Reader(private val s: String) {
        var pos = 0

        fun atEnd() = pos >= s.length

        fun skipWhitespace() {
            while (pos < s.length && s[pos].isWhitespace()) pos++
        }

        private fun peek(): Char {
            require(pos < s.length) { "unexpected end" }
            return s[pos]
        }

        private fun expect(c: Char) {
            require(peek() == c) { "expected '$c' at $pos" }
            pos++
        }

        fun value(): Any? {
            skipWhitespace()
            return when (val c = peek()) {
                '{' -> obj()
                '[' -> array()
                '"' -> string()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else -> if (c == '-' || c.isDigit()) number() else {
                    throw IllegalArgumentException("unexpected '$c' at $pos")
                }
            }
        }

        private fun literal(word: String, result: Any?): Any? {
            require(s.startsWith(word, pos)) { "bad literal at $pos" }
            pos += word.length
            return result
        }

        private fun obj(): Map<String, Any?> {
            expect('{')
            val result = LinkedHashMap<String, Any?>()
            skipWhitespace()
            if (peek() == '}') { pos++; return result }
            while (true) {
                skipWhitespace()
                val key = string()
                skipWhitespace()
                expect(':')
                result[key] = value()
                skipWhitespace()
                if (peek() == ',') { pos++; continue }
                expect('}')
                return result
            }
        }

        private fun array(): List<Any?> {
            expect('[')
            val result = ArrayList<Any?>()
            skipWhitespace()
            if (peek() == ']') { pos++; return result }
            while (true) {
                result.add(value())
                skipWhitespace()
                if (peek() == ',') { pos++; continue }
                expect(']')
                return result
            }
        }

        private fun string(): String {
            expect('"')
            val out = StringBuilder()
            while (true) {
                val c = peek()
                pos++
                when (c) {
                    '"' -> return out.toString()
                    '\\' -> {
                        when (val e = peek().also { pos++ }) {
                            '"', '\\', '/' -> out.append(e)
                            'n' -> out.append('\n')
                            'r' -> out.append('\r')
                            't' -> out.append('\t')
                            'b' -> out.append('\b')
                            'f' -> out.append('\u000C')
                            'u' -> {
                                require(pos + 4 <= s.length) { "bad escape at $pos" }
                                out.append(s.substring(pos, pos + 4).toInt(16).toChar())
                                pos += 4
                            }
                            else -> throw IllegalArgumentException("bad escape '\\$e'")
                        }
                    }
                    else -> out.append(c)
                }
            }
        }

        private fun number(): Number {
            val start = pos
            if (peek() == '-') pos++
            while (pos < s.length && (s[pos].isDigit() || s[pos] in ".eE+-")) pos++
            val text = s.substring(start, pos)
            return text.toLongOrNull() ?: text.toDoubleOrNull()
                ?: throw IllegalArgumentException("bad number '$text'")
        }
    }
}
