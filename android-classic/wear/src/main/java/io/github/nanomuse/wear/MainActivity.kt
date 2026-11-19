package io.github.nanomuse.wear

import android.Manifest
import android.animation.AnimatorSet
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.view.HapticFeedbackConstants
import android.view.InputDevice
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.provider.Settings
import android.text.Editable
import android.text.InputType
import android.text.TextUtils
import android.text.TextWatcher
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.view.animation.AccelerateDecelerateInterpolator
import android.view.animation.LinearInterpolator
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.VideoView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.WebSocket
import org.json.JSONObject
import java.io.File
import java.util.Base64
import java.util.Locale
import kotlin.math.roundToInt

class MainActivity : AppCompatActivity() {

    private lateinit var config: WatchConfig
    private lateinit var client: WatchClient

    private val mainHandler = Handler(Looper.getMainLooper())
    private var rootFrame: FrameLayout? = null
    private var backdropLayer: FrameLayout? = null
    private var rootScroll: ScrollView? = null
    private var rootCol: LinearLayout? = null
    private var bezelScrollbar: BezelScrollbarView? = null
    private var volumeArcView: VolumeArcView? = null
    private var homeChromeOverlay: View? = null
    private var heroAvatarContainer: View? = null
    private var chromeVisible = true
    private var chromeHideRunnable: Runnable? = null
    private var volumeHideRunnable: Runnable? = null

    // Horizontal swipe pages: Swipe L->R for Chats/Side Menu, Home in center, R->L for MiniMuse Apps -> Settings
    private enum class WatchPage { CHATS, HOME, APPS, SETTINGS }
    private var activePage = WatchPage.HOME
    private var selectedThreadId = "main"
    private var activeMiniApp: WatchMiniApp? = null

    // Touch swipe tracking (works alongside ScrollView and rotating bezel)
    private var touchDownX = 0f
    private var touchDownY = 0f
    private var touchDownTime = 0L
    private var swipeConsumed = false

    private var currentState: WatchState? = null
    private var discoveredServers: List<DiscoveredServer> = emptyList()
    private var scanningLan = false
    private var statusNotice = ""

    // Muse-style chat screen (the hero's chat bubble): recent turns + a typed prompt
    private var chatOpen = false
    private var chatInputOpen = false
    private var chatDraft = ""
    private var chatField: EditText? = null
    private var keyboardWarned = false

    // The agent's face worn full-bleed on the home screen: the studio still from the server,
    // the bundled dragon, or the emoji on its colour.
    private var avatarBitmap: Bitmap? = null
    private var avatarLoadedKey = ""
    private var avatarFailedKey = ""
    private var avatarLoadJob: Job? = null

    // The animated face: the same mp4 the app plays, downloaded once and looped on the hero.
    // Kept across renders (the screen rebuilds every few seconds) so it never restarts.
    private var heroVideo: android.widget.VideoView? = null
    private var heroVideoId = ""
    private var heroVideoMood = ""
    private var heroVideoFailed = mutableSetOf<String>()
    private val heroClipRetryAt = mutableMapOf<String, Long>()
    private var heroClipJob: Job? = null
    private var heroBreathe: AnimatorSet? = null

    // Why the last state fetch failed, so the home screen can say it instead of "Connecting..."
    private var lastStateError = ""
    private var stateFailures = 0
    private var healAttemptAtMs = 0L
    private var renderedFingerprint = ""

    private var pollJob: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null

    // Self-updating shell & dynamic WebView app shell state
    private var webShellOpen = false
    private var optionsExpanded = false
    private var haloAnim: AnimatorSet? = null
    private var availableWatchUpdate: WatchUpdateInfo? = null
    private var checkingWatchUpdate = false
    private var updatingWatchApk = false
    private var watchUpdateProgress = 0
    private var pendingInstallApkFile: File? = null
    private var lastAutoCheckMs = 0L
    private var autoStartedUpdateVersion = ""

    // Voice Ask / SpeechRecognizer state
    private var speechRecognizer: SpeechRecognizer? = null
    private var voiceAskActive = false
    private var voiceDraft = ""
    private var voiceAutoSendRunnable: Runnable? = null
    private var voiceHoldUntilMs = 0L

    // Live Call state
    private enum class CallPhase { OFF, CONNECTING, LISTENING, THINKING, SPEAKING }

    private var callPhase = CallPhase.OFF
    private var callMode = "universal" // "realtime" or "universal"
    private var callModelLabel = ""
    private var callSocket: WebSocket? = null
    private var callMuted = false
    private var callSpeakerOff = false
    private var callTranscript = mutableListOf<String>()
    private var callStreamingReply = ""

    // Native Realtime PCM16 audio
    private var audioRecord: AudioRecord? = null
    private var audioRecordJob: Job? = null
    private var audioTrack: AudioTrack? = null
    private var tts: TextToSpeech? = null
    private var ttsReady = false

    private var pendingMicAction: (() -> Unit)? = null
    private val micPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            val act = pendingMicAction
            pendingMicAction = null
            if (granted) {
                act?.invoke()
            } else {
                statusNotice = "Mic permission required for voice"
                render()
            }
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        config = WatchConfig(this)
        client = WatchClient(config)

