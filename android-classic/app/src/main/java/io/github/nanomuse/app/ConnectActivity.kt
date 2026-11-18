package io.github.nanomuse.app

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.EditorInfo
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import android.widget.Toast
import io.github.nanomuse.app.databinding.ActivityConnectBinding
import io.github.nanomuse.app.runtime.LocalRuntime
import io.github.nanomuse.app.runtime.RuntimeService
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import org.json.JSONObject
import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * First screen. The build that carries a root file system asks where nanoMuse should live:
 * on this phone (unpack, then start the runtime) or on the user's computer. The connect-only
 * build goes straight to the second: point the phone at the QR code `nanomuse serve` prints,
 * or paste the link. The link is checked against the server (`GET /api/state`) before it is
 * kept, so a typo or a stale token is caught here and not as a blank page later.
 */
class ConnectActivity : AppCompatActivity() {
    private lateinit var ui: ActivityConnectBinding
    private lateinit var prefs: Prefs
    private lateinit var runtime: LocalRuntime
    private var runtimeListener: ((RuntimeService.State, String?) -> Unit)? = null
    private var inRemoteMode = false
    private val http = OkHttpClient.Builder().connectTimeout(6, TimeUnit.SECONDS).readTimeout(6, TimeUnit.SECONDS).build()
    private val backToModesCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            setupModes()
        }
    }

    private val scanner = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        if (result.resultCode != Activity.RESULT_OK) return@registerForActivityResult
        val text = result.data?.getStringExtra(QrScanActivity.EXTRA_QR_RESULT)?.trim() ?: return@registerForActivityResult
        // a pairing code QR is not a server address: it goes to the pairing claim, not `connect`
        if (Prefs.isPairLink(text)) {
            pairFromQr(text)
            return@registerForActivityResult
        }
        showRemote()
        ui.url.setText(text)
        connect(text)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        runtime = LocalRuntime(this)
        if (prefs.connected && (!prefs.isLocal || runtime.installed)) {
            openMain()
            return
        }
        ui = ActivityConnectBinding.inflate(layoutInflater)
        setContentView(ui.root)
        onBackPressedDispatcher.addCallback(this, backToModesCallback)
        if (runtime.available) {
            setupModes()
        }
        ViewCompat.setOnApplyWindowInsetsListener(ui.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        ui.scan.setOnClickListener {
            scanner.launch(Intent(this, QrScanActivity::class.java))
        }
        ui.connect.setOnClickListener { connect(ui.url.text?.toString() ?: "") }
        ui.backToModes.setOnClickListener { setupModes() }
        ui.url.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO) {
                connect(ui.url.text?.toString() ?: "")
                true
            } else false
        }
        // a link shared from another app (or a deep link) lands here too
        val shared = intent?.dataString
        if (!shared.isNullOrBlank()) {
            ui.url.setText(shared)
            showRemote()
        } else if (savedInstanceState?.getBoolean(STATE_IN_REMOTE, false) == true) {
            showRemote()
        } else if (prefs.isLocal && prefs.connected && !runtime.installed && runtime.available) {
            // Returning user after an APK self-update: unpack the updated capsule cleanly while keeping data
            runLocal()
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_IN_REMOTE, inRemoteMode)
    }

    override fun onDestroy() {
        runtimeListener?.let { RuntimeService.listeners.remove(it) }
        runtimeListener = null
        super.onDestroy()
    }

    // ------------------------------------------------------------------ on this phone

    private fun setupModes() {
        inRemoteMode = false
        ui.title.text = getString(R.string.mode_title)
        ui.body.text = getString(R.string.mode_body)
        ui.modes.visibility = View.VISIBLE
        ui.install.visibility = View.GONE
        ui.remote.visibility = View.GONE
        ui.error.visibility = View.GONE
        backToModesCallback.isEnabled = false
        val mb = ((runtime.bundled?.unpacked ?: 0L) / (1024 * 1024)).toInt().coerceAtLeast(1)
        ui.localHint.text = getString(R.string.mode_local_hint, mb)
        ui.runLocal.setOnClickListener { runLocal() }
        ui.useRemote.setOnClickListener { showRemote() }
    }

    private fun showRemote() {
        inRemoteMode = true
        ui.title.text = getString(R.string.connect_title)
        ui.body.text = getText(R.string.connect_body)
        ui.modes.visibility = View.GONE
        ui.install.visibility = View.GONE
        ui.remote.visibility = View.VISIBLE
        ui.backToModes.visibility = if (runtime.available) View.VISIBLE else View.GONE
        backToModesCallback.isEnabled = runtime.available
    }

    /** Unpack (once), start the service, wait for the first health check, open the app. */
    private fun runLocal() {
        inRemoteMode = false
        backToModesCallback.isEnabled = false
        ui.modes.visibility = View.GONE
        ui.error.visibility = View.GONE
        ui.install.visibility = View.VISIBLE
        ui.installProgress.isIndeterminate = false
        ui.installProgress.progress = 0
        ui.installText.text = getString(R.string.install_unpacking, 0)
        thread(name = "rootfs-install") {
            try {
                if (!runtime.installed) {
                    var lastPercent = -1
                    runtime.install { done, total, _ ->
                        val percent = if (total > 0) (done * 100 / total).toInt().coerceIn(0, 100) else 0
                        if (percent != lastPercent) {
                            lastPercent = percent
                            runOnUiThread {
                                ui.installProgress.progress = percent * 10
                                ui.installText.text = getString(R.string.install_unpacking, percent)
                            }
                        }
                    }
                }
                runOnUiThread { startLocal() }
            } catch (e: Exception) {
                runOnUiThread {
                    ui.install.visibility = View.GONE
                    if (runtime.available) setupModes()
                    fail(getString(R.string.install_failed, e.message ?: e.javaClass.simpleName))
                }
            }
        }
    }

    private fun startLocal() {
        ui.installProgress.isIndeterminate = true
        ui.installText.text = getString(R.string.install_starting)
        prefs.mode = Prefs.MODE_LOCAL
        if (RuntimeService.state == RuntimeService.State.RUNNING) {
            openMain()
            return
        }
        val listener: (RuntimeService.State, String?) -> Unit = { state, detail ->
            runOnUiThread {
                when (state) {
                    RuntimeService.State.RUNNING -> {
                        runtimeListener?.let { RuntimeService.listeners.remove(it) }
                        runtimeListener = null
                        openMain()
                    }
                    RuntimeService.State.FAILED -> {
                        runtimeListener?.let { RuntimeService.listeners.remove(it) }
                        runtimeListener = null
                        ui.install.visibility = View.GONE
                        if (runtime.available) setupModes()
                        fail(detail ?: RuntimeService.lastError ?: getString(R.string.runtime_no_answer))
                    }
                    else -> Unit
                }
            }
        }
        runtimeListener?.let { RuntimeService.listeners.remove(it) }
        runtimeListener = listener
        RuntimeService.listeners.add(listener)
        RuntimeService.start(this)
    }

    companion object {
        private const val STATE_IN_REMOTE = "in_remote_mode"
    }

    // ------------------------------------------------------------------ a pairing code QR

    /**
     * A pairing code QR from the other device (Settings, Devices, "Show pairing code"). This
     * screen has no server to claim with, so with a runtime on the phone the scan starts the
     * local runtime and parks the payload ([Prefs.pendingPair]) for the page to claim once it
     * loads; without a runtime there is nothing to pair from yet, so say what the QR is
     * instead of "not a server address".
     */
    private fun pairFromQr(payload: String) {
        if (runtime.available) {
            prefs.pendingPair = payload
            Toast.makeText(this, R.string.connect_pair_starting, Toast.LENGTH_LONG).show()
            runLocal()
        } else {
            fail(getString(R.string.connect_pair_need_app))
        }
    }

    // ------------------------------------------------------------------ on the computer

    private fun connect(text: String) {
        // pasted or shared pairing code: same routing as a scan (see pairFromQr)
        if (Prefs.isPairLink(text)) {
            pairFromQr(text)
            return
        }
        val parsed = Prefs.parseLink(text)
        if (parsed == null) return fail(getString(R.string.connect_bad_url))
        val (origin, token) = parsed
        if (token.isEmpty()) return fail(getString(R.string.connect_no_token))
        busy(true)
        val req = Request.Builder().url("$origin/api/state").header("Authorization", "Bearer $token").build()
        http.newCall(req).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                runOnUiThread { fail(getString(R.string.connect_unreachable, origin)) }
            }

            override fun onResponse(call: Call, response: Response) {
                val body = response.body?.string() ?: ""
                response.close()
                runOnUiThread {
                    when {
                        response.code == 401 || response.code == 403 -> fail(getString(R.string.connect_unauthorized))
                        !response.isSuccessful -> fail(getString(R.string.connect_unreachable, origin))
                        else -> {
                            prefs.mode = Prefs.MODE_REMOTE
                            prefs.serverUrl = origin
                            prefs.token = token
                            prefs.agentName = runCatching { JSONObject(body).getJSONObject("profile").optString("name") }.getOrDefault("")
                            NotifyService.sync(this@ConnectActivity)
                            openMain()
                        }
                    }
                }
            }
        })
    }

    private fun busy(on: Boolean) {
        ui.progress.visibility = if (on) View.VISIBLE else View.GONE
        ui.error.visibility = View.GONE
        ui.connect.isEnabled = !on
        ui.scan.isEnabled = !on
        ui.connect.text = getString(if (on) R.string.connect_checking else R.string.connect_button)
    }

    private fun fail(message: String) {
        busy(false)
        ui.error.text = message
        ui.error.visibility = View.VISIBLE
    }

    private fun openMain() {
        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
        finish()
    }
}
