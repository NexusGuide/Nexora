package com.nexora.vpn.core.vpn

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.nexora.vpn.MainActivity
import com.nexora.vpn.R
import dagger.hilt.android.AndroidEntryPoint
import java.util.concurrent.Executors
import javax.inject.Inject

/**
 * The tunnel. Creates the VPN interface, hands its file descriptor to the
 * Xray core, and keeps a notification up for as long as it runs.
 *
 * Nexora's own traffic — the core's connections to the server, and API calls
 * to the backend — is excluded from the interface with
 * [Builder.addDisallowedApplication]. Without that, the core's connection to
 * the server would be routed back into the tunnel it is carrying: a loop.
 */
@AndroidEntryPoint
class NexoraVpnService : VpnService() {

    @Inject lateinit var controller: VpnController

    /** Connect and disconnect run in order, never at the same time. */
    private val worker = Executors.newSingleThreadExecutor()

    private var tun: ParcelFileDescriptor? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CONNECT -> {
                // Within five seconds of startForegroundService, or Android
                // kills the app. Before anything slow.
                startInForeground(getString(R.string.vpn_notification_connecting))
                worker.execute(::connect)
            }
            ACTION_DISCONNECT -> worker.execute { disconnect(VpnState.Disconnected) }
            // Started by the system with no action — always-on VPN, or a
            // restart after the process died. There is no configuration to
            // run in memory any more, so there is nothing honest to do but stop.
            else -> worker.execute { disconnect(VpnState.Disconnected) }
        }
        // Not sticky: a restart without the configuration cannot connect.
        return START_NOT_STICKY
    }

    private fun connect() {
        val pending = controller.takePending()
        if (pending == null) {
            disconnect(VpnState.Disconnected)
            return
        }
        // Switching servers: the old tunnel goes first.
        teardown()

        val descriptor = try {
            buildInterface(pending.serverName)
        } catch (e: Exception) {
            Log.w(TAG, "establish failed", e)
            null
        }
        if (descriptor == null) {
            // establish() returns null when the permission was withdrawn.
            disconnect(VpnState.Failed(VpnState.Reason.INTERFACE_FAILED))
            return
        }
        tun = descriptor

        try {
            XrayCore.start(this, pending.configJson, descriptor.fd)
        } catch (e: Exception) {
            Log.w(TAG, "core failed to start: ${e.message}")
            disconnect(VpnState.Failed(VpnState.Reason.CORE_FAILED))
            return
        }

        controller.report(VpnState.Connected(pending.serverName, SystemClock.elapsedRealtime()))
        startInForeground(getString(R.string.vpn_notification_connected, pending.serverName))
    }

    private fun buildInterface(serverName: String): ParcelFileDescriptor? {
        val builder = Builder()
            .setSession(serverName)
            .setMtu(XrayConfigBuilder.DEFAULT_MTU)
            // A /30 of private space nothing else on a phone uses.
            .addAddress(TUN_IPV4, 30)
            .addRoute("0.0.0.0", 0)
            // IPv6 is routed in too, so it cannot leak around the tunnel. The
            // config resolves IPv4 only, so apps fall back to IPv4.
            .addAddress(TUN_IPV6, 126)
            .addRoute("::", 0)
            // Any address works: every packet enters the tunnel, and the core
            // answers port 53 itself.
            .addDnsServer(DNS_ADDRESS)
            .addDisallowedApplication(packageName)
            .setConfigureIntent(openAppIntent())

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            // The tunnel inherits the metered state of the network under it,
            // so Android's data saver keeps working.
            builder.setMetered(false)
        }
        return builder.establish()
    }

    /** Stops the core, closes the interface and reports [finalState]. */
    private fun disconnect(finalState: VpnState) {
        teardown()
        controller.report(finalState)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun teardown() {
        XrayCore.stop()
        runCatching { tun?.close() }
        tun = null
    }

    /** Another VPN app took the tunnel, or the user revoked it in Settings. */
    override fun onRevoke() {
        worker.execute { disconnect(VpnState.Failed(VpnState.Reason.REVOKED)) }
    }

    override fun onDestroy() {
        // Runs on the main thread; stopping the core here as well covers the
        // service being destroyed without a disconnect having been asked for.
        teardown()
        if (controller.state.value !is VpnState.Failed) {
            controller.report(VpnState.Disconnected)
        }
        worker.shutdown()
        super.onDestroy()
    }

    // --- notification -------------------------------------------------------------

    private fun startInForeground(text: String) {
        ensureChannel()
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_monochrome)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(false)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setContentIntent(openAppIntent())
            .addAction(0, getString(R.string.vpn_disconnect), disconnectIntent())
            .build()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            } else {
                0
            },
        )
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) != null) return
        manager.createNotificationChannel(
            NotificationChannel(
                CHANNEL_ID,
                getString(R.string.vpn_channel_name),
                // Low: a permanent status line, not something that makes a sound.
                NotificationManager.IMPORTANCE_LOW,
            ).apply { setShowBadge(false) },
        )
    }

    private fun openAppIntent(): PendingIntent = PendingIntent.getActivity(
        this,
        0,
        Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    private fun disconnectIntent(): PendingIntent = PendingIntent.getService(
        this,
        1,
        Intent(this, NexoraVpnService::class.java).setAction(ACTION_DISCONNECT),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    companion object {
        const val ACTION_CONNECT = "com.nexora.vpn.action.CONNECT"
        const val ACTION_DISCONNECT = "com.nexora.vpn.action.DISCONNECT"

        private const val TAG = "NexoraVpnService"
        private const val CHANNEL_ID = "vpn_status"
        private const val NOTIFICATION_ID = 1
        private const val TUN_IPV4 = "10.211.14.1"
        private const val TUN_IPV6 = "fd6e:7865:6f72::1"
        private const val DNS_ADDRESS = "1.1.1.1"
    }
}
