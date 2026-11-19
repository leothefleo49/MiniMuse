package io.github.nanomuse.app

import android.webkit.JavascriptInterface
import org.json.JSONObject

/**
 * `window.NanoMuseAndroid` inside the page. The web app uses it to know it runs in the phone
 * app (so it hides the Web Push setup, which the app replaces) and to offer the phone-side
 * settings from its own Settings screen.
 */
class Bridge(private val activity: MainActivity) {
    private val prefs = Prefs(activity)

    @JavascriptInterface
    fun version(): String = BuildConfig.VERSION_NAME

    @JavascriptInterface
    fun serverUrl(): String = prefs.serverUrl

    /** "local" when the server runs on this phone, "remote" when it is the user's computer. */
    @JavascriptInterface
    fun mode(): String = prefs.mode.ifEmpty { Prefs.MODE_REMOTE }

    /** Whether the background connection (and with it, notifications) is on. */
    @JavascriptInterface
    fun notificationsEnabled(): Boolean = prefs.notify

    @JavascriptInterface
    fun setNotificationsEnabled(on: Boolean) {
        prefs.notify = on
        activity.runOnUiThread { NotifyService.sync(activity) }
    }

    /** Forget this server and return to the Connect screen. */
    @JavascriptInterface
    fun disconnect() {
        activity.runOnUiThread { activity.forget() }
    }

    /**
     * The agent's browser runs in this app: pull it into the take-over sheet. When the user
     * taps Done the page dispatches `nanomuse:browser-handed-back` with the thread, and the
     * web app tells the server.
     */
    @JavascriptInterface
    fun takeOverBrowser(thread: String?) {
        activity.runOnUiThread { activity.takeOverBrowser(thread ?: "") }
    }

    /** Whether this app has a browser of its own to take over (always, on Android). */
    @JavascriptInterface
    fun hasBrowser(): Boolean = true

    /**
     * Operating the phone: "on" when the accessibility service runs, "off" when it does not,
     * "unsupported" below Android 11 (no screenshots for accessibility services there).
     */
    @JavascriptInterface
    fun accessibilityState(): String = when {
        android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.R -> "unsupported"
        io.github.nanomuse.app.gui.MuseAccessibilityService.available -> "on"
        else -> "off"
    }

    /** Android's accessibility settings, where the service is switched on (and off). */
    @JavascriptInterface
    fun openAccessibilitySettings() {
        activity.runOnUiThread {
            try {
                activity.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            } catch (_: Exception) {
            }
        }
    }

    /** This app's page in Android's settings — for "Allow restricted settings" on Android 13+. */
    @JavascriptInterface
    fun openAppSettings() {
        activity.runOnUiThread {
            try {
                activity.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
                        .setData(android.net.Uri.parse("package:" + activity.packageName))
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            } catch (_: Exception) {
            }
        }
    }

    /**
     * What stands in the way of running in the background, as JSON: `battery_unrestricted`,
     * `overlay`, `exact_alarms` (granted · denied · n/a), `boot_start`, `vendor`,
     * `autostart_settings` (whether this phone has a page we can open), `android`, `local`.
     */
    @JavascriptInterface
    fun keepRunning(): String = KeepRunning.status(activity).toString()

    /** Open the settings page for one of them: battery, overlay, alarms, autostart, app. */
    @JavascriptInterface
    fun openKeepRunning(what: String?) {
        activity.runOnUiThread { KeepRunning.open(activity, what ?: "") }
    }

    @JavascriptInterface
    fun setStartOnBoot(on: Boolean) {
        prefs.startOnBoot = on
    }

    /** Zip the crash files, the runtime's log and the app's logcat lines; open the share sheet. */
    @JavascriptInterface
    fun exportLogs() {
        activity.runOnUiThread { Thread { Diagnostics.export(activity) }.start() }
    }

    /**
     * Pin a workspace file — an app the agent built — to the home screen. Android shows its
     * own confirm dialog; opening the shortcut lands on the file alone, fullscreen.
     */
    @JavascriptInterface
    fun addHomeShortcut(name: String?, path: String?) {
        activity.runOnUiThread { activity.pinAppShortcut(name?.trim().takeUnless { it.isNullOrEmpty() } ?: "App", path ?: "") }
    }

    /** Open the native QR scanner directly to pair with a computer or server without signing out. */
    @JavascriptInterface
    fun scanToPair() {
        activity.runOnUiThread { activity.scanToPair() }
    }

    /**
     * A pairing QR scanned on the start screen: the app started the local runtime for it and
     * parked the payload here. Taken (and cleared) once by the page when it loads, which
     * claims the pairing straight away.
     */
    @JavascriptInterface
    fun takePendingPairPayload(): String {
        val payload = prefs.pendingPair
        if (payload.isNotEmpty()) prefs.pendingPair = ""
        return payload
    }

