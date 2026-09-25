package com.nexora.vpn.core.vpn

import android.content.Context
import android.util.Log
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

    /**
     * Points the core at a directory for its data files. `geoip.dat` and
     * `geosite.dat` are not copied there: when a file is missing the library
     * reads it straight from the APK's assets, which is where the AAR put them.
     */
    fun init(context: Context) {
        if (initialised.getAndSet(true)) return
        val dir = context.applicationContext.filesDir.resolve("xray").apply { mkdirs() }
        Libv2ray.initCoreEnv(dir.absolutePath, "")
        Log.i(TAG, Libv2ray.checkVersionX())
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
