package io.github.nanomuse.app

import android.app.DownloadManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import androidx.core.app.NotificationManagerCompat
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.io.File
import java.lang.ref.WeakReference
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Evergreen Android shell self-updater.
 *
 * Routine UI/backend updates happen in the background via the ~3 MB `nanomuse` wheel inside
 * the runtime (or on the paired PC server), so the Android shell does not nag or force an 80 MB
 * APK reinstall for web/Python changes. Only releases that explicitly declare a newer native shell
 * (`<!-- minimuse-shell: X.Y.Z -->`) trigger a shell APK download — and all download triggers are
 * strictly single-flight with live progress reporting so repeated taps can never enqueue
 * duplicate APKs.
 */
object AppUpdate {
    private const val RELEASES_API = "https://api.github.com/repos/leothefleo49/MiniMuse/releases/latest"
    private const val RELEASES_LATEST_WEB = "https://github.com/leothefleo49/MiniMuse/releases/latest"
    private const val PREFS = "app_update"
    private const val LAST_CHECK = "last_check_ms"
    private const val LAST_RESULT = "last_result_json"
    private const val DOWNLOADING = "apk_download_id"
    private const val DOWNLOADING_VER = "app_update_downloading_ver"
    private const val CHECK_INTERVAL_MS = 15 * 60 * 1000L
    private const val NOTIFICATION_ID = 4711
    private const val AUTO_UPDATE = "auto_update_enabled"
    private const val AUTO_STARTED_VER = "auto_started_version"
    private const val PENDING_APK_URI = "pending_apk_uri"
    private const val DOWNLOADED_VER = "downloaded_version"
    private const val DOWNLOADED_URI = "downloaded_uri"

    private val SHELL_TAG_REGEX =
        Regex("""<!--\s*minimuse-shell:\s*([0-9]+(?:\.[0-9]+)+)\s*-->""", RegexOption.IGNORE_CASE)

    @Volatile
    internal var activeActivity: WeakReference<MainActivity>? = null

    private val downloadLock = Any()
    private val installStarting = AtomicBoolean(false)
    private val progressWatcherRunning = AtomicBoolean(false)
    private val checkInFlight = AtomicBoolean(false)

    private val http = OkHttpClient.Builder()
        .connectTimeout(8, TimeUnit.SECONDS).readTimeout(8, TimeUnit.SECONDS).build()

    class Available(
        val version: String,
        val apkUrl: String,
        val notesUrl: String,
        val shellVersion: String? = null,
        val shellRequired: Boolean = false,
    )

    private data class DownloadState(
        val active: Boolean,
        val id: Long,
        val version: String,
        val progressPct: Int,
        val completedUri: Uri?,
    )

    /** Remove old cached update APKs once the installed app has caught up past them. */
    fun cleanupDownloadedIfCurrent(context: Context) {
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val dlVer = prefs.getString(DOWNLOADED_VER, null)
        if (!dlVer.isNullOrBlank() && !newer(dlVer, BuildConfig.VERSION_NAME)) {
            val uriStr = prefs.getString(DOWNLOADED_URI, null)
            if (!uriStr.isNullOrBlank()) {
                runCatching { appCtx.contentResolver.delete(Uri.parse(uriStr), null, null) }
            }
            prefs.edit()
                .remove(DOWNLOADED_VER)
                .remove(DOWNLOADED_URI)
                .remove(AUTO_STARTED_VER)
                .remove(PENDING_APK_URI)
                .apply()
        }
    }

