package io.github.nanomuse.app

import android.Manifest
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.UserManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import io.github.nanomuse.app.runtime.LocalRuntime
import io.github.nanomuse.app.runtime.RuntimeService

/**
 * After a reboot or app self-update: reconnect to the computer so approvals keep arriving, or
 * bring the phone's own server back so routines and check-ins run. On Android 14/15 if the OS
 * pauses background foreground-service launch (or when an APK update requires unpacking a new
 * rootfs capsule), posts a one-tap notification instead of failing silently.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        val isUpgrade = action == Intent.ACTION_MY_PACKAGE_REPLACED
        val isBoot = action in BOOT_ACTIONS
        if (!isUpgrade && !isBoot) return
        // Ignore Direct Boot callbacks before credential-encrypted storage is unlocked.
        val um = context.getSystemService(UserManager::class.java)
        if (um != null && !um.isUserUnlocked) return

        val prefs = Prefs(context)
        if (!isUpgrade && !prefs.startOnBoot) return
        if (!prefs.connected) return

        if (prefs.isLocal) {
            if (!LocalRuntime(context).installed) {
                postWakeNotification(
                    context,
                    Intent(context, ConnectActivity::class.java),
                    context.getString(R.string.runtime_updated_title),
                    context.getString(R.string.local_starting_body),
                )
                return
            }
            if (!RuntimeService.start(context)) {
                postWakeNotification(
                    context,
                    Intent(context, MainActivity::class.java),
                    context.getString(R.string.runtime_wake_title),
                    context.getString(R.string.runtime_wake_body),
                )
            }
        } else if (!NotifyService.sync(context)) {
            postWakeNotification(
                context,
                Intent(context, MainActivity::class.java),
                context.getString(R.string.runtime_wake_title),
                context.getString(R.string.runtime_wake_body),
            )
        }
    }

    private fun postWakeNotification(context: Context, target: Intent, title: String, body: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        val tap = PendingIntent.getActivity(
            context,
            NOTIFICATION_ID,
            target.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(context, App.CH_LINK)
            .setSmallIcon(R.drawable.ic_stat_nanomuse)
            .setContentTitle(title)
            .setContentText(body)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        runCatching { NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, n) }
    }

    companion object {
        private const val NOTIFICATION_ID = 4712
        private val BOOT_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_LOCKED_BOOT_COMPLETED,
            "android.intent.action.QUICKBOOT_POWERON",
            "com.htc.intent.action.QUICKBOOT_POWERON",
        )
    }
}