        tts = TextToSpeech(this) { status ->
            ttsReady = status == TextToSpeech.SUCCESS
            if (ttsReady) {
                tts?.language = Locale.getDefault()
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {}
                    override fun onDone(utteranceId: String?) {
                        mainHandler.post { onTtsFinished() }
                    }

                    @Deprecated("Deprecated in Java")
                    override fun onError(utteranceId: String?) {
                        mainHandler.post { onTtsFinished() }
                    }
                })
            }
        }

        val frame = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
        }
        val backdrop = FrameLayout(this).apply {
            setBackgroundColor(Color.BLACK)
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(Color.TRANSPARENT)
            isFillViewport = true
            isVerticalScrollBarEnabled = false
            // the home hero wears the whole round screen: children may paint over the padding
            clipToPadding = false
            clipChildren = false
        }
        val col = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            val w = resources.displayMetrics.widthPixels
            val round = resources.configuration.isScreenRound
            val padH = if (round) (w * 0.11f).roundToInt() else dp(12)
            val padV = if (round) (w * 0.14f).roundToInt() else dp(16)
            setPadding(padH, padV, padH, padV + dp(20))
            clipToPadding = false
            clipChildren = false
        }
        scroll.addView(
            col,
            ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ),
        )
        val scrollbar = BezelScrollbarView(this).apply { visibility = View.GONE }
        val volArc = VolumeArcView(this).apply { visibility = View.GONE }
        scroll.setOnScrollChangeListener { _, _, scrollY, _, _ ->
            // 1. Update right-edge curved bezel scrollbar
            val contentH = col.height
            val viewH = scroll.height
            val range = (contentH - viewH).coerceAtLeast(0)
            if (range > dp(16) && (chatOpen || activePage != WatchPage.HOME)) {
                val frac = (scrollY.toFloat() / range.toFloat()).coerceIn(0f, 1f)
                val thumb = (viewH.toFloat() / contentH.toFloat()).coerceIn(0.16f, 0.55f)
                scrollbar.updateScroll(frac, thumb)
                scrollbar.visibility = View.VISIBLE
            } else {
                scrollbar.visibility = View.GONE
            }
            // 2. On Home, scrolling up smoothly shrinks the AI character (as requested)
            if (activePage == WatchPage.HOME && !chatOpen && callPhase == CallPhase.OFF) {
                heroAvatarContainer?.let { av ->
                    val maxShrink = (resources.displayMetrics.widthPixels * 0.48f).coerceAtLeast(1f)
                    val t = (scrollY.toFloat() / maxShrink).coerceIn(0f, 1f)
                    val scale = 1f - 0.42f * t
                    av.scaleX = scale
                    av.scaleY = scale
                    av.alpha = 1f - 0.35f * t
                }
            }
        }
        frame.addView(backdrop, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        frame.addView(scroll, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        frame.addView(scrollbar, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        frame.addView(volArc, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        rootFrame = frame
        backdropLayer = backdrop
        rootScroll = scroll
        rootCol = col
        bezelScrollbar = scrollbar
        volumeArcView = volArc
        setContentView(frame)

        handleAutoPairIntent(intent)
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
        render()
        if (config.isPaired) {
            refreshState()
        } else {
            startLanScan()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        if (handleAutoPairIntent(intent)) {
            render()
            refreshState()
            startPolling()
        }
    }

    private fun handleAutoPairIntent(intent: Intent?): Boolean {
        if (intent == null) return false
        val serverUrl = intent.getStringExtra("server_url")?.trim().orEmpty()
        val token = intent.getStringExtra("token")?.trim().orEmpty()
        val agentName = intent.getStringExtra("agent_name")?.trim().orEmpty()
        val pairCode = intent.getStringExtra("pair_code")?.trim().orEmpty()
        if (serverUrl.isNotEmpty() && token.isNotEmpty()) {
            config.serverUrl = serverUrl
            config.token = token
            if (agentName.isNotEmpty()) {
                config.agentName = agentName
            }
            flash("Auto-paired with ${config.agentName}!")
            return true
        }
        if (serverUrl.isNotEmpty() && pairCode.isNotEmpty()) {
            config.serverUrl = serverUrl
            lifecycleScope.launch {
                val res = client.claimCode(serverUrl, pairCode)
                res.onSuccess {
                    statusNotice = "Paired with ${config.agentName}!"
                    render()
                    refreshState()
                    startPolling()
                }.onFailure { err ->
                    statusNotice = err.message ?: "Pair failed"
                    render()
                }
            }
            return true
        }
        return false
    }

    override fun onResume() {
        super.onResume()
        updateWakeLocks()
        startPolling()
        val pendingFile = pendingInstallApkFile
        if (pendingFile != null && pendingFile.isFile && packageManager.canRequestPackageInstalls()) {
            pendingInstallApkFile = null
            launchWearApkInstaller(pendingFile)
        } else {
            maybeAutoCheckWatchUpdate()
        }
    }

    private fun maybeAutoCheckWatchUpdate(force: Boolean = false) {
        if (checkingWatchUpdate || updatingWatchApk) return
        val now = System.currentTimeMillis()
        if (!force && now - lastAutoCheckMs < 4 * 3600_000L) return
        lastAutoCheckMs = now
        checkingWatchUpdate = true
        if (force) {
            statusNotice = "Checking for updates..."
            render()
        }
        lifecycleScope.launch {
            try {
                val res = client.checkWearUpdate(BuildConfig.VERSION_NAME, BuildConfig.SHELL_VERSION)
                res.onSuccess { info ->
                    availableWatchUpdate = info
                    if (info == null) {
                        if (force) {
                            statusNotice = "Up to date (v${BuildConfig.VERSION_NAME})"
                            render()
                        }
                    } else if (force || (info.shellRequired && autoStartedUpdateVersion != info.version)) {
                        if (info.shellRequired) {
                            autoStartedUpdateVersion = info.version
                            startSingleFlightWatchUpdate(info)
                        } else {
                            statusNotice = "Watch v${info.version} available"
                            render()
                        }
                    }
                }.onFailure { err ->
                    if (force) {
                        statusNotice = err.message ?: "Update check failed"
                        render()
                    }
                }
            } finally {
                checkingWatchUpdate = false
            }
        }
    }

    private fun startSingleFlightWatchUpdate(info: WatchUpdateInfo) {
        if (updatingWatchApk) {
            statusNotice = "Downloading v${info.version} (${watchUpdateProgress}%)..."
            render()
            return
        }
        updatingWatchApk = true
        watchUpdateProgress = 0
        statusNotice = "Downloading v${info.version}..."
        render()
        val dest = File(cacheDir, "updates/MiniMuse-${info.version}-wear.apk")
        lifecycleScope.launch {
            try {
                val dl = client.downloadApk(info, dest) { pct ->
                    mainHandler.post {
                        watchUpdateProgress = pct
                        statusNotice = "Downloading v${info.version}: $pct%"
                        render()
                    }
                }
                dl.onSuccess { file ->
                    statusNotice = "Installing v${info.version}..."
                    render()
                    launchWearApkInstaller(file)
                }.onFailure { err ->
                    statusNotice = err.message ?: "Download failed"
                    render()
                }
            } finally {
                updatingWatchApk = false
                render()
            }
        }
    }

    private fun launchWearApkInstaller(apkFile: File) {
        if (!apkFile.isFile) return
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O && !packageManager.canRequestPackageInstalls()) {
            pendingInstallApkFile = apkFile
            statusNotice = "Allow unknown app install to finish update"
            render()
            val settingsIntent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:$packageName"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { startActivity(settingsIntent) }
            return
        }
        val uri = FileProvider.getUriForFile(this, "${packageName}.fileprovider", apkFile)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { startActivity(intent) }.onFailure {
            statusNotice = "Could not open watch installer"
            render()
        }
    }

    override fun onPause() {
        super.onPause()
        stopHaloAnimation()
        pollJob?.cancel()
        pollJob = null
        if (callPhase == CallPhase.OFF && !voiceAskActive) {
            releaseWakeLocks()
        }
    }

    override fun onDestroy() {
        stopHaloAnimation()
        endLiveCall()
        cancelVoiceAsk()
        releaseWakeLocks()
        try { tts?.stop(); tts?.shutdown() } catch (_: Exception) {}
        super.onDestroy()
    }

    // ---- Wake Lock & Keep Screen On (prevents watch screen sleeping mid-prompt!) ----

    private fun shouldStayAwake(): Boolean {
        return config.keepScreenOnDuringVoice && (voiceAskActive || callPhase != CallPhase.OFF)
    }

    private fun updateWakeLocks() {
        if (shouldStayAwake()) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            if (wakeLock == null) {
                val pm = getSystemService(POWER_SERVICE) as? PowerManager
                wakeLock = pm?.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "minimuse:watch_voice")?.apply {
                    setReferenceCounted(false)
                }
            }
            try {
                if (wakeLock?.isHeld == false) {
                    wakeLock?.acquire(10 * 60 * 1000L) // 10 min safety cap
                }
            } catch (_: Exception) {
            }
        } else {
            releaseWakeLocks()
        }
    }

    private fun releaseWakeLocks() {
        window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        try {
            if (wakeLock?.isHeld == true) wakeLock?.release()
        } catch (_: Exception) {
        }
    }

    private fun startPolling() {
        pollJob?.cancel()
        if (!config.isPaired) return
        pollJob = lifecycleScope.launch {
            while (isActive) {
                refreshState(silent = true)
                delay(if (currentState?.busy == true) 2200L else 5500L)
            }
        }
    }

    private fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
    }

    /** A short-lived line: pairing and keyboard notices should not become the permanent
     *  status on the home screen. */
    private fun flash(message: String, ms: Long = 6000L) {
        statusNotice = message
        render()
        mainHandler.postDelayed({
            if (statusNotice == message) {
                statusNotice = ""
                render()
            }
        }, ms)
    }

    /** Everything the screen actually shows, so an unchanged poll can skip the rebuild. */
    private fun fingerprint(st: WatchState): String = buildString {
        append(st.agentName).append('|').append(st.busy).append('|')
        append(st.avatarId).append('|').append(st.avatarEmoji).append('|')
        append(st.threadId).append('|').append(st.threads.size).append('|').append(st.apps.size).append('|')
        append(st.approvals.joinToString { it.id + it.summary }).append('|')
        append(st.recent.joinToString { it.role + it.text + it.reactions.joinToString("") }).append('|')
        append(st.actions.joinToString { it.label + it.prompt }).append('|')
        append(st.cards.joinToString { it.title + it.body }).append('|')
        append(statusNotice).append('|').append(chatOpen).append(chatInputOpen).append('|').append(activePage.name)
    }

    private fun refreshState(silent: Boolean = false) {
        if (!config.isPaired) return
        lifecycleScope.launch {
            val res = client.fetchState(selectedThreadId)
            res.onSuccess { st ->
                currentState = st
                lastStateError = ""
                stateFailures = 0
                if (!silent) statusNotice = ""
                // A silent poll that brings nothing new must not rebuild the screen: the
                // animated hero would restart every few seconds.
                val fp = fingerprint(st)
                if (!silent || fp != renderedFingerprint) {
                    renderedFingerprint = fp
                    render()
                }
            }.onFailure { err ->
                stateFailures += 1
                lastStateError = err.message ?: err::class.java.simpleName
                // a watch that has been asleep, or a router that handed the server a new
                // address, leaves the saved URL pointing at nothing: look once more on Wi-Fi
                if (stateFailures >= 3) maybeHealAddress()
                if (!silent) {
                    statusNotice = lastStateError
                    render()
                } else if (stateFailures == 3) {
                    // the third silent miss is worth a repaint, so the reason is on screen
                    render()
                }
            }
        }
    }

    /** Find the same server at a new address and keep using it (a stale IP is the usual
     *  reason a paired watch sits on "Connecting..." forever). Also probes loopback (127.0.0.1)
     *  for Bluetooth/ADB-bridged phone links when watch Wi-Fi is off. */
    private fun maybeHealAddress() {
        val now = System.currentTimeMillis()
        if (now - healAttemptAtMs < 30_000L) return
        healAttemptAtMs = now
        lifecycleScope.launch {
            val found = LanDiscovery.scan(this@MainActivity, timeoutMs = 3000L)
            val saved = WatchConfig.normalizeUrl(config.serverUrl)
            val candidates = LinkedHashSet<String>()
            found.forEach { candidates.add(WatchConfig.normalizeUrl(it.url)) }
            candidates.add("http://127.0.0.1:8787")
            for (url in candidates) {
                if (url.isBlank() || url == saved) continue
                val previous = config.serverUrl
                config.serverUrl = url
                val res = client.fetchState(selectedThreadId)
                if (res.isSuccess) {
                    if (found.isNotEmpty()) discoveredServers = found
                    stateFailures = 0
                    lastStateError = ""
                    currentState = res.getOrNull()
                    startPolling()
                    render()
                    return@launch
                }
                config.serverUrl = previous
            }
        }
    }

    private fun startLanScan() {
        scanningLan = true
        statusNotice = "Scanning Wi-Fi for MiniMuse..."
        render()
        lifecycleScope.launch {
            val found = LanDiscovery.scan(this@MainActivity, timeoutMs = 3600L)
            discoveredServers = found
            scanningLan = false
            statusNotice = if (found.isEmpty()) "No local server found yet" else "Found ${found.size} on Wi-Fi"
            render()
        }
    }

    private fun ensureMicPermission(onGranted: () -> Unit) {
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) {
            onGranted()
        } else {
            pendingMicAction = onGranted
            micPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // ---- Voice Ask (stay-awake speech recognition with "Still talking (+10s)" hold) ----

    private fun startVoiceAsk() {
        ensureMicPermission {
            endLiveCall()
            voiceAskActive = true
            voiceDraft = ""
            voiceHoldUntilMs = 0L
            statusNotice = "Listening — take your time"
            updateWakeLocks()
            render()
            beginSpeechRecognition(isLiveCall = false)
        }
    }

    private fun extendTalkingGrace() {
        val base = maxOf(System.currentTimeMillis(), voiceHoldUntilMs)
        voiceHoldUntilMs = base + 10_000L
        cancelVoiceAutoSend()
        statusNotice = "Extended +10s — keep speaking"
        updateWakeLocks()
        // Keep Android SpeechRecognizer alive if suspended between sentences.
        if (speechRecognizer == null && (voiceAskActive || (callPhase == CallPhase.LISTENING && callMode == "universal"))) {
            beginSpeechRecognition(isLiveCall = callPhase != CallPhase.OFF)
        }
        render()
    }

    private fun finishVoiceNow() {
        cancelVoiceAutoSend()
        try { speechRecognizer?.stopListening() } catch (_: Exception) {}
        if (callPhase != CallPhase.OFF) {
            if (callMode == "realtime") {
                callSocket?.send(JSONObject().put("kind", "commit_turn").toString())
                callPhase = CallPhase.THINKING
                render()
            } else if (voiceDraft.isNotBlank()) {
                sendUniversalCallTurn(voiceDraft)
            }
        } else if (voiceAskActive) {
            val text = voiceDraft.trim()
            cancelVoiceAsk()
            if (text.isNotEmpty()) submitWatchAsk(text) else render()
        }
    }

    private fun cancelVoiceAsk() {
        cancelVoiceAutoSend()
        voiceAskActive = false
        voiceDraft = ""
        voiceHoldUntilMs = 0L
        destroySpeechRecognizer()
        updateWakeLocks()
    }

    private fun submitWatchAsk(text: String) {
        statusNotice = "Sending..."
        render()
        lifecycleScope.launch {
            val res = client.ask(text, threadId = selectedThreadId)
            res.onSuccess {
                statusNotice = ""
                refreshState(silent = false)
                delay(1400L)
                refreshState(silent = true)
            }.onFailure { err ->
                statusNotice = err.message ?: "Send failed"
                render()
            }
        }
    }

    private fun cancelVoiceAutoSend() {
        voiceAutoSendRunnable?.let { mainHandler.removeCallbacks(it) }
        voiceAutoSendRunnable = null
    }

    private fun destroySpeechRecognizer() {
        try {
            speechRecognizer?.cancel()
            speechRecognizer?.destroy()
        } catch (_: Exception) {
        }
        speechRecognizer = null
    }

    private fun beginSpeechRecognition(isLiveCall: Boolean) {
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            statusNotice = "Speech recognition unavailable"
            render()
            return
        }
        destroySpeechRecognizer()
        val sr = SpeechRecognizer.createSpeechRecognizer(this)
        speechRecognizer = sr
        val patience = maxOf(config.patienceMs, 5500L)
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, patience)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, patience)
            putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 1500L)
        }
        sr.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) {
                if (!voiceAskActive && callPhase != CallPhase.LISTENING) return
                render()
            }

            override fun onBeginningOfSpeech() {}
            override fun onRmsChanged(rmsdB: Float) {}
            override fun onBufferReceived(buffer: ByteArray?) {}
            override fun onEndOfSpeech() {}

            override fun onError(error: Int) {
                if (!voiceAskActive && callPhase != CallPhase.LISTENING) return
                // On watch, timeout/no-match often triggers briefly before user continues:
                // if user tapped "Still talking", stay awake and keep listening smoothly.
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                    if (System.currentTimeMillis() < voiceHoldUntilMs || voiceDraft.isBlank()) {
                        mainHandler.postDelayed({
                            if (voiceAskActive || (callPhase == CallPhase.LISTENING && !callMuted)) {
                                beginSpeechRecognition(isLiveCall)
                            }
                        }, 250L)
                    } else if (isLiveCall) {
                        scheduleVoiceAutoSend(isLiveCall = true, delayMs = 900L)
                    } else {
                        scheduleVoiceAutoSend(isLiveCall = false, delayMs = 900L)
                    }
                    return
                }
                statusNotice = "Mic retry ($error)"
                render()
            }

            override fun onResults(results: Bundle?) {
                val list = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val finalPiece = list?.firstOrNull()?.trim().orEmpty()
                if (finalPiece.isNotEmpty()) {
                    voiceDraft = if (voiceDraft.isBlank()) finalPiece else "$voiceDraft $finalPiece"
                }
                val now = System.currentTimeMillis()
                val remainingGrace = (voiceHoldUntilMs - now).coerceAtLeast(0L)
                val waitMs = maxOf(1400L, remainingGrace)
                if (voiceDraft.isNotBlank()) {
                    scheduleVoiceAutoSend(isLiveCall, waitMs)
                } else if (voiceAskActive || (isLiveCall && callPhase == CallPhase.LISTENING && !callMuted)) {
                    beginSpeechRecognition(isLiveCall)
                }
                render()
            }

            override fun onPartialResults(partialResults: Bundle?) {
                val list = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                val partial = list?.firstOrNull()?.trim().orEmpty()
                if (partial.isNotEmpty()) {
                    voiceDraft = partial
                    scheduleVoiceAutoSend(isLiveCall, maxOf(config.patienceMs, (voiceHoldUntilMs - System.currentTimeMillis()).coerceAtLeast(0L)))
                    render()
                }
            }

            override fun onEvent(eventType: Int, params: Bundle?) {}
        })
        try {
            sr.startListening(intent)
        } catch (e: Exception) {
            statusNotice = e.message ?: "Voice failed"
            render()
        }
    }

    private fun scheduleVoiceAutoSend(isLiveCall: Boolean, delayMs: Long) {
        cancelVoiceAutoSend()
        val r = Runnable {
            val now = System.currentTimeMillis()
            if (now < voiceHoldUntilMs) {
                scheduleVoiceAutoSend(isLiveCall, (voiceHoldUntilMs - now).coerceAtLeast(500L))
                return@Runnable
            }
            val text = voiceDraft.trim()
            if (text.isEmpty()) return@Runnable
            if (isLiveCall) {
                sendUniversalCallTurn(text)
            } else {
                cancelVoiceAsk()
                submitWatchAsk(text)
            }
        }
        voiceAutoSendRunnable = r
        mainHandler.postDelayed(r, delayMs.coerceAtLeast(600L))
    }

    // ---- Live Call Mode (/ws/call) ----

    private var volumeCarry = 0f

    /** Horizontal swipe gestures for watch navigation (L->R: Chats/Side menu, R->L: Apps -> Settings)
     *  and tap-to-wake for the auto-hiding Home buttons. */
    override fun dispatchTouchEvent(ev: MotionEvent): Boolean {
        when (ev.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                touchDownX = ev.x
                touchDownY = ev.y
                touchDownTime = System.currentTimeMillis()
                swipeConsumed = false
                if (activePage == WatchPage.HOME && !chatOpen && callPhase == CallPhase.OFF) {
                    wakeHomeChrome()
                }
            }
            MotionEvent.ACTION_UP -> {
                val dx = ev.x - touchDownX
                val dy = ev.y - touchDownY
                val dt = System.currentTimeMillis() - touchDownTime
                val minSwipe = dp(52).toFloat()
                if (!swipeConsumed && dt < 700L && kotlin.math.abs(dx) > minSwipe && kotlin.math.abs(dx) > kotlin.math.abs(dy) * 1.35f) {
                    if (handleHorizontalSwipe(rightward = dx > 0f)) {
                        swipeConsumed = true
                        return true
                    }
                }
            }
        }
        return super.dispatchTouchEvent(ev)
    }

    private fun handleHorizontalSwipe(rightward: Boolean): Boolean {
        if (!config.isPaired || voiceAskActive) return false
        if (webShellOpen || activeMiniApp != null) {
            if (rightward) {
                webShellOpen = false
                activeMiniApp = null
                buzz(Buzz.TICK)
                render()
                return true
            }
            return false
        }
        if (chatOpen) {
            if (rightward) {
                chatOpen = false
                chatInputOpen = false
                statusNotice = ""
                buzz(Buzz.TICK)
                render()
                return true
            }
            return false
        }
        if (callPhase != CallPhase.OFF) {
            // During a live call, swipe right-to-left opens live transcript chat overlay
            if (!rightward) {
                chatOpen = true
                buzz(Buzz.TICK)
                render()
                return true
            }
            return false
        }
        val next = when (activePage) {
            WatchPage.CHATS -> if (!rightward) WatchPage.HOME else null
            WatchPage.HOME -> if (rightward) WatchPage.CHATS else WatchPage.APPS
            WatchPage.APPS -> if (rightward) WatchPage.HOME else WatchPage.SETTINGS
            WatchPage.SETTINGS -> if (rightward) WatchPage.APPS else null
        } ?: return false
        activePage = next
        buzz(Buzz.TICK)
        rootScroll?.scrollTo(0, 0)
        render()
        return true
    }

    private fun wakeHomeChrome() {
        cancelHomeChromeHide()
        if (!chromeVisible) {
            chromeVisible = true
            homeChromeOverlay?.animate()?.alpha(1f)?.setDuration(220L)?.start()
        }
        scheduleHomeChromeHide()
    }

    private fun cancelHomeChromeHide() {
        chromeHideRunnable?.let { mainHandler.removeCallbacks(it) }
        chromeHideRunnable = null
    }

    private fun scheduleHomeChromeHide() {
        cancelHomeChromeHide()
        if (!config.autoHideHomeChrome) return
        if (activePage != WatchPage.HOME || chatOpen || callPhase != CallPhase.OFF || voiceAskActive) return
        val st = currentState ?: return
        if (st.busy || statusNotice.isNotBlank() || availableWatchUpdate != null || st.approvals.isNotEmpty()) return
        val r = Runnable {
            if (activePage == WatchPage.HOME && !chatOpen && callPhase == CallPhase.OFF && !voiceAskActive && currentState?.busy != true) {
                chromeVisible = false
                homeChromeOverlay?.animate()?.alpha(0f)?.setDuration(420L)?.start()
            }
        }
        chromeHideRunnable = r
        mainHandler.postDelayed(r, 4500L)
    }

    private fun showVolumeArc(stream: Int) {
        val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
        val max = audio.getStreamMaxVolume(stream).coerceAtLeast(1)
        val cur = audio.getStreamVolume(stream).coerceIn(0, max)
        val frac = cur.toFloat() / max.toFloat()
        volumeArcView?.let { v ->
            v.setLevel(frac)
            v.visibility = View.VISIBLE
            v.alpha = 1f
        }
        volumeHideRunnable?.let { mainHandler.removeCallbacks(it) }
        val r = Runnable {
            volumeArcView?.animate()?.alpha(0f)?.setDuration(300L)?.withEndAction {
                volumeArcView?.visibility = View.GONE
            }?.start()
        }
        volumeHideRunnable = r
        mainHandler.postDelayed(r, 2400L)
    }

    /** The rotating bezel: scrolls the page, and during a live call turns the call volume up and down
     *  with the left-bezel curved cyan volume arc (matching the original Muse watch app). */
    override fun onGenericMotionEvent(ev: MotionEvent): Boolean {
        if (ev.action == MotionEvent.ACTION_SCROLL && ev.isFromSource(InputDevice.SOURCE_ROTARY_ENCODER)) {
            val axis = ev.getAxisValue(MotionEvent.AXIS_SCROLL)
            if (callPhase != CallPhase.OFF && !chatOpen) {
                val (steps, carry) = Bezel.volumeSteps(axis, volumeCarry)
                volumeCarry = carry
                if (steps != 0) {
                    val audio = getSystemService(Context.AUDIO_SERVICE) as AudioManager
                    val stream = if (callMode == "realtime") AudioManager.STREAM_VOICE_CALL else AudioManager.STREAM_MUSIC
                    val dir = if (steps > 0) AudioManager.ADJUST_RAISE else AudioManager.ADJUST_LOWER
                    repeat(kotlin.math.abs(steps)) { audio.adjustStreamVolume(stream, dir, 0) }
                    showVolumeArc(stream)
                    buzz(Buzz.TICK)
                }
            } else {
                val factor = ViewConfiguration.get(this).getScaledVerticalScrollFactor()
                rootScroll?.smoothScrollBy(0, Bezel.scrollPixels(axis, factor))
                rootScroll?.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK)
            }
            return true
        }
        return super.onGenericMotionEvent(ev)
    }

    private enum class Buzz { TICK, CLICK, HEAVY }

    /** A short vibration for what just happened: a tick for a notch, a click for a send, a heavy one for an end. */
    private fun buzz(kind: Buzz) {
        try {
            val vibrator: Vibrator? = if (android.os.Build.VERSION.SDK_INT >= 31) {
                (getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
            } else {
                @Suppress("DEPRECATION")
                getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
            }
            val effect = when (kind) {
                Buzz.TICK -> VibrationEffect.EFFECT_TICK
                Buzz.CLICK -> VibrationEffect.EFFECT_CLICK
                Buzz.HEAVY -> VibrationEffect.EFFECT_HEAVY_CLICK
            }
            vibrator?.vibrate(VibrationEffect.createPredefined(effect))
        } catch (_: Exception) {
        }
    }

    private fun startLiveCall() {
        ensureMicPermission {
            cancelVoiceAsk()
            endLiveCall()
            buzz(Buzz.CLICK)
            callPhase = CallPhase.CONNECTING
            callMuted = false
            callSpeakerOff = false
            callTranscript.clear()
            callStreamingReply = ""
            statusNotice = ""
            updateWakeLocks()
            render()

            callSocket = client.openCallWebSocket(
                patienceMs = config.patienceMs,
                onEvent = { ev -> mainHandler.post { handleCallEvent(ev) } },
                onClosed = { reason ->
                    mainHandler.post {
                        if (callPhase != CallPhase.OFF) {
                            statusNotice = reason ?: "Call ended"
                            endLiveCall()
                        }
                    }
                },
            )
        }
    }

    private fun handleCallEvent(ev: JSONObject) {
        if (callPhase == CallPhase.OFF) return
        when (ev.optString("kind")) {
            "call_ready" -> {
                callMode = ev.optString("mode", "universal")
                callModelLabel = ev.optString("model")
                callPhase = CallPhase.LISTENING
                voiceDraft = ""
                if (callMode == "realtime") {
                    startPcmCapture()
                } else {
                    beginSpeechRecognition(isLiveCall = true)
                }
                render()
            }
            "user_transcript" -> {
                val t = ev.optString("text").trim()
                if (t.isNotEmpty()) {
                    callTranscript.add("You: $t")
                    trimTranscript()
                    render()
                }
            }
            "assistant_start" -> {
                callPhase = CallPhase.THINKING
                callStreamingReply = ""
                render()
            }
            "assistant_delta" -> {
                callPhase = CallPhase.SPEAKING
                callStreamingReply += ev.optString("text")
                render()
            }
            "audio_delta" -> {
                callPhase = CallPhase.SPEAKING
                if (!callSpeakerOff) {
                    val b64 = ev.optString("pcm16_b64")
                    val sr = ev.optInt("sample_rate", 24000)
                    if (b64.isNotBlank()) playPcmChunk(b64, sr)
                }
                render()
            }
            "assistant_done" -> {
                val full = ev.optString("text").ifBlank { callStreamingReply }.trim()
                callStreamingReply = ""
                if (full.isNotEmpty()) {
                    callTranscript.add("${config.agentName}: $full")
                    trimTranscript()
                }
                if (callMode != "realtime" && full.isNotEmpty() && !callSpeakerOff && ttsReady) {
                    callPhase = CallPhase.SPEAKING
                    render()
                    tts?.speak(full, TextToSpeech.QUEUE_FLUSH, null, "watch_call_${System.currentTimeMillis()}")
                } else {
                    callPhase = CallPhase.LISTENING
                    if (callMode != "realtime" && !callMuted) {
                        beginSpeechRecognition(isLiveCall = true)
                    }
                    render()
                }
            }
            "call_notice" -> {
                statusNotice = ev.optString("text")
                render()
            }
            "call_error" -> {
                statusNotice = ev.optString("error", "Call error")
                render()
            }
        }
    }

    private fun trimTranscript() {
        while (callTranscript.size > 8) {
            callTranscript.removeAt(0)
        }
    }

    private fun onTtsFinished() {
        if (callPhase == CallPhase.OFF) return
        callPhase = CallPhase.LISTENING
        if (callMode != "realtime" && !callMuted) {
            beginSpeechRecognition(isLiveCall = true)
        }
        render()
    }

    private fun sendUniversalCallTurn(text: String) {
        val clean = text.trim()
        if (clean.isEmpty() || callSocket == null) return
        voiceDraft = ""
        voiceHoldUntilMs = 0L
        destroySpeechRecognizer()
        callPhase = CallPhase.THINKING
        callTranscript.add("You: $clean")
        trimTranscript()
        render()
        callSocket?.send(JSONObject().put("kind", "user_text").put("text", clean).toString())
    }

    private fun startPcmCapture() {
        stopPcmCapture()
        val sr = 24000
        val minBuf = AudioRecord.getMinBufferSize(sr, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            .coerceAtLeast(4800)
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_COMMUNICATION,
                sr,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                minBuf,
            )
        } catch (_: Exception) {
            null
        } ?: return
        audioRecord = rec
        rec.startRecording()
        audioRecordJob = lifecycleScope.launch(Dispatchers.IO) {
            val buf = ByteArray(2400) // 50ms of 24kHz mono 16-bit PCM
            while (isActive && callPhase != CallPhase.OFF) {
                val n = rec.read(buf, 0, buf.size)
                if (n > 0 && !callMuted) {
                    val slice = if (n == buf.size) buf else buf.copyOf(n)
                    val b64 = Base64.getEncoder().encodeToString(slice)
                    callSocket?.send(
                        JSONObject()
                            .put("kind", "audio_chunk")
                            .put("pcm16_b64", b64)
                            .put("sample_rate", sr)
                            .toString(),
                    )
                }
            }
        }
    }

    private fun stopPcmCapture() {
        audioRecordJob?.cancel()
        audioRecordJob = null
        try {
            audioRecord?.stop()
            audioRecord?.release()
        } catch (_: Exception) {
        }
        audioRecord = null
    }

    private fun playPcmChunk(b64: String, sampleRate: Int) {
        val bytes = try { Base64.getDecoder().decode(b64) } catch (_: Exception) { return }
        if (audioTrack == null) {
            val minBuf = AudioTrack.getMinBufferSize(
                sampleRate,
                AudioFormat.CHANNEL_OUT_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            ).coerceAtLeast(8192)
            audioTrack = AudioTrack.Builder()
                .setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .build(),
                )
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(sampleRate)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .build(),
                )
                .setBufferSizeInBytes(minBuf)
                .setTransferMode(AudioTrack.MODE_STREAM)
                .build().apply { play() }
        }
        try {
            audioTrack?.write(bytes, 0, bytes.size)
        } catch (_: Exception) {
        }
    }

    private fun stopPcmPlayback() {
        try {
            audioTrack?.stop()
            audioTrack?.release()
        } catch (_: Exception) {
        }
        audioTrack = null
    }

    private fun endLiveCall() {
        if (callPhase == CallPhase.OFF && callSocket == null) return
        buzz(Buzz.HEAVY)
        callPhase = CallPhase.OFF
        cancelVoiceAutoSend()
        destroySpeechRecognizer()
        stopPcmCapture()
        stopPcmPlayback()
        try { tts?.stop() } catch (_: Exception) {}
        try {
            callSocket?.send(JSONObject().put("kind", "end_call").toString())
            callSocket?.close(1000, "ended")
        } catch (_: Exception) {
        }
        callSocket = null
        voiceDraft = ""
        voiceHoldUntilMs = 0L
        updateWakeLocks()
        render()
        refreshState(silent = true)
    }

    // ---- UI Rendering (Muse-Style Pure OLED Watch Experience) ----

    private fun stopHaloAnimation() {
        haloAnim?.cancel()
        haloAnim = null
        heroBreathe?.cancel()
        heroBreathe = null
    }

    private fun render() {
        stopHaloAnimation()
        cancelHomeChromeHide()
        homeChromeOverlay = null
        heroAvatarContainer = null
        val col = rootCol ?: return
        col.removeAllViews()
        backdropLayer?.removeAllViews()
        bezelScrollbar?.visibility = View.GONE
        currentState?.let { renderedFingerprint = fingerprint(it) }

        if (!config.isPaired) {
            webShellOpen = false
            activeMiniApp = null
            renderPairScreen(col)
        } else if (webShellOpen || activeMiniApp != null) {
            renderWebAppShell(col)
        } else if (voiceAskActive) {
            renderVoiceAskScreen(col)
        } else if (chatOpen) {
            // Keep the living animated character softly dimmed behind the chat bubbles (just like Muse watch OS)
            backdropLayer?.let { bg ->
                val screenW = resources.displayMetrics.widthPixels
                val av = avatarHeroView(currentState)
                bg.addView(av, FrameLayout.LayoutParams(screenW, screenW, Gravity.CENTER))
                bg.addView(
                    View(this).apply { setBackgroundColor(Color.parseColor("#B8000000")) },
                    FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
                )
            }
            renderChatScreen(col)
        } else if (callPhase != CallPhase.OFF) {
            renderLiveCallScreen(col)
        } else {
            when (activePage) {
                WatchPage.CHATS -> renderChatsDrawerScreen(col)
                WatchPage.HOME -> renderHomeScreen(col)
                WatchPage.APPS -> renderWatchAppsScreen(col)
                WatchPage.SETTINGS -> renderSettingsScreen(col)
            }
        }
    }

    private fun renderWebAppShell(col: LinearLayout) {
        val mini = activeMiniApp
        val label = if (mini != null) "Back · ${mini.name}" else "Back to Watch"
        col.addView(
            pillButton(label, "#18181B", strokeHex = "#2E2E34") {
                webShellOpen = false
                activeMiniApp = null
                render()
            },
        )
        val base = WatchConfig.normalizeUrl(config.serverUrl)
        val tok = config.token.trim()
        val url = if (mini != null) {
            val path = if (mini.url.startsWith("http")) mini.url else "$base${mini.url}"
            if (tok.isNotEmpty() && !path.contains("token=")) {
                val sep = if (path.contains("?")) "&" else "?"
                "$path${sep}token=$tok"
            } else {
                path
            }
        } else {
            if (tok.isNotEmpty()) "$base/?watch=1#token=$tok" else "$base/?watch=1"
        }
        val wv = WebView(this).apply {
            setBackgroundColor(Color.BLACK)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.useWideViewPort = true
            settings.loadWithOverviewMode = true
            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean = false
            }
            if (tok.isNotEmpty()) {
                loadUrl(url, mapOf("Authorization" to "Bearer $tok"))
            } else {
                loadUrl(url)
            }
        }
        val h = (resources.displayMetrics.heightPixels * 0.80f).roundToInt().coerceAtLeast(dp(220))
        col.addView(wv, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, h))
    }

    private fun renderPairScreen(col: LinearLayout) {
        col.addView(brandHeader("MiniMuse", "Pair Watch", dotHex = "#3B82F6"))
        col.addView(subText("Fastest: open MiniMuse on your phone or PC on the same Wi-Fi, then tap Auto-Connect below (no typing needed)."))

        if (statusNotice.isNotBlank()) {
            col.addView(noticeCard(statusNotice, "#0F1B33", "#93C5FD", strokeHex = "#1E3A8A"))
        }

        val urlInput = inputBox("http://192.168.1.x:8787", config.serverUrl)
        val codeInput = inputBox("6-digit code", "").apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS
            gravity = Gravity.CENTER
            textSize = 15f
            setTypeface(Typeface.MONOSPACE, Typeface.BOLD)
        }

        col.addView(
            pillButton(
                if (scanningLan) "Searching Wi-Fi..." else "Auto-Connect on Wi-Fi",
                "#0168FF",
                strokeHex = "#3B82F6",
            ) {
                if (scanningLan) return@pillButton
                val typed = urlInput.text?.toString().orEmpty()
                scanningLan = true
                statusNotice = "Finding MiniMuse on Wi-Fi..."
                render()
                lifecycleScope.launch {
                    val found = LanDiscovery.scan(this@MainActivity, timeoutMs = 3600L)
                    if (found.isNotEmpty()) discoveredServers = found
                    scanningLan = false
                    val cands = WatchClient.pairingCandidates(typed, config.serverUrl, discoveredServers.map { it.url })
                    val res = client.autoClaimFirst(cands)
                    res.onSuccess {
                        statusNotice = ""
                        refreshState()
                        startPolling()
                    }.onFailure { err ->
                        statusNotice = err.message ?: "Could not auto-connect"
                        render()
                    }
                }
            },
        )

        if (discoveredServers.isNotEmpty()) {
            col.addView(sectionLabel("Found on Wi-Fi"))
            for (srv in discoveredServers) {
                col.addView(
                    pillButton("Connect ${srv.name}", "#121215", strokeHex = "#2A2A32") {
                        urlInput.setText(srv.url)
                        statusNotice = "Connecting to ${srv.name}..."
                        render()
                        lifecycleScope.launch {
                            val res = client.autoClaim(srv.url)
                            res.onSuccess {
                                statusNotice = ""
                                refreshState()
                                startPolling()
                            }.onFailure {
                                statusNotice = "Selected ${srv.name} — enter 6-digit code below"
                                render()
                            }
                        }
                    },
                )
            }
        }

        col.addView(
            pillButton(
                if (scanningLan) "Scanning Wi-Fi..." else "Scan Wi-Fi",
                "#101E36",
                fgHex = "#93C5FD",
                strokeHex = "#1D4ED8",
            ) {
                if (!scanningLan) startLanScan()
            },
        )

        col.addView(sectionLabel("Or pair with 6-digit code"))
        col.addView(codeInput)
        col.addView(sectionLabel("Server address (optional if on Wi-Fi)"))
        col.addView(urlInput)

        col.addView(
            pillButton("Pair with Code", "#142442", fgHex = "#93C5FD", strokeHex = "#2563EB") {
                val u = urlInput.text?.toString().orEmpty()
                val c = codeInput.text?.toString().orEmpty()
                statusNotice = "Pairing..."
                render()
                lifecycleScope.launch {
                    var cands = WatchClient.pairingCandidates(u, config.serverUrl, discoveredServers.map { it.url })
                    if (cands.isEmpty()) {
                        val found = LanDiscovery.scan(this@MainActivity, timeoutMs = 3500L)
                        if (found.isNotEmpty()) discoveredServers = found
                        cands = WatchClient.pairingCandidates(u, config.serverUrl, discoveredServers.map { it.url })
                    }
                    val res = if (c.trim().length < 4 && cands.isNotEmpty()) {
                        client.autoClaimFirst(cands)
                    } else {
                        client.claimFirst(cands, c)
                    }
                    res.onSuccess {
                        statusNotice = ""
                        refreshState()
                        startPolling()
                    }.onFailure { err ->
                        statusNotice = err.message ?: "Pairing failed"
                        render()
                    }
                }
            },
        )
    }

    private fun renderVoiceAskScreen(col: LinearLayout) {
        col.addView(pulsingOrb(label = "Listening", active = true, accentHex = "#0168FF"))
        col.addView(subText("Screen stays awake while speaking"))

        val draftCard = TextView(this).apply {
            text = voiceDraft.ifBlank { "Speak now. Pause when done or tap Send." }
            textSize = 13.5f
            setTextColor(if (voiceDraft.isBlank()) Color.parseColor("#9CA3AF") else Color.WHITE)
            background = outlinedRect("#121215", "#27272D", 16)
            setPadding(dp(12), dp(10), dp(12), dp(10))
            gravity = Gravity.CENTER_HORIZONTAL
        }
        col.addView(draftCard, marginBottomParams(dp(10)))

        val row = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
        }.also { it.layoutParams = marginBottomParams(dp(8)) }
        row.addView(
            pillButton("+10s", "#142442", fgHex = "#93C5FD", strokeHex = "#2563EB") {
                extendTalkingGrace()
            }.also {
                it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(4)
                }
            },
        )
        row.addView(
            pillButton("Send", "#0168FF", strokeHex = "#3B82F6") {
                finishVoiceNow()
            }.also {
                it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = dp(4)
                }
            },
        )
        col.addView(row)

        col.addView(
            pillButton("Cancel", "#141418", fgHex = "#D4D4D8", strokeHex = "#2A2A32") {
                cancelVoiceAsk()
                render()
            },
        )
    }

    private fun renderLiveCallScreen(col: LinearLayout) {
        val st = currentState
        val screenW = resources.displayMetrics.widthPixels
        val (phaseLabel, glowHex, showArc) = when (callPhase) {
            CallPhase.CONNECTING -> Triple("Connecting...", "#38BDF8", true)
            CallPhase.LISTENING -> Triple(if (callMuted) "Mic muted" else "Listening...", if (callMuted) "#94A3B8" else "#60A5FA", false)
            CallPhase.THINKING -> Triple("Thinking...", "#818CF8", true)
            CallPhase.SPEAKING -> Triple("Speaking...", "#34D399", false)
            CallPhase.OFF -> Triple("", "#38BDF8", false)
        }

        // Exact Muse Watch Call Screen: full-bleed animated character stays front & center,
        // curved glowing status along the top rim, sweeping arc while connecting, left-bezel
        // cyan volume arc on bezel rotation, and 3 curved bezel buttons (bright pink End Call,
        // Chat overlay toggle, Mic/Mute).
        val hero = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(screenW, screenW).apply {
                val round = resources.configuration.isScreenRound
                val padH = if (round) (screenW * 0.11f).roundToInt() else dp(12)
                val padV = if (round) (screenW * 0.14f).roundToInt() else dp(16)
                marginStart = -padH
                marginEnd = -padH
                topMargin = -padV
            }
        }

        val av = avatarHeroView(st)
        av.alpha = if (callPhase == CallPhase.CONNECTING) 0.58f else 0.92f
        hero.addView(av, FrameLayout.LayoutParams(screenW, screenW))

        if (showArc) {
            hero.addView(PulseArcView(this), FrameLayout.LayoutParams(screenW, screenW))
        }

        if (phaseLabel.isNotBlank()) {
            hero.addView(
                CurvedTopTextView(this, phaseLabel, glowHex),
                FrameLayout.LayoutParams(screenW, screenW),
            )
        }

        // Subtle live caption if someone is speaking
        val caption = when {
            voiceDraft.isNotBlank() -> voiceDraft
            callStreamingReply.isNotBlank() -> callStreamingReply
            else -> ""
        }
        if (caption.isNotBlank()) {
            hero.addView(
                TextView(this).apply {
                    text = caption.take(90)
                    textSize = 11.5f
                    maxLines = 2
                    ellipsize = TextUtils.TruncateAt.END
                    setTextColor(Color.WHITE)
                    gravity = Gravity.CENTER
                    background = roundRect("#A6121420", 14)
                    setPadding(dp(10), dp(5), dp(10), dp(5))
                },
                FrameLayout.LayoutParams(
                    (screenW * 0.72f).roundToInt(),
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL,
                ).apply { bottomMargin = dp(86) },
            )
        }

        // Bottom 3 curved bezel buttons: bright pink End Call (left), Chat overlay (center), Mute (right)
        hero.addView(
            arcButtons(
                screenW,
                iconButton(R.drawable.ic_call, "#FF2D8E") { endLiveCall() },
                iconButton(R.drawable.ic_chat, null) {
                    chatOpen = true
                    render()
                },
                iconButton(R.drawable.ic_mic, if (callMuted) "#991B1B" else null) {
                    callMuted = !callMuted
                    if (callMode != "realtime") {
                        if (callMuted) destroySpeechRecognizer() else beginSpeechRecognition(isLiveCall = true)
                    }
                    render()
                },
            ),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ).apply { bottomMargin = dp(6) },
        )
        col.addView(hero)

        // Scroll down below hero during a call for +10s grace, Done, Speaker, and recent transcript
        if (callPhase == CallPhase.LISTENING || callMode == "universal") {
            val speakRow = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
            }.also { it.layoutParams = marginBottomParams(dp(7)) }
            speakRow.addView(
                pillButton("+10s", "#142442", fgHex = "#93C5FD", strokeHex = "#2563EB") {
                    extendTalkingGrace()
                }.also {
                    it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                        marginEnd = dp(4)
                    }
                },
            )
            if (voiceDraft.isNotBlank() || callMode == "realtime") {
                speakRow.addView(
                    pillButton("Done", "#0168FF", strokeHex = "#3B82F6") {
                        finishVoiceNow()
                    }.also {
                        it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                            marginStart = dp(4)
                        }
                    },
                )
            }
            col.addView(speakRow)
        }

        col.addView(
            pillButton(
                if (callSpeakerOff) "Speaker: OFF" else "Speaker: ON",
                if (callSpeakerOff) "#27272A" else "#121216",
                strokeHex = "#2E2E36",
            ) {
                callSpeakerOff = !callSpeakerOff
                if (callSpeakerOff) {
                    try { tts?.stop() } catch (_: Exception) {}
                }
                render()
            },
        )

        for (line in callTranscript.takeLast(4).reversed()) {
            val isUserLine = line.startsWith("You: ")
            val clean = if (isUserLine) line.removePrefix("You: ") else line.removePrefix("${config.agentName}: ")
            col.addView(bubbleView(clean, isUser = isUserLine))
        }
    }

    private fun renderHomeScreen(col: LinearLayout) {
        val st = currentState
        val screenW = resources.displayMetrics.widthPixels

        // Exact Muse Watch Home: full-bleed animated character filling the round OLED face,
        // 3 translucent glass round buttons hugging the bottom bezel curve (auto-fading after
        // idle so only the living character shows; tap anywhere to wake), curved top status
        // only when connecting/busy/error, and scrolling up shrinks the character smoothly.
        val hero = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(screenW, screenW).apply {
                // cancel the column padding so the face is full-bleed
                val round = resources.configuration.isScreenRound
                val padH = if (round) (screenW * 0.11f).roundToInt() else dp(12)
                val padV = if (round) (screenW * 0.14f).roundToInt() else dp(16)
                marginStart = -padH
                marginEnd = -padH
                topMargin = -padV
            }
        }

        val av = avatarHeroView(st)
        heroAvatarContainer = av
        hero.addView(av, FrameLayout.LayoutParams(screenW, screenW))

        val (statusLine, statusHex) = when {
            st != null && st.busy -> "Working..." to "#8AB8FF"
            st != null && statusNotice.isNotBlank() -> statusNotice to "#FDE68A"
            st != null -> "" to "#9BE7C4"
            lastStateError.isNotBlank() && stateFailures >= 2 ->
                "Offline · ${hostLabel().take(16)}" to "#FCA5A5"
            else -> "Connecting..." to "#8AB8FF"
        }
        if (statusLine.isNotBlank()) {
            hero.addView(
                CurvedTopTextView(this, statusLine, statusHex),
                FrameLayout.LayoutParams(screenW, screenW),
            )
        }

        if (st == null || st.busy) {
            hero.addView(PulseArcView(this), FrameLayout.LayoutParams(screenW, screenW))
        }

        // Chrome overlay (buttons + quiet hints + page dots) — auto-hides when peacefully idle
        val chrome = FrameLayout(this).apply {
            alpha = if (chromeVisible || !config.autoHideHomeChrome || st == null || st.busy) 1f else 0f
        }
        homeChromeOverlay = chrome

        val overlayCol = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
        }
        val upd = availableWatchUpdate
        if (upd != null) {
            overlayCol.addView(
                pillButton(
                    if (updatingWatchApk) "Updating ${watchUpdateProgress}%..." else "Update Watch v${upd.version}",
                    "#CC0D9488",
                    strokeHex = "#14B8A6",
                ) {
                    if (!updatingWatchApk) startSingleFlightWatchUpdate(upd)
                }.apply {
                    isEnabled = !updatingWatchApk
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    )
                },
            )
        }
        if (st != null && st.approvals.isNotEmpty()) {
            overlayCol.addView(
                pillButton(
                    "Approval needed (${st.approvals.size})",
                    "#CC92400E",
                    fgHex = "#FEF3C7",
                    strokeHex = "#F59E0B",
                ) {
                    activePage = WatchPage.CHATS
                    render()
                }.apply {
                    layoutParams = LinearLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                    )
                },
            )
        }
        if (overlayCol.childCount > 0) {
            chrome.addView(
                overlayCol,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.TOP,
                ).apply { topMargin = dp(40) },
            )
        }

        // Exact Muse video 3 translucent glass bezel buttons (call pink ONLY during active call)
        chrome.addView(
            arcButtons(
                screenW,
                iconButton(R.drawable.ic_call, null) { startLiveCall() },
                iconButton(R.drawable.ic_chat, null) {
                    chatOpen = true
                    statusNotice = ""
                    render()
                },
                iconButton(R.drawable.ic_mic, null) { startVoiceAsk() },
            ),
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM,
            ).apply { bottomMargin = dp(6) },
        )

        // Subtle 4-page indicator dots at very top rim when no status text occupies it
        if (statusLine.isBlank()) {
            chrome.addView(
                pageDotsRow(WatchPage.HOME),
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.TOP or Gravity.CENTER_HORIZONTAL,
                ).apply { topMargin = dp(10) },
            )
        }

        hero.addView(chrome, FrameLayout.LayoutParams(screenW, screenW))
        col.addView(hero)

        // A paired watch that cannot reach its server must say so, with a way out, instead of
        // sitting on "Connecting..." forever.
        if (st == null && lastStateError.isNotBlank() && stateFailures >= 2) {
            col.addView(
                noticeCard(
                    "Not reaching ${hostLabel()}: $lastStateError",
                    "#2A1215",
                    "#FCA5A5",
                    strokeHex = "#7F1D1D",
                ),
            )
            val retryRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
            retryRow.addView(
                pillButton("Retry", "#101E36", fgHex = "#93C5FD", strokeHex = "#1D4ED8") {
                    stateFailures = 0
                    lastStateError = ""
                    render()
                    refreshState()
                }.also {
                    it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                        marginEnd = dp(4)
                    }
                },
            )
            retryRow.addView(
                pillButton("Re-pair", "#121216", fgHex = "#E4E4E7", strokeHex = "#26262C") {
                    config.clearPairing()
                    currentState = null
                    stopPolling()
                    statusNotice = ""
                    render()
                }.also {
                    it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                        marginStart = dp(4)
                    }
                },
            )
            col.addView(retryRow)
        }

        // Scroll below hero reveals quick info cards + swipe hint shortcuts
        if (st != null && st.cards.isNotEmpty()) {
            for (card in st.cards.take(2)) {
                val txt = if (card.title.isNotEmpty()) "${card.title}: ${card.body}" else card.body
                col.addView(noticeCard(txt.take(140), "#121216", "#E2E8F0", strokeHex = "#26262C"))
            }
        }

        val navRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            layoutParams = marginBottomParams(dp(6))
        }
        navRow.addView(
            pillButton("Chats", "#121216", fgHex = "#D4D4D8", strokeHex = "#26262C") {
                activePage = WatchPage.CHATS
                rootScroll?.scrollTo(0, 0)
                render()
            }.also {
                it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(4)
                }
            },
        )
        navRow.addView(
            pillButton("Apps", "#121216", fgHex = "#D4D4D8", strokeHex = "#26262C") {
                activePage = WatchPage.APPS
                rootScroll?.scrollTo(0, 0)
                render()
            }.also {
                it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginStart = dp(4)
                }
            },
        )
        col.addView(navRow)
        col.addView(
            pillButton("Settings", "#0D0D10", fgHex = "#9CA3AF", strokeHex = "#222228") {
                activePage = WatchPage.SETTINGS
                rootScroll?.scrollTo(0, 0)
                render()
            },
        )

        scheduleHomeChromeHide()
    }

    /** Swipe Left -> Right from Home: Chats & Side Menu (switch between Main Chat & side chats,
     *  answer approvals, and trigger quick actions). */
    private fun renderChatsDrawerScreen(col: LinearLayout) {
        val st = currentState
        col.addView(pageDotsRow(WatchPage.CHATS))
        col.addView(brandHeader("Chats", config.agentName, dotHex = "#495596"))

        if (st != null && st.approvals.isNotEmpty()) {
            col.addView(sectionLabel("Needs approval"))
            for (ap in st.approvals.take(3)) {
                col.addView(noticeCard("${ap.tool}: ${ap.summary}", "#271A07", "#FDE68A", strokeHex = "#92400E"))
                val apRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
                apRow.addView(
                    pillButton("Allow", "#15803D", strokeHex = "#22C55E") {
                        lifecycleScope.launch {
                            client.approve(ap, approved = true)
                            refreshState()
                        }
                    }.also {
                        it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                            marginEnd = dp(4)
                        }
                    },
                )
                apRow.addView(
                    pillButton("Deny", "#7F1D1D", strokeHex = "#EF4444") {
                        lifecycleScope.launch {
                            client.approve(ap, approved = false)
                            refreshState()
                        }
                    }.also {
                        it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                            marginStart = dp(4)
                        }
                    },
                )
                col.addView(apRow)
            }
        }

        val threads = st?.threads ?: emptyList()
        if (threads.isNotEmpty()) {
            for (th in threads.take(12)) {
                val active = th.id == selectedThreadId
                val card = LinearLayout(this).apply {
                    orientation = LinearLayout.VERTICAL
                    background = rippleBackground(
                        if (active) "#1E294B" else "#121216",
                        if (active) "#495596" else "#26262C",
                        16,
                    )
                    setPadding(dp(12), dp(9), dp(12), dp(9))
                    layoutParams = marginBottomParams(dp(7))
                    setOnClickListener {
                        buzz(Buzz.CLICK)
                        selectedThreadId = th.id
                        activePage = WatchPage.HOME
                        chatOpen = true
                        refreshState(silent = false)
                        render()
                    }
                }
                val titleRow = TextView(this).apply {
                    text = if (th.isMain) "${th.title} ★" else th.title
                    textSize = 13f
                    setTypeface(null, Typeface.BOLD)
                    setTextColor(Color.WHITE)
                    maxLines = 1
                    ellipsize = TextUtils.TruncateAt.END
                }
                card.addView(titleRow)
                if (th.preview.isNotBlank()) {
                    card.addView(
                        TextView(this).apply {
                            text = th.preview
                            textSize = 11f
                            setTextColor(Color.parseColor("#A1A1AA"))
                            maxLines = 2
                            ellipsize = TextUtils.TruncateAt.END
                        },
                    )
                }
                col.addView(card)
            }
        } else {
            col.addView(
                pillButton("Open Main Chat", "#495596", strokeHex = "#6366F1") {
                    selectedThreadId = "main"
                    activePage = WatchPage.HOME
                    chatOpen = true
                    render()
                },
            )
        }

        if (st != null && st.actions.isNotEmpty()) {
            col.addView(sectionLabel("Quick actions"))
            for (act in st.actions.take(3)) {
                col.addView(
                    pillButton(act.label, "#121216", fgHex = "#E4E4E7", strokeHex = "#26262C") {
                        selectedThreadId = "main"
                        activePage = WatchPage.HOME
                        chatOpen = true
                        submitWatchAsk(act.prompt)
                    },
                )
            }
        }

        col.addView(
            pillButton("Home →", "#0D0D10", fgHex = "#9CA3AF", strokeHex = "#222228") {
                activePage = WatchPage.HOME
                rootScroll?.scrollTo(0, 0)
                render()
            },
        )
    }

    /** Swipe Right -> Left from Home: MiniMuse Watch Apps (open AI-built HTML apps on your wrist,
     *  or ask MiniMuse to build / optimize an app for the watch). */
    private fun renderWatchAppsScreen(col: LinearLayout) {
        val st = currentState
        col.addView(pageDotsRow(WatchPage.APPS))
        col.addView(brandHeader("Watch Apps", "Made by ${config.agentName}", dotHex = "#10B981"))

        val apps = st?.apps ?: emptyList()
        if (apps.isEmpty()) {
            col.addView(
                noticeCard(
                    "No MiniMuse apps found yet. Ask ${config.agentName} to make a watch app or optimize an existing app for Wear OS!",
                    "#121216",
                    "#A1A1AA",
                    strokeHex = "#26262C",
                ),
            )
        } else {
            for (app in apps.take(15)) {
                val badge = if (app.watchOptimized) " · Watch" else ""
                col.addView(
                    pillButton(
                        "${app.name}$badge",
                        if (app.watchOptimized) "#102420" else "#121216",
                        fgHex = "#FFFFFF",
                        strokeHex = if (app.watchOptimized) "#10B981" else "#26262C",
                    ) {
                        buzz(Buzz.CLICK)
                        activeMiniApp = app
                        render()
                    },
                )
            }
        }

        col.addView(
            pillButton("+ Ask to Build Watch App", "#101E36", fgHex = "#93C5FD", strokeHex = "#2563EB") {
                activePage = WatchPage.HOME
                startVoiceAsk()
            },
        )
        col.addView(
            pillButton("Settings →", "#121216", fgHex = "#E4E4E7", strokeHex = "#26262C") {
                activePage = WatchPage.SETTINGS
                rootScroll?.scrollTo(0, 0)
                render()
            },
        )
    }

    /** Swipe Right -> Left from Apps: Watch Settings, Bluetooth/server self-update, Live UI, Unpair. */
    private fun renderSettingsScreen(col: LinearLayout) {
        val st = currentState
        col.addView(pageDotsRow(WatchPage.SETTINGS))
        col.addView(
            brandHeader(
                "Settings",
                if (st != null) "Connected · ${hostLabel().take(18)}" else "Offline · ${hostLabel().take(18)}",
                dotHex = if (st != null) "#10B981" else "#EF4444",
            ),
        )

        if (statusNotice.isNotBlank()) {
            col.addView(noticeCard(statusNotice, "#0F1B33", "#93C5FD", strokeHex = "#1E3A8A"))
        }

        val upd = availableWatchUpdate
        if (upd != null) {
            col.addView(
                pillButton(
                    if (updatingWatchApk) "Updating ${watchUpdateProgress}%..." else "Install Watch v${upd.version}",
                    "#0D9488",
                    strokeHex = "#14B8A6",
                ) {
                    if (!updatingWatchApk) startSingleFlightWatchUpdate(upd)
                }.apply { isEnabled = !updatingWatchApk },
            )
        }

        val checkLabel = if (checkingWatchUpdate) "Checking Updates..." else "Check Watch Update (v${BuildConfig.VERSION_NAME})"
        col.addView(
            pillButton(checkLabel, "#141418", strokeHex = "#282830") {
                if (!checkingWatchUpdate) maybeAutoCheckWatchUpdate(force = true)
            }.apply { isEnabled = !checkingWatchUpdate },
        )

        val wakeLabel = if (config.keepScreenOnDuringVoice) "Stay Awake Voice: ON" else "Stay Awake Voice: OFF"
        col.addView(
            pillButton(wakeLabel, "#141418", strokeHex = "#282830") {
                config.keepScreenOnDuringVoice = !config.keepScreenOnDuringVoice
                updateWakeLocks()
                render()
            },
        )

        val autoHideLabel = if (config.autoHideHomeChrome) "Auto-Hide Buttons: ON" else "Auto-Hide Buttons: OFF"
        col.addView(
            pillButton(autoHideLabel, "#141418", strokeHex = "#282830") {
                config.autoHideHomeChrome = !config.autoHideHomeChrome
                chromeVisible = true
                render()
            },
        )

        col.addView(
            pillButton("Reconnect / Refresh", "#101E36", fgHex = "#93C5FD", strokeHex = "#1D4ED8") {
                stateFailures = 0
                lastStateError = ""
                statusNotice = "Refreshing..."
                render()
                refreshState(silent = false)
            },
        )

        col.addView(
            pillButton("Open Full Live UI", "#121216", fgHex = "#E4E4E7", strokeHex = "#26262C") {
                webShellOpen = true
                render()
            },
        )

        col.addView(
            pillButton("Unpair Watch", "#3B0D0D", fgHex = "#FCA5A5", strokeHex = "#7F1D1D") {
                endLiveCall()
                config.clearPairing()
                currentState = null
                statusNotice = ""
                activePage = WatchPage.HOME
                render()
                startLanScan()
            },
        )
    }

    private fun pageDotsRow(current: WatchPage): LinearLayout {
        val pages = listOf(WatchPage.CHATS, WatchPage.HOME, WatchPage.APPS, WatchPage.SETTINGS)
        return LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                gravity = Gravity.CENTER_HORIZONTAL
                bottomMargin = dp(4)
            }
            for (p in pages) {
                val active = p == current
                addView(
                    View(this@MainActivity).apply {
                        val sz = dp(if (active) 6 else 4)
                        layoutParams = LinearLayout.LayoutParams(sz, sz).apply {
                            marginStart = dp(3)
                            marginEnd = dp(3)
                        }
                        background = GradientDrawable().apply {
                            shape = GradientDrawable.OVAL
                            setColor(if (active) Color.WHITE else Color.parseColor("#55FFFFFF"))
                        }
                    },
                )
            }
        }
    }

    /** The three round buttons. On a round screen they sit on the bezel's curve: the chat
     *  button a little lower than the two at the sides, so all three look like they rest on
     *  the edge instead of floating on a straight line. */
    private fun arcButtons(screenW: Int, call: View, chat: View, voice: View): View {
        val round = resources.configuration.isScreenRound
        if (!round) {
            return LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
                addView(call)
                addView(chat)
                addView(voice)
            }
        }
        val size = dp(48)
        val sideBottom = dp(30)
        val container = FrameLayout(this)
        container.addView(
            call,
            FrameLayout.LayoutParams(size, size, Gravity.BOTTOM or Gravity.START).apply {
                marginStart = dp(16)
                bottomMargin = sideBottom
            },
        )
        container.addView(
            voice,
            FrameLayout.LayoutParams(size, size, Gravity.BOTTOM or Gravity.END).apply {
                marginEnd = dp(16)
                bottomMargin = sideBottom
            },
        )
        container.addView(
            chat,
            FrameLayout.LayoutParams(size, size, Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL).apply {
                bottomMargin = dp(8)
            },
        )
        container.layoutParams = LinearLayout.LayoutParams(screenW, dp(86))
        return container
    }

    /** The address the watch is trying, short enough for the round screen. */
    private fun hostLabel(): String {
        val url = WatchConfig.normalizeUrl(config.serverUrl)
        if (url.isBlank()) return "the server"
        return url.removePrefix("http://").removePrefix("https://").trimEnd('/')
    }

    private fun avatarHeroView(st: WatchState?): View {
        val id = st?.avatarId ?: "dragon"
        if (id.isBlank() || id == "emoji") {
            return FrameLayout(this).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(Color.parseColor(st?.avatarColor ?: "#0064d4"))
                }
                addView(
                    TextView(this@MainActivity).apply {
                        text = st?.avatarEmoji ?: "?"
                        textSize = 72f
                        gravity = Gravity.CENTER
                    },
                    FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT,
                    ),
                )
            }
        }
        val screenW = resources.displayMetrics.widthPixels
        val holder = FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(screenW, screenW)
            setBackgroundColor(Color.BLACK)
        }
        val still = ImageView(this).apply {
            scaleType = ImageView.ScaleType.CENTER_CROP
            val bmp = avatarBitmapFor(id)
            if (bmp != null) {
                setImageBitmap(bmp)
            } else {
                setImageResource(R.drawable.dragon_idle)
                if (id != "dragon") loadAvatarAsync(id)
            }
        }
        holder.addView(
            still,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
        // the moving version on top of the still: the still is what shows before it is ready,
        // on faces that have no clip, and while the clip is still downloading
        attachHeroVideo(holder, still, id, if (st?.busy == true) "working" else "idle")
        // no clip on its way or playing: let the still breathe, so it is never frozen
        if (heroVideo == null) breathe(still)
        return holder
    }

    /** A slow breath on the still, so a face with no clip (the bundled dragon, a face drawn
     *  before animations existed) is alive too. The clip cancels it when it starts. */
    private fun breathe(still: ImageView) {
        heroBreathe?.cancel()
        val x = ObjectAnimator.ofFloat(still, View.SCALE_X, 1f, 1.035f).apply {
            duration = 2400L
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            interpolator = AccelerateDecelerateInterpolator()
        }
        val y = ObjectAnimator.ofFloat(still, View.SCALE_Y, 1f, 1.035f).apply {
            duration = 2400L
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            interpolator = AccelerateDecelerateInterpolator()
        }
        heroBreathe = AnimatorSet().apply {
            playTogether(x, y)
            start()
        }
    }

    /** Play the agent's own animation on the home screen, looped and silent.
     *
     *  The same `mp4` the app plays beside a chat is fetched once (a player cannot send the
     *  access token) and then reused; the view itself is kept between renders so a state
     *  poll does not restart the loop. */
    private fun attachHeroVideo(holder: FrameLayout, still: ImageView, id: String, mood: String) {
        if (id.isBlank() || id == "emoji") return
        val key = "$id|$mood"
        val existing = heroVideo
        if (existing != null && heroVideoId == id && heroVideoMood == mood) {
            (existing.parent as? ViewGroup)?.removeView(existing)
            holder.addView(
                existing,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                ),
            )
            return
        }
        if (heroVideoFailed.contains(key)) return
        val retryAt = heroClipRetryAt[key] ?: 0L
        if (System.currentTimeMillis() < retryAt) return
        val safeId = id.replace(Regex("[^A-Za-z0-9._-]"), "_")
        val clip = File(cacheDir, "avatar-$safeId-$mood.mp4")
        if (clip.exists() && clip.length() > 10_000L) {
            startHeroVideo(holder, still, clip, id, mood)
            return
        }
        if (heroClipJob?.isActive == true) return
        heroClipJob = lifecycleScope.launch {
            client.fetchAvatarClip(id, mood, cacheDir).onSuccess { file ->
                mainHandler.post {
                    if (!config.isPaired || chatOpen || callPhase != CallPhase.OFF || voiceAskActive) {
                        return@post
                    }
                    if (currentState?.avatarId != id) return@post
                    if (holder.isAttachedToWindow) {
                        startHeroVideo(holder, still, file, id, mood)
                    } else {
                        // the screen was rebuilt while the clip was on its way: draw again so
                        // the cached clip is picked up by the view that is on screen now
                        render()
                    }
                }
            }.onFailure {
                // a face with no clip is worth remembering; a download that failed because
                // the watch was offline is worth trying again in a minute
                heroClipRetryAt[key] = System.currentTimeMillis() + 60_000L
            }
        }
    }

    private fun startHeroVideo(holder: FrameLayout, still: ImageView, file: File, id: String, mood: String) {
        val previous = heroVideo
        if (previous != null) {
            (previous.parent as? ViewGroup)?.removeView(previous)
            try {
                previous.stopPlayback()
            } catch (_: Exception) {
            }
        }
        val video = VideoView(this).apply {
            setVideoPath(file.absolutePath)
            setOnPreparedListener { mp ->
                mp.isLooping = true
                mp.setVolume(0f, 0f)
                start()
                // the real clip is playing now: drop the breathing still under it
                heroBreathe?.cancel()
                heroBreathe = null
                still.alpha = 0f
            }
            setOnErrorListener { _, _, _ ->
                heroVideoFailed.add("$id|$mood")
                visibility = View.GONE
                still.alpha = 1f
                true
            }
        }
        heroVideo = video
        heroVideoId = id
        heroVideoMood = mood
        holder.addView(
            video,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT,
            ),
        )
    }

    private fun avatarBitmapFor(id: String): Bitmap? {
        if (avatarLoadedKey == id && avatarBitmap != null) return avatarBitmap
        if (id == "dragon") {
            val bmp = BitmapFactory.decodeResource(resources, R.drawable.dragon_idle)
            if (bmp != null) {
                avatarBitmap = bmp
                avatarLoadedKey = id
            }
            return bmp
        }
        // A studio face: the cached download first, then the server.
        if (avatarFailedKey == id) return null
        val f = File(cacheDir, "avatar-${id.replace(Regex("[^A-Za-z0-9._-]"), "_")}.webp")
        if (f.exists()) {
            val bmp = BitmapFactory.decodeFile(f.absolutePath)
            if (bmp != null) {
                avatarBitmap = bmp
                avatarLoadedKey = id
                return bmp
            }
            f.delete()
        }
        return null
    }

    private fun loadAvatarAsync(id: String) {
        if (id.isBlank() || id == "dragon") return
        if (avatarFailedKey == id || avatarLoadJob?.isActive == true) return
        avatarLoadJob = lifecycleScope.launch {
            client.fetchAvatarFile(id, cacheDir).onSuccess { f ->
                val bmp = BitmapFactory.decodeFile(f.absolutePath)
                if (bmp != null) {
                    avatarBitmap = bmp
                    avatarLoadedKey = id
                    mainHandler.post {
                        if (config.isPaired && !chatOpen && callPhase == CallPhase.OFF && !voiceAskActive) render()
                    }
                }
            }.onFailure {
                avatarFailedKey = id
            }
        }
    }

    /** The chat overlay matching the real Muse watch OS: rich indigo assistant bubbles,
     *  warm orange user pills, emoji reactions, curved right-edge scrollbar, keyboard + mic
     *  along the bottom, and auto-scroll to newest messages at the bottom. */
    private fun renderChatScreen(col: LinearLayout) {
        val st = currentState
        val headerTitle = if (selectedThreadId == "main") config.agentName else (st?.threadTitle ?: selectedThreadId)
        col.addView(
            TextView(this).apply {
                text = "← $headerTitle"
                textSize = 12.5f
                setTypeface(null, Typeface.BOLD)
                setTextColor(Color.parseColor("#CBD5E1"))
                gravity = Gravity.CENTER
                setPadding(dp(8), dp(2), dp(8), dp(8))
                setOnClickListener {
                    buzz(Buzz.TICK)
                    chatOpen = false
                    chatInputOpen = false
                    statusNotice = ""
                    render()
                }
            },
        )
        // Approvals need an answer, so they come first.
        if (st != null && st.approvals.isNotEmpty()) {
            for (ap in st.approvals.take(3)) {
                col.addView(noticeCard("${ap.tool}: ${ap.summary}", "#271A07", "#FDE68A", strokeHex = "#92400E"))
                val apRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
                apRow.addView(
                    pillButton("Allow", "#15803D", strokeHex = "#22C55E") {
                        lifecycleScope.launch {
                            client.approve(ap, approved = true)
                            refreshState()
                        }
                    }.also {
                        it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                            marginEnd = dp(4)
                        }
                    },
                )
                apRow.addView(
                    pillButton("Deny", "#7F1D1D", strokeHex = "#EF4444") {
                        lifecycleScope.launch {
                            client.approve(ap, approved = false)
                            refreshState()
                        }
                    }.also {
                        it.layoutParams = LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                            marginStart = dp(4)
                        }
                    },
                )
                col.addView(apRow)
            }
        }
        if (statusNotice.isNotBlank()) {
            col.addView(noticeCard(statusNotice, "#0F1B33", "#93C5FD", strokeHex = "#1E3A8A"))
        }
        val recent = st?.recent ?: emptyList()
        for (msg in recent) {
            col.addView(bubbleView(msg.text.take(600), isUser = msg.role == "user", reactions = msg.reactions))
        }
        if (recent.isEmpty()) {
            col.addView(noticeCard("Say hello - ${config.agentName} answers here.", "#121216", "#A1A1AA", strokeHex = "#26262C"))
        }
        // Bottom icon bar: pink End Call (if call is live), Keyboard, inline input + Send, Mic.
        val inputRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            layoutParams = LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
            ).apply {
                topMargin = dp(8)
            }
        }
        if (callPhase != CallPhase.OFF && !chatInputOpen) {
            inputRow.addView(
                iconButton(R.drawable.ic_call, "#FF2D8E", sizeDp = 40) { endLiveCall() },
            )
        }
        inputRow.addView(
            iconButton(R.drawable.ic_keyboard, null, sizeDp = 40) {
                keyboardWarned = false
                chatInputOpen = !chatInputOpen
                render()
            },
        )
        if (chatInputOpen) {
            val input = EditText(this).apply {
                hint = "Message..."
                setText(chatDraft)
                setHintTextColor(Color.parseColor("#71717A"))
                setTextColor(Color.WHITE)
                textSize = 13f
                isSingleLine = true
                isFocusableInTouchMode = true
                imeOptions = EditorInfo.IME_ACTION_SEND
                background = outlinedRect("#121216", "#495596", 14)
                setPadding(dp(10), dp(8), dp(10), dp(8))
                addTextChangedListener(object : TextWatcher {
                    override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                    override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
                    override fun afterTextChanged(s: Editable?) {
                        chatDraft = s?.toString() ?: ""
                    }
                })
                setOnEditorActionListener { _, actionId, event ->
                    val sendKey = actionId == EditorInfo.IME_ACTION_SEND ||
                        (event?.keyCode == android.view.KeyEvent.KEYCODE_ENTER &&
                            event.action == android.view.KeyEvent.ACTION_DOWN)
                    if (sendKey) {
                        val text = chatDraft.trim()
                        if (text.isNotEmpty()) {
                            chatDraft = ""
                            chatInputOpen = false
                            submitWatchAsk(text)
                        }
                        true
                    } else {
                        false
                    }
                }
            }
            chatField = input
            inputRow.addView(
                input,
                LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f).apply {
                    marginEnd = dp(4)
                },
            )
            inputRow.addView(
                iconButton(R.drawable.ic_send, "#E87A24", sizeDp = 40) {
                    val text = chatDraft.trim()
                    if (text.isNotEmpty()) {
                        chatDraft = ""
                        chatInputOpen = false
                        submitWatchAsk(text)
                    }
                },
            )
        } else {
            inputRow.addView(View(this), LinearLayout.LayoutParams(0, 1, 1f))
        }
        inputRow.addView(
            iconButton(R.drawable.ic_mic, null, sizeDp = 40) { startVoiceAsk() },
        )
        col.addView(inputRow)

        // Scroll to newest messages at the bottom automatically
        rootScroll?.post {
            rootScroll?.fullScroll(View.FOCUS_DOWN)
        }

        // The keyboard button promises a keyboard: give the field focus and ask the watch's
        // own input method to open. Without this the field existed but nothing ever appeared.
        if (chatInputOpen) {
            chatField?.let { field ->
                field.post {
                    field.requestFocus()
                    val imm = getSystemService(INPUT_METHOD_SERVICE) as? InputMethodManager
                    val shown = imm?.showSoftInput(field, InputMethodManager.SHOW_IMPLICIT) == true
                    if (!shown) {
                        imm?.showSoftInput(field, InputMethodManager.SHOW_FORCED)
                        if (!keyboardWarned) {
                            keyboardWarned = true
                            flash("No keyboard on this watch - use the mic, or install Gboard")
                        }
                    }
                }
            }
        }
    }

    /** A round icon button like the Muse hero's: a dark frosted translucent glass circle with a
     *  delicate white rim and white vector glyph, turning bright pink (#FF2D8E) when a call is active. */
    private fun iconButton(iconRes: Int, activeHex: String? = null, sizeDp: Int = 48, onClick: () -> Unit): View {
        val size = dp(sizeDp)
        val pad = dp(sizeDp / 5)
        return FrameLayout(this).apply {
            layoutParams = LinearLayout.LayoutParams(size, size).apply {
                marginStart = dp(4)
                marginEnd = dp(4)
            }
            background = RippleDrawable(
                ColorStateList.valueOf(Color.parseColor("#38FFFFFF")),
                GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(if (activeHex != null) Color.parseColor(activeHex) else Color.parseColor("#73000000"))
                    if (activeHex == null) {
                        setStroke(dp(1).coerceAtLeast(1), Color.parseColor("#3DFFFFFF"))
                    }
                },
                GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(Color.WHITE)
                },
            )
            addView(
                ImageView(this@MainActivity).apply {
                    setImageResource(iconRes)
                    scaleType = ImageView.ScaleType.FIT_CENTER
                    setColorFilter(Color.WHITE)
                    setPadding(pad, pad, pad, pad)
                },
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                ),
            )
            setOnClickListener {
                buzz(Buzz.CLICK)
                onClick()
            }
        }
    }

    /** Curved glowing status text hugging the top circular bezel rim (just like "Connecting..."
     *  in the original Muse Wear OS app). */
    inner class CurvedTopTextView(
        context: Context,
        private val label: String,
        glowHex: String,
    ) : View(context) {
        private val textPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = Color.WHITE
            textSize = 14f * resources.displayMetrics.scaledDensity
            typeface = Typeface.create(Typeface.SANS_SERIF, Typeface.BOLD)
            textAlign = Paint.Align.CENTER
            setShadowLayer(12f, 0f, 0f, Color.parseColor(glowHex))
        }
        private val arcPath = Path()

        override fun onDraw(canvas: Canvas) {
            if (label.isBlank()) return
            val w = width.toFloat()
            val h = height.toFloat()
            val inset = dp(24).toFloat()
            val rect = RectF(inset, inset, w - inset, h - inset)
            arcPath.reset()
            arcPath.addArc(rect, 210f, 120f)
            canvas.drawTextOnPath(label, arcPath, 0f, 0f, textPaint)
        }
    }

    /** Glowing cyan curved volume arc along the LEFT bezel (135°..225°) when turning the watch
     *  bezel during a live call — matching the exact original Muse watch OS visual. */
    inner class VolumeArcView(context: Context) : View(context) {
        private var level = 0.6f
        private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor("#33FFFFFF")
            strokeWidth = dp(5).toFloat()
            strokeCap = Paint.Cap.ROUND
        }
        private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor("#38BDF8")
            strokeWidth = dp(5).toFloat()
            strokeCap = Paint.Cap.ROUND
            setShadowLayer(10f, 0f, 0f, Color.parseColor("#0284C7"))
        }

        fun setLevel(fraction: Float) {
            level = fraction.coerceIn(0f, 1f)
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            val w = width.toFloat()
            val h = height.toFloat()
            val inset = dp(9).toFloat()
            val rect = RectF(inset, inset, w - inset, h - inset)
            // Left bezel arc: bottom-left (142°) clockwise up to top-left (218°), total 76°
            val totalSweep = 76f
            canvas.drawArc(rect, 142f, totalSweep, false, trackPaint)
            val activeSweep = (totalSweep * level).coerceAtLeast(4f)
            canvas.drawArc(rect, 142f, activeSweep, false, fillPaint)
        }
    }

    /** Sleek curved right-edge scrollbar arc along the right bezel during Chat & lists
     *  (matching the original Muse watch OS video). */
    inner class BezelScrollbarView(context: Context) : View(context) {
        private var fraction = 0f
        private var thumbFraction = 0.28f
        private val trackPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor("#24FFFFFF")
            strokeWidth = dp(3).toFloat()
            strokeCap = Paint.Cap.ROUND
        }
        private val thumbPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor("#CC93C5FD")
            strokeWidth = dp(3.5f.roundToInt()).toFloat()
            strokeCap = Paint.Cap.ROUND
        }

        fun updateScroll(scrollFraction: Float, thumbSize: Float) {
            fraction = scrollFraction.coerceIn(0f, 1f)
            thumbFraction = thumbSize.coerceIn(0.14f, 0.6f)
            invalidate()
        }

        override fun onDraw(canvas: Canvas) {
            val w = width.toFloat()
            val h = height.toFloat()
            val inset = dp(7).toFloat()
            val rect = RectF(inset, inset, w - inset, h - inset)
            // Right bezel arc: -34° to +34° (68° total)
            val startDeg = -34f
            val totalSweep = 68f
            canvas.drawArc(rect, startDeg, totalSweep, false, trackPaint)
            val thumbSweep = totalSweep * thumbFraction
            val thumbStart = startDeg + (totalSweep - thumbSweep) * fraction
            canvas.drawArc(rect, thumbStart, thumbSweep, false, thumbPaint)
        }
    }

    /** The busy arc: a blue arc sweeping around the round screen while the agent connects or works. */
    inner class PulseArcView(context: Context) : View(context) {
        private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            style = Paint.Style.STROKE
            color = Color.parseColor("#0168FF")
            strokeWidth = dp(5).toFloat()
            strokeCap = Paint.Cap.ROUND
        }
        private var angle = 0f
        private val animator = ValueAnimator.ofFloat(0f, 360f).apply {
            duration = 1600
            repeatCount = ValueAnimator.INFINITE
            interpolator = LinearInterpolator()
            addUpdateListener { anim ->
                angle = anim.animatedValue as Float
                invalidate()
            }
        }

        override fun onAttachedToWindow() {
            super.onAttachedToWindow()
            animator.start()
        }

        override fun onDetachedFromWindow() {
            animator.cancel()
            super.onDetachedFromWindow()
        }

        override fun onDraw(canvas: Canvas) {
            val w = width.toFloat()
            val h = height.toFloat()
            val r = minOf(w, h) / 2f - paint.strokeWidth
            val cx = w / 2f
            val cy = h / 2f
            canvas.drawArc(RectF(cx - r, cy - r, cx + r, cy + r), 150f + angle, 110f, false, paint)
        }
    }

    // ---- Polished Wear View Builders ----

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).roundToInt()

    private fun marginBottomParams(bottom: Int) = LinearLayout.LayoutParams(
        ViewGroup.LayoutParams.MATCH_PARENT,
        ViewGroup.LayoutParams.WRAP_CONTENT,
    ).apply { bottomMargin = bottom }

    private fun roundRect(hex: String, radiusDp: Int) = GradientDrawable().apply {
        setColor(Color.parseColor(hex))
        cornerRadius = dp(radiusDp).toFloat()
    }

    private fun outlinedRect(bgHex: String, strokeHex: String, radiusDp: Int) = GradientDrawable().apply {
        setColor(Color.parseColor(bgHex))
        setStroke(dp(1).coerceAtLeast(1), Color.parseColor(strokeHex))
        cornerRadius = dp(radiusDp).toFloat()
    }

    private fun rippleBackground(bgHex: String, strokeHex: String?, radiusDp: Int): RippleDrawable {
        val content = if (strokeHex != null) outlinedRect(bgHex, strokeHex, radiusDp) else roundRect(bgHex, radiusDp)
        val mask = roundRect("#FFFFFF", radiusDp)
        return RippleDrawable(ColorStateList.valueOf(Color.parseColor("#33FFFFFF")), content, mask)
    }

    private fun brandHeader(name: String, statusLine: String, dotHex: String): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            layoutParams = marginBottomParams(dp(10))

            val row = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
            }
            val badge = FrameLayout(this@MainActivity).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(Color.WHITE)
                }
                val lp = LinearLayout.LayoutParams(dp(24), dp(24)).apply { marginEnd = dp(6) }
                layoutParams = lp
                addView(
                    ImageView(this@MainActivity).apply {
                        setImageResource(R.drawable.ic_launcher_foreground_nm)
                        scaleType = ImageView.ScaleType.FIT_CENTER
                        setPadding(dp(2), dp(2), dp(2), dp(2))
                    },
                    FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
                )
            }
            val title = TextView(this@MainActivity).apply {
                text = name
                textSize = 15.5f
                setTypeface(null, Typeface.BOLD)
                setTextColor(Color.WHITE)
                maxLines = 1
                ellipsize = TextUtils.TruncateAt.END
            }
            row.addView(badge)
            row.addView(title)
            addView(row)

            val chip = LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER
                layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                    topMargin = dp(3)
                }
                val dot = View(this@MainActivity).apply {
                    background = GradientDrawable().apply {
                        shape = GradientDrawable.OVAL
                        setColor(Color.parseColor(dotHex))
                    }
                    layoutParams = LinearLayout.LayoutParams(dp(6), dp(6)).apply { marginEnd = dp(5) }
                }
                val lbl = TextView(this@MainActivity).apply {
                    text = statusLine
                    textSize = 11.5f
                    setTextColor(Color.parseColor("#A1A1AA"))
                }
                addView(dot)
                addView(lbl)
            }
            addView(chip)
        }
    }

    private fun pulsingOrb(label: String, active: Boolean, accentHex: String): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_HORIZONTAL
            layoutParams = marginBottomParams(dp(8))

            val container = FrameLayout(this@MainActivity).apply {
                layoutParams = LinearLayout.LayoutParams(dp(68), dp(68))
            }
            val halo = View(this@MainActivity).apply {
                background = GradientDrawable().apply {
                    shape = GradientDrawable.OVAL
                    setColor(Color.parseColor(accentHex))
                }
                alpha = if (active) 0.28f else 0.14f
            }
            val core = FrameLayout(this@MainActivity).apply {
                background = outlinedRect(accentHex, "#93C5FD", 26)
                val coreSize = dp(46)
                val lp = FrameLayout.LayoutParams(coreSize, coreSize, Gravity.CENTER)
                layoutParams = lp
                addView(
                    ImageView(this@MainActivity).apply {
                        setImageResource(R.drawable.ic_stat_nanomuse)
                        setColorFilter(Color.WHITE)
                        setPadding(dp(11), dp(11), dp(11), dp(11))
                    },
                    FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
                )
            }
            container.addView(halo, FrameLayout.LayoutParams(dp(64), dp(64), Gravity.CENTER))
            container.addView(core)
            addView(container)

            addView(
                TextView(this@MainActivity).apply {
                    text = label
                    textSize = 14f
                    setTypeface(null, Typeface.BOLD)
                    setTextColor(Color.WHITE)
                    gravity = Gravity.CENTER
                    layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                        topMargin = dp(4)
                    }
                },
            )

            if (active) {
                val sx = ObjectAnimator.ofFloat(halo, View.SCALE_X, 0.88f, 1.15f).apply {
                    repeatCount = ValueAnimator.INFINITE
                    repeatMode = ValueAnimator.REVERSE
                    duration = 950L
                }
                val sy = ObjectAnimator.ofFloat(halo, View.SCALE_Y, 0.88f, 1.15f).apply {
                    repeatCount = ValueAnimator.INFINITE
                    repeatMode = ValueAnimator.REVERSE
                    duration = 950L
                }
                val al = ObjectAnimator.ofFloat(halo, View.ALPHA, 0.18f, 0.42f).apply {
                    repeatCount = ValueAnimator.INFINITE
                    repeatMode = ValueAnimator.REVERSE
                    duration = 950L
                }
                haloAnim = AnimatorSet().apply {
                    interpolator = AccelerateDecelerateInterpolator()
                    playTogether(sx, sy, al)
                    start()
                }
            }
        }
    }

    private fun titleText(text: String) = TextView(this).apply {
        this.text = text
        textSize = 16.5f
        setTypeface(null, Typeface.BOLD)
        setTextColor(Color.WHITE)
        gravity = Gravity.CENTER
    }.also { it.layoutParams = marginBottomParams(dp(2)) }

    private fun subText(text: String) = TextView(this).apply {
        this.text = text
        textSize = 12f
        setTextColor(Color.parseColor("#A1A1AA"))
        gravity = Gravity.CENTER
    }.also { it.layoutParams = marginBottomParams(dp(8)) }

    private fun sectionLabel(text: String) = TextView(this).apply {
        this.text = text.uppercase()
        textSize = 10.5f
        setTypeface(null, Typeface.BOLD)
        setTextColor(Color.parseColor("#71717A"))
        letterSpacing = 0.06f
        setPadding(dp(2), dp(2), dp(2), 0)
    }.also { it.layoutParams = marginBottomParams(dp(4)) }

    private fun noticeCard(text: String, bgHex: String, fgHex: String, strokeHex: String? = null) = TextView(this).apply {
        this.text = text
        textSize = 12.5f
        setTextColor(Color.parseColor(fgHex))
        background = if (strokeHex != null) outlinedRect(bgHex, strokeHex, 16) else roundRect(bgHex, 16)
        setPadding(dp(12), dp(9), dp(12), dp(9))
    }.also { it.layoutParams = marginBottomParams(dp(7)) }

    private fun bubbleView(text: String, isUser: Boolean, reactions: List<String> = emptyList()): LinearLayout {
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = if (isUser) Gravity.END else Gravity.START
            layoutParams = marginBottomParams(dp(7))
            val tv = TextView(this@MainActivity).apply {
                this.text = text
                textSize = 12.5f
                setTextColor(Color.WHITE)
                background = if (isUser) {
                    roundRect("#E87A24", 20)
                } else {
                    roundRect("#E6495596", 20)
                }
                setPadding(dp(12), dp(8), dp(12), dp(8))
                maxWidth = (resources.displayMetrics.widthPixels * 0.76f).roundToInt()
            }
            addView(tv)
            if (reactions.isNotEmpty()) {
                addView(
                    TextView(this@MainActivity).apply {
                        this.text = reactions.joinToString(" ")
                        textSize = 12f
                        setTextColor(Color.WHITE)
                        background = roundRect("#99181A24", 12)
                        setPadding(dp(7), dp(2), dp(7), dp(2))
                        layoutParams = LinearLayout.LayoutParams(
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                            ViewGroup.LayoutParams.WRAP_CONTENT,
                        ).apply { topMargin = dp(2) }
                    },
                )
            }
        }
    }

    private fun pillButton(
        label: String,
        bgHex: String,
        fgHex: String = "#FFFFFF",
        strokeHex: String? = null,
        onClick: () -> Unit,
    ) = Button(this).apply {
        text = label
        isAllCaps = false
        textSize = 13f
        setTypeface(null, Typeface.BOLD)
        setTextColor(Color.parseColor(fgHex))
        background = rippleBackground(bgHex, strokeHex, 24)
        setPadding(dp(12), dp(9), dp(12), dp(9))
        minimumHeight = dp(40)
        stateListAnimator = null
        elevation = 0f
        setOnClickListener { onClick() }
    }.also { it.layoutParams = marginBottomParams(dp(7)) }

    private fun inputBox(hint: String, initial: String) = EditText(this).apply {
        this.hint = hint
        setText(initial)
        setHintTextColor(Color.parseColor("#71717A"))
        setTextColor(Color.WHITE)
        textSize = 13f
        background = outlinedRect("#121216", "#2A2A32", 14)
        setPadding(dp(11), dp(9), dp(11), dp(9))
        isSingleLine = true
    }.also { it.layoutParams = marginBottomParams(dp(8)) }
}