    private fun queryDownloadState(appCtx: Context): DownloadState {
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val dlId = prefs.getLong(DOWNLOADING, -1L)
        val ver = prefs.getString(DOWNLOADING_VER, null).orEmpty()
        if (dlId <= 0L) return DownloadState(false, -1L, "", 0, null)
        val mgr = appCtx.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager
            ?: return DownloadState(false, -1L, "", 0, null)
        val state = runCatching {
            mgr.query(DownloadManager.Query().setFilterById(dlId))?.use { c ->
                if (!c.moveToFirst()) return@use null
                val st = c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS))
                val bytes = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_BYTES_DOWNLOADED_SO_FAR))
                val total = c.getLong(c.getColumnIndexOrThrow(DownloadManager.COLUMN_TOTAL_SIZE_BYTES))
                val pct = if (total > 0L) ((bytes * 100L) / total).toInt().coerceIn(0, 100) else 0
                when (st) {
                    DownloadManager.STATUS_PENDING,
                    DownloadManager.STATUS_RUNNING,
                    DownloadManager.STATUS_PAUSED -> DownloadState(true, dlId, ver, pct, null)
                    DownloadManager.STATUS_SUCCESSFUL -> {
                        val uri = mgr.getUriForDownloadedFile(dlId)
                        DownloadState(false, dlId, ver, 100, uri)
                    }
                    else -> {
                        prefs.edit().remove(DOWNLOADING).remove(DOWNLOADING_VER).apply()
                        DownloadState(false, -1L, "", 0, null)
                    }
                }
            }
        }.getOrNull()
        if (state == null) {
            prefs.edit().remove(DOWNLOADING).remove(DOWNLOADING_VER).apply()
            return DownloadState(false, -1L, "", 0, null)
        }
        return state
    }

    private fun cachedAvailable(context: Context): Available? {
        cleanupDownloadedIfCurrent(context)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val cached = prefs.getString(LAST_RESULT, null) ?: return null
        return parse(cached)
    }

    /** Instantaneous snapshot for the JS bridge (no network calls). */
    fun statusJson(context: Context): String {
        val appCtx = context.applicationContext
        val available = cachedAvailable(appCtx)
        val dlState = queryDownloadState(appCtx)
        val dlVer = downloadedVersion(appCtx)
        val downloadedMatch = available != null && dlVer == available.version
        return JSONObject()
            .put("current", BuildConfig.VERSION_NAME)
            .put("shellVersion", BuildConfig.SHELL_VERSION)
            .put("version", available?.version ?: JSONObject.NULL)
            .put("latest", available?.version ?: JSONObject.NULL)
            .put("apkUrl", available?.apkUrl ?: JSONObject.NULL)
            .put("notes", available?.notesUrl ?: JSONObject.NULL)
            .put("notesUrl", available?.notesUrl ?: JSONObject.NULL)
            .put("shellRequired", available?.shellRequired ?: false)
            .put("autoUpdate", isAutoUpdateEnabled(appCtx))
            .put("downloadedVersion", dlVer ?: JSONObject.NULL)
            .put("downloaded", downloadedMatch)
            .put("downloading", dlState.active || installStarting.get())
            .put("downloadingVersion", if (dlState.active) dlState.version else JSONObject.NULL)
            .put("progress", dlState.progressPct)
            .put("canInstallPackages", canInstallPackages(appCtx))
            .toString()
    }

    fun emitState(context: Context) {
        val json = statusJson(context)
        val act = activeActivity?.get() ?: return
        if (!act.isFinishing && !act.isDestroyed) {
            act.emitAppUpdateJson(json)
        }
    }

    /** Check GitHub asynchronously on a background thread and emit updated state to WebView. */
    fun refreshAsync(context: Context) {
        if (!checkInFlight.compareAndSet(false, true)) return
        val appCtx = context.applicationContext
        Thread {
            try {
                check(appCtx, force = true)
                emitState(appCtx)
            } finally {
                checkInFlight.set(false)
            }
        }.start()
    }

    /**
     * Single-flight entry point from UI buttons. Never blocks the JS/UI thread and never starts
     * duplicate downloads when a download is already in progress.
     */
    fun installAsync(context: Context) {
        val appCtx = context.applicationContext
        val dl = queryDownloadState(appCtx)
        if (dl.active) {
            startProgressWatcher(appCtx)
            emitState(appCtx)
            return
        }
        if (dl.completedUri != null && dl.id > 0L) {
            handleDownloadComplete(appCtx, dl.id)
            emitState(appCtx)
            return
        }
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val dlVer = prefs.getString(DOWNLOADED_VER, null)
        val dlUriStr = prefs.getString(DOWNLOADED_URI, null)
        if (!dlVer.isNullOrBlank() && !dlUriStr.isNullOrBlank() && newer(dlVer, BuildConfig.VERSION_NAME)) {
            val uri = Uri.parse(dlUriStr)
            val exists = runCatching { appCtx.contentResolver.openInputStream(uri)?.use { true } }.getOrNull() == true
            if (exists) {
                launchInstaller(context, uri, promptPermission = true)
                emitState(appCtx)
                return
            }
        }
        if (!installStarting.compareAndSet(false, true)) {
            emitState(appCtx)
            return
        }
        emitState(appCtx)
        Thread {
            try {
                val available = check(appCtx, force = true)
                if (available != null) {
                    install(context, available)
                }
            } finally {
                installStarting.set(false)
                emitState(appCtx)
            }
        }.start()
    }

    /** The newest release's version and its local APK's download URL; null when none/older/offline. */
    fun check(context: Context, force: Boolean = false): Available? {
        cleanupDownloadedIfCurrent(context)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!force) {
            val cached = prefs.getString(LAST_RESULT, null)
            val age = System.currentTimeMillis() - prefs.getLong(LAST_CHECK, 0L)
            if (cached != null && age in 0 until CHECK_INTERVAL_MS) return parse(cached)
        }
        val body = try {
            val r = http.newCall(Request.Builder()
                .url(RELEASES_API)
                .header("Accept", "application/vnd.github+json")
                .header("User-Agent", "MiniMuse/" + BuildConfig.VERSION_NAME)
                .build()).execute()
            if (r.isSuccessful) {
                r.body?.string()
            } else {
                null
            }
        } catch (_: Exception) {
            null
        }
        if (body != null) {
            prefs.edit()
                .putLong(LAST_CHECK, System.currentTimeMillis())
                .putString(LAST_RESULT, body)
                .apply()
            return parse(body)
        }
        // Fallback: follow GitHub's HTML redirect on /releases/latest (immune to 60/hr API rate limits)
        return try {
            val r = http.newCall(Request.Builder()
                .url(RELEASES_LATEST_WEB)
                .header("User-Agent", "MiniMuse/" + BuildConfig.VERSION_NAME)
                .build()).execute()
            val finalUrl = r.request.url.toString()
            val m = Regex("""/releases/tag/v?(\d+(?:\.\d+)+)""").find(finalUrl)
            val ver = m?.groupValues?.getOrNull(1) ?: return null
            if (!newer(ver, BuildConfig.VERSION_NAME)) return null
            val apkUrl = "https://github.com/leothefleo49/MiniMuse/releases/download/v$ver/MiniMuse-$ver-arm64.apk"
            Available(ver, apkUrl, finalUrl, shellVersion = null, shellRequired = false)
        } catch (_: Exception) {
            null
        }
    }

    private fun parse(body: String): Available? {
        return try {
            val rel = JSONObject(body)
            val tag = rel.optString("tag_name").trimStart('v')
            if (!newer(tag, BuildConfig.VERSION_NAME)) return null
            val preferred = (rel.optJSONArray("assets") ?: return null).let { assets ->
                val apks = (0 until assets.length()).mapNotNull { i ->
                    assets.optJSONObject(i)?.let { a ->
                        val name = a.optString("name")
                        val url = a.optString("browser_download_url")
                        if (name.endsWith(".apk") && url.isNotBlank()) name to url else null
                    }
                }
                apks.firstOrNull { (name, _) ->
                    name.contains("arm64", ignoreCase = true) &&
                        !name.contains("connect", ignoreCase = true) &&
                        !name.contains("wear", ignoreCase = true)
                }?.second
                    ?: apks.firstOrNull { (name, _) ->
                        !name.contains("connect", ignoreCase = true) &&
                            !name.contains("wear", ignoreCase = true)
                    }?.second
                    ?: apks.firstOrNull()?.second
                    ?: return null
            }
            val relBody = rel.optString("body")
            val declaredShell = SHELL_TAG_REGEX.find(relBody)?.groupValues?.getOrNull(1)
            val shellRequired = declaredShell != null && newer(declaredShell, BuildConfig.SHELL_VERSION)
            Available(tag, preferred, rel.optString("html_url"), shellVersion = declaredShell, shellRequired = shellRequired)
        } catch (_: Exception) {
            null
        }
    }

    private fun newer(candidate: String, current: String): Boolean {
        val numRe = Regex("""\d+""")
        val curClean = current.substringBefore('-')
        val candClean = candidate.trimStart('v').substringBefore('-')
        val a = numRe.findAll(candClean).mapNotNull { it.value.toIntOrNull() }.toList()
        val b = numRe.findAll(curClean).mapNotNull { it.value.toIntOrNull() }.toList()
        if (a.isEmpty() || b.isEmpty()) return false
        for (i in 0 until maxOf(a.size, b.size)) {
            val x = a.getOrNull(i) ?: 0
            val y = b.getOrNull(i) ?: 0
            if (x != y) return x > y
        }
        return false
    }

    /**
     * Download the APK (deduplicated against any active DownloadManager transfer) and open Android's
     * install sheet when ready.
     */
    fun install(context: Context, available: Available) {
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getString(DOWNLOADED_VER, null) == available.version) {
            val uriStr = prefs.getString(DOWNLOADED_URI, null)
            if (!uriStr.isNullOrBlank()) {
                val uri = Uri.parse(uriStr)
                val exists = runCatching { appCtx.contentResolver.openInputStream(uri)?.use { true } }.getOrNull() == true
                if (exists) {
                    launchInstaller(context, uri, promptPermission = true)
                    return
                }
            }
        }
        synchronized(downloadLock) {
            val dl = queryDownloadState(appCtx)
            if (dl.active && dl.version == available.version) {
                startProgressWatcher(appCtx)
                emitState(appCtx)
                return
            }
            if (dl.completedUri != null && dl.version == available.version) {
                handleDownloadComplete(appCtx, dl.id)
                return
            }
            if (!canInstallPackages(appCtx)) {
                requestInstallPackagesPermission(context)
            }
            enqueueDownload(appCtx, available)
        }
    }

    fun canInstallPackages(context: Context): Boolean =
        android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.O ||
            context.packageManager.canRequestPackageInstalls()

    fun requestInstallPackagesPermission(context: Context) {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O && !canInstallPackages(context)) {
            val settingsIntent = Intent(
                android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:" + context.applicationContext.packageName)
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(settingsIntent) }
        }
    }

    private fun enqueueDownload(appCtx: Context, available: Available): Long {
        val mgr = appCtx.getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
        val name = "MiniMuse-${available.version}.apk"
        // Remove stale partial/duplicate downloads of the same version so Android doesn't number them -1, -2, ...
        runCatching {
            val dlDir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            dlDir?.listFiles { _, fname ->
                fname.startsWith("MiniMuse-${available.version}") && fname.endsWith(".apk")
            }?.forEach { runCatching { it.delete() } }
        }
        val id = mgr.enqueue(
            DownloadManager.Request(Uri.parse(available.apkUrl))
                .setTitle(name)
                .setMimeType("application/vnd.android.package-archive")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(
                    Environment.DIRECTORY_DOWNLOADS, name
                )
        )
        appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .putLong(DOWNLOADING, id)
            .putString(DOWNLOADING_VER, available.version)
            .apply()
        startProgressWatcher(appCtx)
        emitState(appCtx)
        return id
    }

    private fun startProgressWatcher(appCtx: Context) {
        if (!progressWatcherRunning.compareAndSet(false, true)) return
        Thread {
            try {
                while (true) {
                    val st = queryDownloadState(appCtx)
                    if (st.active) {
                        emitState(appCtx)
                        Thread.sleep(700)
                        continue
                    }
                    if (st.completedUri != null && st.id > 0L) {
                        handleDownloadComplete(appCtx, st.id)
                    }
                    emitState(appCtx)
                    break
                }
            } catch (_: InterruptedException) {
            } finally {
                progressWatcherRunning.set(false)
            }
        }.start()
    }

    internal fun launchInstaller(context: Context, uri: Uri, promptPermission: Boolean): Boolean {
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!canInstallPackages(appCtx)) {
            prefs.edit().putString(PENDING_APK_URI, uri.toString()).apply()
            if (promptPermission) {
                requestInstallPackagesPermission(context)
            }
            return false
        }
        prefs.edit().remove(PENDING_APK_URI).apply()
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }
        return true
    }

    fun retryPendingInstall(context: Context) {
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val uriStr = prefs.getString(PENDING_APK_URI, null) ?: return
        if (!canInstallPackages(appCtx)) return
        val uri = Uri.parse(uriStr)
        val exists = runCatching { appCtx.contentResolver.openInputStream(uri)?.use { true } }.getOrNull() == true
        if (!exists) {
            prefs.edit().remove(PENDING_APK_URI).apply()
            return
        }
        launchInstaller(context, uri, promptPermission = false)
    }

    internal fun handleDownloadComplete(context: Context, completedId: Long) {
        if (completedId == -1L) return
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (prefs.getLong(DOWNLOADING, -1L) != completedId) return
        val downloadingVer = prefs.getString(DOWNLOADING_VER, null)
        prefs.edit().remove(DOWNLOADING).remove(DOWNLOADING_VER).apply()
        val mgr = appCtx.getSystemService(Context.DOWNLOAD_SERVICE) as? DownloadManager ?: return
        val ok = mgr.query(DownloadManager.Query().setFilterById(completedId))?.use { c ->
            c.moveToFirst() && c.getInt(c.getColumnIndexOrThrow(DownloadManager.COLUMN_STATUS)) == DownloadManager.STATUS_SUCCESSFUL
        } ?: false
        if (!ok) {
            emitState(appCtx)
            return
        }
        val uri = mgr.getUriForDownloadedFile(completedId) ?: return
        prefs.edit()
            .putString(DOWNLOADED_URI, uri.toString())
            .apply { if (!downloadingVer.isNullOrBlank()) putString(DOWNLOADED_VER, downloadingVer) }
            .apply()

        val verLabel = downloadingVer ?: "latest"
        val act = activeActivity?.get()
        if (act != null && !act.isFinishing && !act.isDestroyed) {
            act.runOnUiThread {
                emitState(appCtx)
                launchInstaller(act, uri, promptPermission = true)
            }
        } else {
            launchInstaller(appCtx, uri, promptPermission = false)
        }

        // Also post a direct Activity notification so tapping it always opens the installer on Android 10-15
        val nm = NotificationManagerCompat.from(appCtx)
        val channel = android.app.NotificationChannel(
            "app-update", appCtx.getString(R.string.update_channel),
            android.app.NotificationManager.IMPORTANCE_HIGH
        )
        nm.createNotificationChannel(channel)
        val actIntent = Intent(appCtx, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_INSTALL_APK_URI, uri.toString())
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val flags = android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
        val tap = android.app.PendingIntent.getActivity(appCtx, NOTIFICATION_ID + 1, actIntent, flags)
        val n = androidx.core.app.NotificationCompat.Builder(appCtx, "app-update")
            .setSmallIcon(R.drawable.ic_stat_nanomuse)
            .setContentTitle("MiniMuse v$verLabel downloaded")
            .setContentText("Tap to finish installing the update")
            .setPriority(androidx.core.app.NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        try {
            nm.notify(NOTIFICATION_ID, n)
        } catch (_: SecurityException) {
        }
    }

    fun isAutoUpdateEnabled(context: Context): Boolean =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(AUTO_UPDATE, true)

    fun setAutoUpdateEnabled(context: Context, on: Boolean) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean(AUTO_UPDATE, on).apply()
    }

    fun downloadedVersion(context: Context): String? =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(DOWNLOADED_VER, null)

    fun checkAndNotify(context: Context) {
        checkAndAutoPrepare(context) {}
    }

    /**
     * Periodic background check: only auto-downloads the shell APK when a newer native shell
     * (`shellRequired == true`) is released. All routine Web/Python updates apply inside the
     * evergreen runtime in the background without forcing an APK reinstall.
     */
    fun checkAndAutoPrepare(context: Context, onAvailable: (Available) -> Unit = {}) {
        val available = check(context) ?: return
        val appCtx = context.applicationContext
        val prefs = appCtx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!available.shellRequired) {
            emitState(appCtx)
            onAvailable(available)
            return
        }
        val nm = NotificationManagerCompat.from(appCtx)
        val channel = android.app.NotificationChannel(
            "app-update", appCtx.getString(R.string.update_channel),
            android.app.NotificationManager.IMPORTANCE_DEFAULT
        )
        nm.createNotificationChannel(channel)
        val tap = PendingIntentHolder.installIntent(appCtx, available)
        val n = androidx.core.app.NotificationCompat.Builder(appCtx, "app-update")
            .setSmallIcon(R.drawable.ic_stat_nanomuse)
            .setContentTitle(appCtx.getString(R.string.update_available_title, available.version))
            .setContentText(appCtx.getString(R.string.update_available_text))
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        try {
            nm.notify(NOTIFICATION_ID, n)
        } catch (_: SecurityException) {
        }
        if (isAutoUpdateEnabled(appCtx)) {
            synchronized(downloadLock) {
                val dl = queryDownloadState(appCtx)
                val alreadyDownloaded = prefs.getString(DOWNLOADED_VER, null) == available.version
                val alreadyStarted = prefs.getString(AUTO_STARTED_VER, null) == available.version
                if (!dl.active && !alreadyDownloaded && !alreadyStarted) {
                    prefs.edit().putString(AUTO_STARTED_VER, available.version).apply()
                    runCatching { enqueueDownload(appCtx, available) }
                } else if (dl.active) {
                    startProgressWatcher(appCtx)
                }
            }
        }
        emitState(appCtx)
        onAvailable(available)
    }

    /** Receiver for [DownloadManager.ACTION_DOWNLOAD_COMPLETE]. */
    class Receiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != DownloadManager.ACTION_DOWNLOAD_COMPLETE) return
            val id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1L)
            handleDownloadComplete(context, id)
        }
    }

    /**
     * Triggered when the user taps the notification: opens [MainActivity] / kicks off download or
     * installs directly.
     */
    class InstallReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val ver = intent.getStringExtra("version") ?: return
            val url = intent.getStringExtra("url") ?: return
            val notes = intent.getStringExtra("notes") ?: ""
            install(context, Available(ver, url, notes, shellVersion = ver, shellRequired = true))
        }
    }

    private object PendingIntentHolder {
        fun installIntent(context: Context, a: Available): android.app.PendingIntent {
            val intent = Intent(context, InstallReceiver::class.java)
                .putExtra("version", a.version)
                .putExtra("url", a.apkUrl)
                .putExtra("notes", a.notesUrl)
            val flags = android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
            return android.app.PendingIntent.getBroadcast(context, NOTIFICATION_ID, intent, flags)
        }
    }
}
