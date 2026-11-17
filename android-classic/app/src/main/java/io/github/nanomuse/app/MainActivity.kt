package io.github.nanomuse.app

import android.Manifest
import android.app.Activity
import android.app.DownloadManager
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.view.View
import android.webkit.CookieManager
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.util.Log
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import androidx.core.content.getSystemService
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.lifecycleScope
import io.github.nanomuse.app.browser.TakeOverSheet
import io.github.nanomuse.app.databinding.ActivityMainBinding
import io.github.nanomuse.app.device.ShareInbox
import io.github.nanomuse.app.runtime.LocalRuntime
import io.github.nanomuse.app.runtime.RuntimeService
import kotlinx.coroutines.launch
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit

/**
 * The app proper: the nanoMuse web app in a WebView, plus the parts a browser tab cannot do —
 * notifications while the screen is off (see [NotifyService]), the camera for QR codes,
 * a file picker for attachments, downloads, and a way back to the Connect screen.
 *
 * In local mode the server is the phone's own [RuntimeService]; the page is loaded once it
 * answers, and its failures are shown here with the runtime log.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var ui: ActivityMainBinding
    private lateinit var prefs: Prefs
    private var pendingThread: String? = null
    private var pendingShare: Intent? = null
    private var waitingForRuntime = false
    private var takeOver: TakeOverSheet? = null
    private val runtimeListener: (RuntimeService.State, String?) -> Unit = { state, detail ->
        runOnUiThread { runtimeChanged(state, detail) }
    }
    private var pendingFiles: ValueCallback<Array<Uri>>? = null
    private var pendingPermission: PermissionRequest? = null
    private val pairHttp by lazy {
        OkHttpClient.Builder()
            .connectTimeout(6, TimeUnit.SECONDS)
            .readTimeout(6, TimeUnit.SECONDS)
            .build()
    }

    private val pairScanner = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode != Activity.RESULT_OK) return@registerForActivityResult
        val text = result.data?.getStringExtra(QrScanActivity.EXTRA_QR_RESULT)?.trim().orEmpty()
        if (text.isNotEmpty()) pairFromQrLink(text)
    }

    private val pickFiles = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val cb = pendingFiles ?: return@registerForActivityResult
        pendingFiles = null
        val data = result.data
        val uris = when {
            result.resultCode != Activity.RESULT_OK || data == null -> null
            data.clipData != null -> Array(data.clipData!!.itemCount) { data.clipData!!.getItemAt(it).uri }
            data.data != null -> arrayOf(data.data!!)
            else -> null
        }
        cb.onReceiveValue(uris)
    }

    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        NotifyService.sync(this)
    }

    private val askCamera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        val req = pendingPermission ?: return@registerForActivityResult
        pendingPermission = null
        if (granted) req.grant(req.resources) else req.deny()
    }

    /** Camera and/or microphone for the page (getUserMedia), asked in one Android dialog. */
    private val askMediaPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { result ->
        val req = pendingPermission ?: return@registerForActivityResult
        pendingPermission = null
        val allowed = mutableListOf<String>()
        for (res in req.resources) {
            when (res) {
                PermissionRequest.RESOURCE_VIDEO_CAPTURE -> if (granted(Manifest.permission.CAMERA)) allowed.add(res)
                PermissionRequest.RESOURCE_AUDIO_CAPTURE -> if (granted(Manifest.permission.RECORD_AUDIO)) allowed.add(res)
            }
        }
        if (allowed.isEmpty()) req.deny() else req.grant(allowed.toTypedArray())
        emitPermissionsChanged()
        if (result.isEmpty()) Unit
    }

    /** The page's location request waiting on Android's location dialog. */
    private var pendingGeo: Pair<String, android.webkit.GeolocationPermissions.Callback>? = null

    private val askLocation = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        val (origin, cb) = pendingGeo ?: return@registerForActivityResult
        pendingGeo = null
        val ok = granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)
        cb.invoke(origin, ok, false)
        emitPermissionsChanged()
    }

    internal fun granted(permission: String): Boolean =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    private val askMultiplePermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        NotifyService.sync(this)
        emitPermissionsChanged()
    }

    private val askPhoneSyncPermissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        emitPermissionsChanged()
        extractAndEmitPhoneSync()
    }

    /** A home-screen shortcut's workspace file, waiting for the web app to be ready. */
    private var pendingShortcutPath: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        if (!prefs.connected || (prefs.isLocal && !LocalRuntime(this).installed)) {
            goConnect()
            return
        }
        ui = ActivityMainBinding.inflate(layoutInflater)
        setContentView(ui.root)
        ViewCompat.setOnApplyWindowInsetsListener(ui.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            WindowInsetsCompat.CONSUMED
        }
        setupWebView()
        ui.retry.setOnClickListener { retry() }
        ui.changeServer.setOnClickListener { forget() }
        if (prefs.isLocal) {
            ui.changeServer.text = getString(R.string.start_over)
            RuntimeService.listeners.add(runtimeListener)
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (ui.web.canGoBack()) ui.web.goBack() else moveTaskToBack(true)
            }
        })

        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            NotifyService.sync(this)
        }
        if (ShareInbox.isShare(intent)) pendingShare = intent
        intent?.getStringExtra(EXTRA_SHORTCUT_PATH)?.let { pendingShortcutPath = it }
        handleUpdateIntent(intent)
        load(intent?.getStringExtra(EXTRA_THREAD))
        // the once-a-day self-update check; a notification when a newer release is out
        Thread { AppUpdate.checkAndAutoPrepare(this) { available -> notifyAppUpdateAvailable(available) } }.start()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (handleUpdateIntent(intent)) return
        if (ShareInbox.isShare(intent)) {
            pendingShare = intent
            load(null)
            return
        }
        intent.getStringExtra(EXTRA_SHORTCUT_PATH)?.let { pendingShortcutPath = it }
        intent.getStringExtra(EXTRA_THREAD)?.let { load(it) }
    }

    private fun handleUpdateIntent(intent: Intent?): Boolean {
        if (intent == null) return false
        val apkUri = intent.getStringExtra(EXTRA_INSTALL_APK_URI)
        if (!apkUri.isNullOrBlank()) {
            intent.removeExtra(EXTRA_INSTALL_APK_URI)
            AppUpdate.launchInstaller(this, Uri.parse(apkUri), promptPermission = true)
            return true
        }
        val ver = intent.getStringExtra(EXTRA_INSTALL_UPDATE_VER)
        val url = intent.getStringExtra(EXTRA_INSTALL_UPDATE_URL)
        if (!ver.isNullOrBlank() && !url.isNullOrBlank()) {
            intent.removeExtra(EXTRA_INSTALL_UPDATE_VER)
            intent.removeExtra(EXTRA_INSTALL_UPDATE_URL)
            AppUpdate.install(this, AppUpdate.Available(ver, url, ""))
            return true
        }
        return false
    }

    private fun setupWebView() {
        val web = ui.web
        with(web.settings) {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = true
            cacheMode = WebSettings.LOAD_DEFAULT
            userAgentString = userAgentString + App.USER_AGENT_SUFFIX
            setSupportMultipleWindows(false)
        }
        CookieManager.getInstance().setAcceptCookie(true)
        web.addJavascriptInterface(Bridge(this), Bridge.NAME)
        web.setBackgroundColor(ContextCompat.getColor(this, R.color.bg))

        web.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (sameServer(url)) return false
                // anything else (links the agent found, OAuth pages…) belongs in the real browser
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, url)) }
                return true
            }

            override fun onPageFinished(view: WebView, url: String) {
                ui.offline.visibility = View.GONE
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                if (prefs.isLocal) {
                    if (RuntimeService.state == RuntimeService.State.STARTING || RuntimeService.state == RuntimeService.State.STOPPED) {
                        waitingForRuntime = true
                        showStarting()
                        if (RuntimeService.state == RuntimeService.State.STOPPED) RuntimeService.start(this@MainActivity)
                        return
                    }
                    showRuntimeFailure(RuntimeService.lastError ?: getString(R.string.offline_body, prefs.serverUrl))
                    return
                }
                ui.offlineTitle.text = getString(R.string.offline_title)
                ui.offlineBody.text = getString(R.string.offline_body, prefs.serverUrl)
                ui.offlineProgress.visibility = View.GONE
                ui.offlineLog.visibility = View.GONE
                ui.retry.visibility = View.VISIBLE
                ui.changeServer.visibility = View.VISIBLE
                ui.offline.visibility = View.VISIBLE
            }
        }

        web.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(
                webView: WebView,
                filePathCallback: ValueCallback<Array<Uri>>,
                params: FileChooserParams,
            ): Boolean {
                pendingFiles?.onReceiveValue(null)
                pendingFiles = filePathCallback
                val intent = params.createIntent().apply {
                    if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                    if (params.acceptTypes.none { it.isNotBlank() }) type = "*/*"
                }
                return runCatching { pickFiles.launch(intent); true }.getOrElse {
                    pendingFiles = null
                    false
                }
            }

            override fun onPermissionRequest(request: PermissionRequest) {
                // the page asks for the camera (attachments, QR) or the microphone (dictation, calls):
                // each is granted only when Android has granted the matching app permission, and
                // Android's own dialog is shown first when it has not
                val wantsVideo = request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
                val wantsAudio = request.resources.contains(PermissionRequest.RESOURCE_AUDIO_CAPTURE)
                if (!wantsVideo && !wantsAudio) {
                    request.deny()
                    return
                }
                val need = mutableListOf<String>()
                if (wantsVideo && !granted(Manifest.permission.CAMERA)) need.add(Manifest.permission.CAMERA)
                if (wantsAudio && !granted(Manifest.permission.RECORD_AUDIO)) need.add(Manifest.permission.RECORD_AUDIO)
                if (need.isEmpty()) {
                    request.grant(request.resources)
                } else {
                    pendingPermission?.deny()
                    pendingPermission = request
                    askMediaPermissions.launch(need.toTypedArray())
                }
            }

            override fun onGeolocationPermissionsShowPrompt(
                origin: String,
                callback: android.webkit.GeolocationPermissions.Callback,
            ) {
                // Without this override a WebView answers every location request with
                // "User denied Geolocation" and never asks. Ask Android first, then answer the page.
                if (granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION)) {
                    callback.invoke(origin, true, false)
                    return
                }
                pendingGeo?.let { (o, cb) -> cb.invoke(o, false, false) }
                pendingGeo = origin to callback
                askLocation.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
            }

            override fun onGeolocationPermissionsHidePrompt() {
                pendingGeo = null
            }
        }

        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            val uri = Uri.parse(url)
            if (!sameServer(uri)) {
                runCatching { startActivity(Intent(Intent.ACTION_VIEW, uri)) }
                return@setDownloadListener
            }
            // workspace files carry the token in the query, so the DownloadManager can fetch them as is
            val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
            val req = DownloadManager.Request(uri)
                .setMimeType(mimeType)
                .addRequestHeader("User-Agent", userAgent)
                .setTitle(name)
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            runCatching { getSystemService<DownloadManager>()?.enqueue(req) }
        }
    }

    private fun sameServer(url: Uri): Boolean {
        val server = Uri.parse(prefs.serverUrl)
        return url.scheme == server.scheme && url.host == server.host && url.port == server.port
    }

    private fun load(thread: String?) {
        if (prefs.isLocal && RuntimeService.state != RuntimeService.State.RUNNING) {
            // the phone's own server: start it (idempotent) and load once it answers
            pendingThread = thread
            waitingForRuntime = true
            if (RuntimeService.state == RuntimeService.State.FAILED) {
                showRuntimeFailure(RuntimeService.lastError ?: getString(R.string.runtime_no_answer))
            } else {
                showStarting()
            }
            RuntimeService.start(this)
            return
        }
        ui.offline.visibility = View.GONE
        // a home-screen shortcut opens its app alone on the page, nothing else around it
        pendingShortcutPath?.let { path ->
            pendingShortcutPath = null
            pendingThread = null
            ui.web.loadUrl(prefs.pageUrlForFile(path))
            return
        }
        val share = pendingShare
        if (share != null) {
            // something shared from another app: a new conversation with it in the composer
            pendingShare = null
            lifecycleScope.launch {
                val url = try {
                    ShareInbox.accept(this@MainActivity, prefs, share)
                } catch (e: Exception) {
                    Log.w("MainActivity", "share into nanoMuse failed", e)
                    Toast.makeText(this@MainActivity, getString(R.string.share_failed, e.message ?: ""), Toast.LENGTH_LONG).show()
                    prefs.pageUrl(thread)
                }
                ui.web.loadUrl(url)
            }
            return
        }
        ui.web.loadUrl(prefs.pageUrl(thread))
    }

    private fun retry() {
        if (prefs.isLocal && RuntimeService.state != RuntimeService.State.RUNNING) {
            showStarting()
            waitingForRuntime = true
            RuntimeService.start(this)
        } else {
            load(pendingThread)
        }
    }

    private fun runtimeChanged(state: RuntimeService.State, detail: String?) {
        if (!::ui.isInitialized) return
        when (state) {
            RuntimeService.State.RUNNING -> if (waitingForRuntime) {
                waitingForRuntime = false
                val t = pendingThread
                pendingThread = null
                load(t)
            }
            RuntimeService.State.FAILED -> showRuntimeFailure(detail ?: RuntimeService.lastError ?: getString(R.string.runtime_no_answer))
            else -> Unit
        }
    }

    private fun showStarting() {
        ui.offlineTitle.text = getString(R.string.local_starting_title)
        ui.offlineBody.text = getString(R.string.local_starting_body)
        ui.offlineProgress.visibility = View.VISIBLE
        ui.offlineLog.visibility = View.GONE
        ui.retry.visibility = View.GONE
        ui.changeServer.visibility = View.GONE
        ui.offline.visibility = View.VISIBLE
    }

    private fun showRuntimeFailure(message: String) {
        ui.offlineTitle.text = getString(R.string.offline_title)
        ui.offlineBody.text = message
        ui.offlineProgress.visibility = View.GONE
        val tail = LocalRuntime(this).logTail(30)
        ui.offlineLog.text = tail
        ui.offlineLog.visibility = if (tail.isBlank()) View.GONE else View.VISIBLE
        ui.retry.visibility = View.VISIBLE
        ui.changeServer.visibility = View.VISIBLE
        ui.offline.visibility = View.VISIBLE
    }

    /** From the JS bridge: the agent's browser (this app's own WebView) into the user's hands. */
    fun takeOverBrowser(thread: String) {
        val sheet = takeOver ?: TakeOverSheet(this) {
            val js = "window.dispatchEvent(new CustomEvent('nanomuse:browser-handed-back', {detail: {thread: " +
                org.json.JSONObject.quote(thread) + "}}))"
            if (::ui.isInitialized) ui.web.evaluateJavascript(js, null)
        }.also { takeOver = it }
        if (!sheet.show()) Toast.makeText(this, R.string.takeover_nothing, Toast.LENGTH_SHORT).show()
    }

    /** From the JS bridge and the offline screen: drop the server and start over. */
    fun forget() {
        NotifyService.stop(this)
        if (prefs.isLocal) RuntimeService.stop(this) // the phone's data stays on disk
        prefs.forget()
        ui.web.clearHistory()
        goConnect()
    }

    /** Launch the portrait-first QR scanner directly from WebView Settings/Devices to pair a PC. */
    fun scanToPair() {
        pairScanner.launch(Intent(this, QrScanActivity::class.java))
    }

    private fun pairFromQrLink(text: String) {
        val trimmed = text.trim()
        if (trimmed.startsWith("nanomuse://pair", ignoreCase = true) || trimmed.startsWith("nanomuse-pair:", ignoreCase = true)) {
            val detail = org.json.JSONObject().put("payload", trimmed).toString()
            val js = "window.dispatchEvent(new CustomEvent('nanomuse:sync-qr', {detail: $detail}))"
            runOnUiThread { ui.web.evaluateJavascript(js, null) }
            return
        }
        val parsed = Prefs.parseLink(text)
        if (parsed == null) {
            Toast.makeText(this, R.string.connect_bad_url, Toast.LENGTH_LONG).show()
            return
        }
        val (origin, token) = parsed
        if (token.isEmpty()) {
            Toast.makeText(this, R.string.connect_no_token, Toast.LENGTH_LONG).show()
            return
        }
        Toast.makeText(this, R.string.connect_checking, Toast.LENGTH_SHORT).show()
        val req = Request.Builder().url("$origin/api/state").header("Authorization", "Bearer $token").build()
        pairHttp.newCall(req).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread {
                    Toast.makeText(this@MainActivity, getString(R.string.connect_unreachable, origin), Toast.LENGTH_LONG).show()
                }
            }

            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string() ?: ""
                response.close()
                runOnUiThread {
                    when {
                        response.code == 401 || response.code == 403 ->
                            Toast.makeText(this@MainActivity, R.string.connect_unauthorized, Toast.LENGTH_LONG).show()
                        !response.isSuccessful ->
                            Toast.makeText(this@MainActivity, getString(R.string.connect_unreachable, origin), Toast.LENGTH_LONG).show()
                        else -> {
                            if (prefs.isLocal) {
                                RuntimeService.listeners.remove(runtimeListener)
                                RuntimeService.stop(this@MainActivity)
                            }
                            waitingForRuntime = false
                            pendingThread = null
                            prefs.mode = Prefs.MODE_REMOTE
                            prefs.serverUrl = origin
                            prefs.token = token
                            prefs.agentName = runCatching { JSONObject(body).getJSONObject("profile").optString("name") }.getOrDefault("")
                            if (::ui.isInitialized) {
                                ui.changeServer.text = getString(R.string.change_server)
                                ui.web.clearHistory()
                            }
                            NotifyService.sync(this@MainActivity)
                            val host = Uri.parse(origin).host ?: origin
                            Toast.makeText(this@MainActivity, getString(R.string.pair_connected, host), Toast.LENGTH_SHORT).show()
                            load(null)
                        }
                    }
                }
            }
        })
    }

    private val biometricAuthenticators: Int
        get() = BiometricManager.Authenticators.BIOMETRIC_STRONG or BiometricManager.Authenticators.BIOMETRIC_WEAK

    fun biometricStatusJson(): String {
        val status = BiometricManager.from(this).canAuthenticate(biometricAuthenticators)
        val available = status == BiometricManager.BIOMETRIC_SUCCESS || status == BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED
        val enrolled = status == BiometricManager.BIOMETRIC_SUCCESS
        val reason = when (status) {
            BiometricManager.BIOMETRIC_SUCCESS -> ""
            BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED -> "none_enrolled"
            BiometricManager.BIOMETRIC_ERROR_HW_UNAVAILABLE -> "hw_unavailable"
            else -> "unavailable"
        }
        return JSONObject()
            .put("available", available)
            .put("enrolled", enrolled)
            .put("reason", reason)
            .toString()
    }

    fun authenticateBiometric(requestId: String, title: String, subtitle: String) {
        val status = BiometricManager.from(this).canAuthenticate(biometricAuthenticators)
        if (status != BiometricManager.BIOMETRIC_SUCCESS) {
            val msg = if (status == BiometricManager.BIOMETRIC_ERROR_NONE_ENROLLED) {
                "No fingerprint or face unlock is set up in Android Settings yet."
            } else {
                "Biometric hardware is unavailable on this device."
            }
            emitBiometricResult(requestId, ok = false, cancelled = false, error = msg)
            return
        }
        val executor = ContextCompat.getMainExecutor(this)
        val prompt = BiometricPrompt(
            this,
            executor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    emitBiometricResult(requestId, ok = true, cancelled = false, error = "")
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    val cancelled = errorCode == BiometricPrompt.ERROR_USER_CANCELED ||
                        errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON ||
                        errorCode == BiometricPrompt.ERROR_CANCELED
                    emitBiometricResult(requestId, ok = false, cancelled = cancelled, error = errString.toString())
                }
            },
        )
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle(title.ifBlank { getString(R.string.biometric_prompt_title) })
            .setSubtitle(subtitle.ifBlank { getString(R.string.biometric_prompt_subtitle) })
            .setAllowedAuthenticators(biometricAuthenticators)
            .setNegativeButtonText(getString(android.R.string.cancel))
            .build()
        prompt.authenticate(info)
    }

    private fun emitBiometricResult(requestId: String, ok: Boolean, cancelled: Boolean, error: String) {
        if (!::ui.isInitialized) return
        val detail = JSONObject()
            .put("requestId", requestId)
            .put("ok", ok)
            .put("cancelled", cancelled)
            .put("error", error)
            .toString()
        val js = "window.dispatchEvent(new CustomEvent('nanomuse:native-biometric', {detail: $detail}))"
        ui.web.evaluateJavascript(js, null)
    }

    /**
     * From the JS bridge: pin this workspace file (an app the agent built) to the home
     * screen. Android shows a confirm dialog; the shortcut opens the file alone, fullscreen.
     */
    fun pinAppShortcut(name: String, path: String) {
        if (path.isEmpty()) return
        if (android.os.Build.VERSION.SDK_INT < 26) return
        try {
            val sm = getSystemService(android.content.pm.ShortcutManager::class.java) ?: return
            if (!sm.isRequestPinShortcutSupported) return
            val intent = Intent(this, MainActivity::class.java)
                .setAction(Intent.ACTION_VIEW)
                .putExtra(EXTRA_SHORTCUT_PATH, path)
            val shortcut = android.content.pm.ShortcutInfo.Builder(this, "app:" + path)
                .setShortLabel(name.take(24))
                .setLongLabel(name)
                .setIcon(android.graphics.drawable.Icon.createWithResource(this, R.mipmap.ic_launcher))
                .setIntent(intent)
                .build()
            sm.requestPinShortcut(shortcut, null)
        } catch (_: Exception) {
        }
    }

    private fun goConnect() {
        startActivity(Intent(this, ConnectActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }

    internal fun emitAppUpdateJson(jsonStr: String) {
        val js = "window.dispatchEvent(new CustomEvent('nanomuse:app-update', {detail: $jsonStr}))"
        runOnUiThread { if (::ui.isInitialized) ui.web.evaluateJavascript(js, null) }
    }

    internal fun notifyAppUpdateAvailable(available: AppUpdate.Available?) {
        if (available == null) return
        emitAppUpdateJson(AppUpdate.statusJson(this))
    }

    fun permissionsStatusJson(): String {
        val notifOk = Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
        val installOk = AppUpdate.canInstallPackages(this)
        val cameraOk = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val locOk = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        return JSONObject()
            .put("notifications", notifOk)
            .put("installPackages", installOk)
            .put("camera", cameraOk)
            .put("location", locOk)
            .toString()
    }

    fun requestAllPermissions() {
        val needed = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            needed.add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.CAMERA)
        }
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            needed.add(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        if (needed.isNotEmpty()) {
            askMultiplePermissions.launch(needed.toTypedArray())
        }
        if (!AppUpdate.canInstallPackages(this)) {
            AppUpdate.requestInstallPackagesPermission(this)
        }
        emitPermissionsChanged()
    }

    private fun emitPermissionsChanged() {
        if (!::ui.isInitialized) return
        val js = "window.dispatchEvent(new CustomEvent('nanomuse:permissions-changed', {detail: ${permissionsStatusJson()}}))"
        runOnUiThread { ui.web.evaluateJavascript(js, null) }
    }

    fun deviceGoogleEmailsJson(): String {
        val out = JSONArray()
        val seen = LinkedHashSet<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED) {
            runCatching {
                contentResolver.query(
                    CalendarContract.Calendars.CONTENT_URI,
                    arrayOf(CalendarContract.Calendars.ACCOUNT_NAME, CalendarContract.Calendars.ACCOUNT_TYPE),
                    null,
                    null,
                    "${CalendarContract.Calendars.IS_PRIMARY} DESC",
                )?.use { c ->
                    while (c.moveToNext()) {
                        val acct = (c.getString(0) ?: "").trim()
                        val type = (c.getString(1) ?: "").trim()
                        if (acct.contains("@") && (type == "com.google" || acct.endsWith("@gmail.com", ignoreCase = true) || seen.isEmpty())) {
                            seen.add(acct)
                        }
                    }
                }
            }
        }
        for (email in seen) out.put(email)
        return out.toString()
    }

    fun syncPhoneCalendarAndContacts() {
        val calOk = ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED
        val conOk = ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED
        if (!calOk || !conOk) {
            val need = mutableListOf<String>()
            if (!calOk) need.add(Manifest.permission.READ_CALENDAR)
            if (!conOk) need.add(Manifest.permission.READ_CONTACTS)
            askPhoneSyncPermissions.launch(need.toTypedArray())
            return
        }
        extractAndEmitPhoneSync()
    }

    private fun escapeIcsText(value: String): String =
        value.replace("\\", "\\\\")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
            .replace(",", "\\,")
            .replace(";", "\\;")

    private fun extractAndEmitPhoneSync() {
        Thread {
            val calOk = ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALENDAR) == PackageManager.PERMISSION_GRANTED
            val conOk = ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED
            if (!calOk && !conOk) {
                val detail = JSONObject()
                    .put("ok", false)
                    .put("error", "Allow Calendar or Contacts permission on Android to sync automatically.")
                    .toString()
                runOnUiThread {
                    if (::ui.isInitialized) {
                        ui.web.evaluateJavascript("window.dispatchEvent(new CustomEvent('nanomuse:phone-sync-ready', {detail: $detail}))", null)
                    }
                }
                return@Thread
            }

            val emailsArr = JSONArray(deviceGoogleEmailsJson())
            var icsText = ""
            var eventsCount = 0
            if (calOk) {
                runCatching {
                    val nowMs = System.currentTimeMillis()
                    val fromMs = nowMs - 7L * 86_400_000L
                    val toMs = nowMs + 90L * 86_400_000L
                    val utcFmt = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss'Z'").withZone(ZoneOffset.UTC)
                    val stamp = utcFmt.format(Instant.now())
                    val sb = StringBuilder()
                    sb.append("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//MiniMuse//Phone Calendar Sync//EN\r\nCALSCALE:GREGORIAN\r\nX-WR-CALNAME:Phone & Google Calendar\r\n")
                    val uri = CalendarContract.Instances.CONTENT_URI.buildUpon()
                        .appendPath(fromMs.toString())
                        .appendPath(toMs.toString())
                        .build()
                    val cols = arrayOf(
                        CalendarContract.Instances.EVENT_ID,
                        CalendarContract.Instances.TITLE,
                        CalendarContract.Instances.BEGIN,
                        CalendarContract.Instances.END,
                        CalendarContract.Instances.ALL_DAY,
                        CalendarContract.Instances.EVENT_LOCATION,
                        CalendarContract.Instances.DESCRIPTION,
                    )
                    contentResolver.query(uri, cols, null, null, "${CalendarContract.Instances.BEGIN} ASC")?.use { c ->
                        while (c.moveToNext() && eventsCount < 400) {
                            val id = c.getLong(0)
                            val title = (c.getString(1) ?: "Untitled event").trim()
                            val begin = c.getLong(2)
                            val end = c.getLong(3)
                            val allDay = c.getInt(4) == 1
                            val loc = (c.getString(5) ?: "").trim()
                            val desc = (c.getString(6) ?: "").trim()
                            sb.append("BEGIN:VEVENT\r\n")
                            sb.append("UID:phone-$id-${begin}@minimuse.android\r\n")
                            sb.append("DTSTAMP:$stamp\r\n")
                            if (allDay) {
                                val dayFmt = DateTimeFormatter.ofPattern("yyyyMMdd").withZone(ZoneOffset.UTC)
                                sb.append("DTSTART;VALUE=DATE:${dayFmt.format(Instant.ofEpochMilli(begin))}\r\n")
                                sb.append("DTEND;VALUE=DATE:${dayFmt.format(Instant.ofEpochMilli(maxOf(begin + 86_400_000L, end)))}\r\n")
                            } else {
                                sb.append("DTSTART:${utcFmt.format(Instant.ofEpochMilli(begin))}\r\n")
                                sb.append("DTEND:${utcFmt.format(Instant.ofEpochMilli(maxOf(begin + 3_600_000L, end)))}\r\n")
                            }
                            sb.append("SUMMARY:${escapeIcsText(title)}\r\n")
                            if (loc.isNotEmpty()) sb.append("LOCATION:${escapeIcsText(loc.take(300))}\r\n")
                            if (desc.isNotEmpty()) sb.append("DESCRIPTION:${escapeIcsText(desc.take(1200))}\r\n")
                            sb.append("END:VEVENT\r\n")
                            eventsCount++
                        }
                    }
                    sb.append("END:VCALENDAR\r\n")
                    icsText = sb.toString()
                }
            }

            var vcfText = ""
            var contactsCount = 0
            if (conOk) {
                runCatching {
                    val names = LinkedHashMap<Long, String>()
                    contentResolver.query(
                        ContactsContract.Contacts.CONTENT_URI,
                        arrayOf(ContactsContract.Contacts._ID, ContactsContract.Contacts.DISPLAY_NAME_PRIMARY),
                        null,
                        null,
                        "${ContactsContract.Contacts.DISPLAY_NAME_PRIMARY} ASC",
                    )?.use { c ->
                        while (c.moveToNext() && names.size < 600) {
                            val id = c.getLong(0)
                            val dn = (c.getString(1) ?: "").trim()
                            if (dn.isNotEmpty()) names[id] = dn
                        }
                    }
                    val phonesById = HashMap<Long, MutableList<String>>()
                    contentResolver.query(
                        ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                        arrayOf(ContactsContract.CommonDataKinds.Phone.CONTACT_ID, ContactsContract.CommonDataKinds.Phone.NUMBER),
                        null,
                        null,
                        null,
                    )?.use { c ->
                        while (c.moveToNext()) {
                            val cid = c.getLong(0)
                            val num = (c.getString(1) ?: "").trim()
                            if (num.isNotEmpty()) phonesById.getOrPut(cid) { mutableListOf() }.add(num)
                        }
                    }
                    val emailsById = HashMap<Long, MutableList<String>>()
                    contentResolver.query(
                        ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                        arrayOf(ContactsContract.CommonDataKinds.Email.CONTACT_ID, ContactsContract.CommonDataKinds.Email.ADDRESS),
                        null,
                        null,
                        null,
                    )?.use { c ->
                        while (c.moveToNext()) {
                            val cid = c.getLong(0)
                            val addr = (c.getString(1) ?: "").trim()
                            if (addr.isNotEmpty()) emailsById.getOrPut(cid) { mutableListOf() }.add(addr)
                        }
                    }
                    val addrsById = HashMap<Long, MutableList<String>>()
                    contentResolver.query(
                        ContactsContract.CommonDataKinds.StructuredPostal.CONTENT_URI,
                        arrayOf(
                            ContactsContract.CommonDataKinds.StructuredPostal.CONTACT_ID,
                            ContactsContract.CommonDataKinds.StructuredPostal.FORMATTED_ADDRESS,
                        ),
                        null,
                        null,
                        null,
                    )?.use { c ->
                        while (c.moveToNext()) {
                            val cid = c.getLong(0)
                            val adr = (c.getString(1) ?: "").trim()
                            if (adr.isNotEmpty()) addrsById.getOrPut(cid) { mutableListOf() }.add(adr)
                        }
                    }

                    val vcf = StringBuilder()
                    for ((cid, dn) in names) {
                        vcf.append("BEGIN:VCARD\r\nVERSION:3.0\r\n")
                        vcf.append("FN:${escapeIcsText(dn)}\r\n")
                        for (em in emailsById[cid].orEmpty().distinct()) {
                            vcf.append("EMAIL;TYPE=INTERNET:$em\r\n")
                        }
                        for (ph in phonesById[cid].orEmpty().distinct()) {
                            vcf.append("TEL;TYPE=CELL:$ph\r\n")
                        }
                        for (adr in addrsById[cid].orEmpty().distinct()) {
                            vcf.append("ADR;TYPE=HOME:;;${escapeIcsText(adr)};;;;\r\n")
                        }
                        vcf.append("END:VCARD\r\n")
                        contactsCount++
                    }
                    vcfText = vcf.toString()
                }
            }

            val detail = JSONObject()
                .put("ok", true)
                .put("calendar_name", "Phone & Google Calendar")
                .put("ics", icsText)
                .put("events", eventsCount)
                .put("contacts_name", "Phone & Google Contacts")
                .put("vcf", vcfText)
                .put("contacts", contactsCount)
                .put("emails", emailsArr)
                .toString()
            runOnUiThread {
                if (::ui.isInitialized) {
                    ui.web.evaluateJavascript("window.dispatchEvent(new CustomEvent('nanomuse:phone-sync-ready', {detail: $detail}))", null)
                }
            }
        }.start()
    }

    override fun onResume() {
        super.onResume()
        AppUpdate.activeActivity = java.lang.ref.WeakReference(this)
        AppUpdate.retryPendingInstall(this)
        emitPermissionsChanged()
        Thread { AppUpdate.checkAndAutoPrepare(this) { available -> notifyAppUpdateAvailable(available) } }.start()
        if (::ui.isInitialized) {
            ui.web.onResume()
            if (prefs.isLocal && ui.offline.visibility == View.VISIBLE && RuntimeService.state != RuntimeService.State.FAILED) {
                load(pendingThread)
            }
        }
    }

    override fun onPause() {
        if (AppUpdate.activeActivity?.get() === this) {
            AppUpdate.activeActivity = null
        }
        if (::ui.isInitialized) ui.web.onPause()
        super.onPause()
    }

    override fun onDestroy() {
        RuntimeService.listeners.remove(runtimeListener)
        takeOver?.dismiss()
        if (::ui.isInitialized) ui.web.destroy()
        super.onDestroy()
    }

    companion object {
        const val EXTRA_THREAD = "thread"

        /** A home-screen shortcut's workspace file: the app opens it alone, fullscreen. */
        const val EXTRA_SHORTCUT_PATH = "io.github.nanomuse.shortcut_path"
        const val EXTRA_INSTALL_APK_URI = "io.github.nanomuse.install_apk_uri"
        const val EXTRA_INSTALL_UPDATE_VER = "io.github.nanomuse.install_update_ver"
        const val EXTRA_INSTALL_UPDATE_URL = "io.github.nanomuse.install_update_url"
    }
}