    /** Hardware fingerprint / face unlock readiness for local & LAN HTTP WebView pages. */
    @JavascriptInterface
    fun biometricStatus(): String = activity.biometricStatusJson()

    /** Show the system BiometricPrompt and emit `nanomuse:native-biometric` to the page. */
    @JavascriptInterface
    fun authenticateBiometric(requestId: String?, title: String?, subtitle: String?) {
        val rid = requestId?.trim().orEmpty()
        if (rid.isEmpty()) return
        activity.runOnUiThread {
            activity.authenticateBiometric(rid, title ?: "", subtitle ?: "")
        }
    }

    /** Whether automatic update checks and background downloads are enabled. */
    @JavascriptInterface
    fun autoUpdateEnabled(): Boolean = AppUpdate.isAutoUpdateEnabled(activity)

    @JavascriptInterface
    fun setAutoUpdateEnabled(on: Boolean) {
        AppUpdate.setAutoUpdateEnabled(activity, on)
    }

    /** Instant cached status JSON from the shell updater (never blocks the JS thread). */
    @JavascriptInterface
    fun checkAppUpdate(): String = AppUpdate.statusJson(activity)

    /** Refresh release metadata asynchronously and emit `nanomuse:app-update`. */
    @JavascriptInterface
    fun refreshAppUpdate() {
        AppUpdate.refreshAsync(activity)
    }

    /** Single-flight download/install of the APK shell (no-op duplicate guard while active). */
    @JavascriptInterface
    fun installAppUpdate() {
        AppUpdate.installAsync(activity)
    }

    /** Check whether Android's "Install unknown apps" permission is granted for MiniMuse. */
    @JavascriptInterface
    fun canInstallPackages(): Boolean = AppUpdate.canInstallPackages(activity)

    /** Open Android's "Install unknown apps" screen for MiniMuse so updates & watch APKs can install. */
    @JavascriptInterface
    fun requestInstallPermission() {
        activity.runOnUiThread { AppUpdate.requestInstallPackagesPermission(activity) }
    }

    /** Request any needed runtime + install permissions for watch auto-install & updates. */
    @JavascriptInterface
    fun requestAllPermissions() {
        activity.runOnUiThread { activity.requestAllPermissions() }
    }

    /** Is MiniMuse the phone's default digital assistant? "on", "off" or "unknown". */
    @JavascriptInterface
    fun assistantState(): String = io.github.nanomuse.app.assist.Assist.state(activity)

    /** Open Android's *Default digital assistant app* page, the only place one can be chosen. */
    @JavascriptInterface
    fun openAssistantSettings() {
        activity.runOnUiThread {
            try {
                activity.startActivity(io.github.nanomuse.app.assist.Assist.settingsIntent())
            } catch (_: Exception) {
                activity.startActivity(android.content.Intent(android.provider.Settings.ACTION_SETTINGS))
            }
        }
    }

    /** Ask for one permission by name: notifications, camera, microphone, location, calendar, contacts, nearby, install. */
    @JavascriptInterface
    fun requestPermission(key: String?) {
        activity.runOnUiThread { activity.requestPermissionByKey(key.orEmpty()) }
    }

    /** Return JSON summarizing granted permissions on this phone. */
    @JavascriptInterface
    fun permissionsStatus(): String = activity.permissionsStatusJson()

    /** JSON array of Google/calendar account emails already signed in on this phone (if permitted). */
    @JavascriptInterface
    fun deviceGoogleEmails(): String = activity.deviceGoogleEmailsJson()

    /** Request Calendar/Contacts permissions if needed and sync phone Google Calendar & Contacts into MiniMuse. */
    @JavascriptInterface
    fun syncPhoneCalendarAndContacts() {
        activity.runOnUiThread { activity.syncPhoneCalendarAndContacts() }
    }

    /** Discover Wear OS watches advertising Wireless Debugging mDNS on the local Wi-Fi network. */
    @JavascriptInterface
    fun discoverWatchAdb(): String = WearAdbDiscovery.discoverJson(activity)

    /** Open Android Settings → Developer Options (or About Phone if Developer Options isn't unlocked yet). */
    @JavascriptInterface
    fun openDeveloperSettings() {
        activity.runOnUiThread {
            try {
                activity.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            } catch (_: Exception) {
                try {
                    activity.startActivity(
                        android.content.Intent(android.provider.Settings.ACTION_DEVICE_INFO_SETTINGS)
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                    )
                } catch (_: Exception) {
                }
            }
        }
    }

    /** Open Android Settings → Notification Access / App Notifications for Live Notifications. */
    @JavascriptInterface
    fun openNotificationListenerSettings() {
        activity.runOnUiThread {
            try {
                activity.startActivity(
                    android.content.Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                        .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            } catch (_: Exception) {
                try {
                    activity.startActivity(
                        android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, activity.packageName)
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK),
                    )
                } catch (_: Exception) {
                }
            }
        }
    }

    companion object {
        const val NAME = "NanoMuseAndroid"
    }
}
