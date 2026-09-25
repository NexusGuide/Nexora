package com.nexora.vpn.core.vpn

import android.content.Context
import android.util.Log
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean
import libv2ray.CoreCallbackHandler
import libv2ray.CoreController
import libv2ray.Libv2ray

/**
 * The one place that touches the native Xray library (AndroidLibXrayLite,
 * LGPL-3.0, loaded as a shared library — see NOTICE.md).
 *
 * Everything else talks to [VpnController], so the native API can change
 * version without the rest of the app noticing.
 */
internal object XrayCore {

    private const val TAG = "XrayCore"

    /** Answers 204 with an empty body: the cheapest thing to fetch. */
    const val DELAY_TEST_URL = "https://www.gstatic.com/generate_204"

    private val initialised = AtomicBoolean(false)

    @Volatile
    private var controller: CoreController? = null

    /** The routing data files the configuration refers to (geoip:, geosite:). */
    private val GEO_FILES = listOf("geoip.dat", "geosite.dat")

    /**
     * Prepares the core: gives Go the Android context, and puts the routing
     * data files where it will look for them.
     *
     * Both were missing in the first build, and every connection failed with
     * "could not start": the configuration routes by `geoip:ir` and
     * `geosite:category-ir`, the core could not open those files, and it
     * refused the configuration. The library's fallback — reading them out of
     * the APK — only works once `Seq.setContext` has been called; copying them
     * to a real directory removes the dependency on that fallback altogether.
     */
    fun init(context: Context) {
        if (initialised.get()) return
        synchronized(this) {
            if (initialised.get()) return
            val app = context.applicationContext
            go.Seq.setContext(app)
            val dir = app.filesDir.resolve("xray").apply { mkdirs() }
            copyGeoFiles(app, dir)
            Libv2ray.initCoreEnv(dir.absolutePath, "")
            Log.i(TAG, Libv2ray.checkVersionX())
            initialised.set(true)
        }
    }

    /**
     * Copies the geo files out of the APK when they are missing or came with
     * another core version. About 28 MB, once per install or core update.
     */
    private fun copyGeoFiles(context: Context, dir: File) {
        // Keyed to the core's own version string: the files ship inside the
        // core's AAR, so they change exactly when it does. (The app's
        // versionCode stays the same across debug builds.)
        val version = Libv2ray.checkVersionX()
        val stamp = File(dir, ".geo-version")
        val upToDate = stamp.exists() && stamp.readText() == version &&
            GEO_FILES.all { File(dir, it).length() > 0 }
        if (upToDate) return

        for (name in GEO_FILES) {
            val target = File(dir, name)
            val partial = File(dir, "$name.part")
            context.assets.open(name).use { input ->
                partial.outputStream().use { output -> input.copyTo(output) }
            }
            // Renamed into place, so a copy interrupted half way is never
            // mistaken for a complete file.
            check(partial.renameTo(target)) { "could not move $name into place" }
        }
        stamp.writeText(version)
    }

    /**
     * Starts the core on the VPN interface [tunFd]. Returns once the core is
     * running; throws with the core's own message if it would not start.
     */
    fun start(context: Context, configJson: String, tunFd: Int) {
        init(context)
        val core = controller ?: Libv2ray.newCoreController(Callback).also { controller = it }
        core.startLoop(configJson, tunFd)
        check(core.isRunning) { "core did not report running" }
    }

    fun stop() {
        runCatching { controller?.stopLoop() }
            .onFailure { Log.w(TAG, "stopLoop failed", it) }
    }

    val isRunning: Boolean get() = controller?.isRunning == true

    /** Delay through the running tunnel, in ms, or null if it did not answer. */
    fun measureRunningDelay(): Long? =
        runCatching { controller?.measureDelay(DELAY_TEST_URL) }
            .getOrNull()
            ?.takeIf { it > 0 }

    /** Delay through a configuration that is not running, in ms, or null. */
    fun measureDelay(context: Context, delayConfigJson: String): Long? {
        init(context)
        return runCatching { Libv2ray.measureOutboundDelay(delayConfigJson, DELAY_TEST_URL) }
            .getOrNull()
            ?.takeIf { it > 0 }
    }

    /** The core's lifecycle callbacks. State is tracked by the service, not here. */
    private object Callback : CoreCallbackHandler {
        override fun startup(): Long = 0
        override fun shutdown(): Long = 0
        override fun onEmitStatus(code: Long, message: String?): Long {
            Log.d(TAG, "status $code")
            return 0
        }
    }
}
