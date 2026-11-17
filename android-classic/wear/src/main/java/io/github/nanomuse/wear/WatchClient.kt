package io.github.nanomuse.wear

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.concurrent.TimeUnit

data class CallCapabilities(
    val realtimeSupported: Boolean,
    val realtimeKind: String?,
    val chatProvider: String,
    val chatModel: String,
)

data class WatchApproval(
    val id: String,
    val kind: String,
    val tool: String,
    val summary: String,
)

data class WatchMessage(
    val role: String,
    val text: String,
)

data class WatchQuickAction(
    val label: String,
    val prompt: String,
)

data class WatchInfoCard(
    val title: String,
    val body: String,
)

data class WatchState(
    val agentName: String,
    val version: String,
    val busy: Boolean,
    val call: CallCapabilities,
    val approvals: List<WatchApproval>,
    val recent: List<WatchMessage>,
    val actions: List<WatchQuickAction> = emptyList(),
    val cards: List<WatchInfoCard> = emptyList(),
    val serverShellVersion: String = "",
    val wearApkUrl: String = "",
) {
    companion object {
        fun fromJson(obj: JSONObject): WatchState {
            val callObj = obj.optJSONObject("call") ?: JSONObject()
            val caps = CallCapabilities(
                realtimeSupported = callObj.optBoolean("realtime_supported", false),
                realtimeKind = callObj.optString("realtime_kind").takeIf { it.isNotBlank() && it != "null" },
                chatProvider = callObj.optString("chat_provider"),
                chatModel = callObj.optString("chat_model"),
            )
            val approvalsList = mutableListOf<WatchApproval>()
            val pa = obj.optJSONArray("pending_approvals")
            if (pa != null) {
                for (i in 0 until pa.length()) {
                    val item = pa.optJSONObject(i) ?: continue
                    val id = item.optString("id")
                    if (id.isBlank()) continue
                    approvalsList.add(
                        WatchApproval(
                            id = id,
                            kind = "approval",
                            tool = item.optString("tool", "action"),
                            summary = item.optString("summary", "Approval needed"),
                        ),
                    )
                }
            }
            val ph = obj.optJSONArray("pending_holds")
            if (ph != null) {
                for (i in 0 until ph.length()) {
                    val item = ph.optJSONObject(i) ?: continue
                    val id = item.optString("id")
                    if (id.isBlank()) continue
                    approvalsList.add(
                        WatchApproval(
                            id = id,
                            kind = "hold",
                            tool = item.optString("tool", "hold"),
                            summary = item.optString("reason", "Held task waiting"),
                        ),
                    )
                }
            }
            val recentList = mutableListOf<WatchMessage>()
            val rec = obj.optJSONArray("recent")
            if (rec != null) {
                for (i in 0 until rec.length()) {
                    val item = rec.optJSONObject(i) ?: continue
                    val role = item.optString("role")
                        .ifBlank { item.optString("kind") }
                        .ifBlank { "assistant" }
                    val text = item.optString("text")
                        .ifBlank { item.optString("content") }
                        .ifBlank { item.optString("summary") }
                    if (text.isNotBlank()) {
                        recentList.add(WatchMessage(role = role, text = text.trim()))
                    }
                }
            }
            val actionsList = mutableListOf<WatchQuickAction>()
            val actArr = obj.optJSONArray("actions")
            if (actArr != null) {
                for (i in 0 until actArr.length()) {
                    val item = actArr.optJSONObject(i) ?: continue
                    val label = item.optString("label").trim()
                    val prompt = item.optString("prompt").trim()
                    if (label.isNotEmpty() && prompt.isNotEmpty()) {
                        actionsList.add(WatchQuickAction(label = label, prompt = prompt))
                    }
                }
            }
            val cardsList = mutableListOf<WatchInfoCard>()
            val cardArr = obj.optJSONArray("cards")
            if (cardArr != null) {
                for (i in 0 until cardArr.length()) {
                    val item = cardArr.optJSONObject(i) ?: continue
                    val title = item.optString("title").trim()
                    val body = item.optString("body").trim()
                    if (title.isNotEmpty() || body.isNotEmpty()) {
                        cardsList.add(WatchInfoCard(title = title, body = body))
                    }
                }
            }
            val updObj = obj.optJSONObject("update")
            return WatchState(
                agentName = obj.optString("agent_name", "MiniMuse").ifBlank { "MiniMuse" },
                version = obj.optString("version"),
                busy = obj.optBoolean("busy", false),
                call = caps,
                approvals = approvalsList,
                recent = recentList,
                actions = actionsList,
                cards = cardsList,
                serverShellVersion = updObj?.optString("shell_version").orEmpty(),
                wearApkUrl = updObj?.optString("apk_url").orEmpty(),
            )
        }
    }
}

data class WatchUpdateInfo(
    val version: String,
    val apkUrl: String,
    val releaseUrl: String,
    val fromServer: Boolean = false,
    val shellVersion: String? = null,
    val shellRequired: Boolean = false,
)

class WatchClient(private val config: WatchConfig) {
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(25, TimeUnit.SECONDS)
        .writeTimeout(15, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    suspend fun claimCode(rawUrl: String, rawCode: String): Result<String> = withContext(Dispatchers.IO) {
        val baseUrl = WatchConfig.normalizeUrl(rawUrl)
        val code = rawCode.trim()
        if (baseUrl.isBlank()) return@withContext Result.failure(IllegalArgumentException("Enter server address"))
        if (code.length < 4) return@withContext Result.failure(IllegalArgumentException("Enter 6-digit code"))
        try {
            val payload = JSONObject().put("code", code).toString()
            val req = Request.Builder()
                .url("$baseUrl/api/watch/claim")
                .post(payload.toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val msg = extractError(text).ifBlank { "HTTP ${resp.code}" }
                    return@withContext Result.failure(IllegalStateException(msg))
                }
                val obj = JSONObject(text)
                val token = obj.optString("token")
                if (token.isBlank()) {
                    return@withContext Result.failure(IllegalStateException("Missing watch token"))
                }
                val name = obj.optString("name", "MiniMuse").ifBlank { "MiniMuse" }
                config.serverUrl = baseUrl
                config.token = token
                config.agentName = name
                Result.success(name)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /**
     * Pair with the first server that accepts the code: the address typed (if any), the one
     * saved, then every server found on Wi-Fi. Typing a long address on a watch is the hard
     * part, so a blank address is fine as long as something was found.
     */
    suspend fun claimFirst(candidates: List<String>, rawCode: String): Result<String> {
        if (candidates.isEmpty()) {
            return Result.failure(IllegalArgumentException("No server yet: tap Scan Wi-Fi, or type its address"))
        }
        var last: Throwable? = null
        for (url in candidates) {
            val res = claimCode(url, rawCode)
            if (res.isSuccess) return res
            last = res.exceptionOrNull()
        }
        val tried = if (candidates.size == 1) "" else " (tried ${candidates.size} addresses)"
        return Result.failure(IllegalStateException((last?.message ?: "Pairing failed") + tried))
    }
    suspend fun fetchState(): Result<WatchState> = withContext(Dispatchers.IO) {
        val baseUrl = config.serverUrl
        val token = config.token
        if (baseUrl.isBlank() || token.isBlank()) {
            return@withContext Result.failure(IllegalStateException("Not paired"))
        }
        try {
            val req = Request.Builder()
                .url("$baseUrl/api/watch/state")
                .header("Authorization", "Bearer $token")
                .get()
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val msg = extractError(text).ifBlank { "HTTP ${resp.code}" }
                    return@withContext Result.failure(IllegalStateException(msg))
                }
                val parsed = WatchState.fromJson(JSONObject(text))
                config.agentName = parsed.agentName
                Result.success(parsed)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun ask(prompt: String, language: String = Locale.getDefault().toLanguageTag()): Result<Unit> =
        withContext(Dispatchers.IO) {
            val clean = prompt.trim()
            if (clean.isEmpty()) return@withContext Result.failure(IllegalArgumentException("Empty prompt"))
            val payload = JSONObject()
                .put("text", clean)
                .put("language", language)
                .toString()
            try {
                val req = Request.Builder()
                    .url("${config.serverUrl}/api/watch/ask")
                    .header("Authorization", "Bearer ${config.token}")
                    .post(payload.toRequestBody(JSON_MEDIA))
                    .build()
                http.newCall(req).execute().use { resp ->
                    val text = resp.body?.string().orEmpty()
                    if (!resp.isSuccessful) {
                        val msg = extractError(text).ifBlank { "HTTP ${resp.code}" }
                        return@withContext Result.failure(IllegalStateException(msg))
                    }
                    Result.success(Unit)
                }
            } catch (e: Exception) {
                Result.failure(e)
            }
        }

    suspend fun approve(item: WatchApproval, approved: Boolean): Result<Unit> = withContext(Dispatchers.IO) {
        val payload = JSONObject()
            .put("id", item.id)
            .put("approved", approved)
            .put("scope", "once")
            .put("kind", item.kind)
            .toString()
        try {
            val req = Request.Builder()
                .url("${config.serverUrl}/api/watch/approve")
                .header("Authorization", "Bearer ${config.token}")
                .post(payload.toRequestBody(JSON_MEDIA))
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val msg = extractError(text).ifBlank { "HTTP ${resp.code}" }
                    return@withContext Result.failure(IllegalStateException(msg))
                }
                Result.success(Unit)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun checkWearUpdate(currentVersion: String, currentShellVersion: String = currentVersion): Result<WatchUpdateInfo?> = withContext(Dispatchers.IO) {
        // 1. Prefer the paired server first (works tethered/LAN without direct watch GitHub access)
        val baseUrl = config.serverUrl.trim().trimEnd('/')
        val token = config.token.trim()
        if (baseUrl.isNotBlank() && token.isNotBlank()) {
            try {
                val sReq = Request.Builder()
                    .url("$baseUrl/api/watch/version")
                    .header("Authorization", "Bearer $token")
                    .get()
                    .build()
                http.newCall(sReq).execute().use { resp ->
                    if (resp.isSuccessful) {
                        val obj = JSONObject(resp.body?.string().orEmpty())
                        val srvVer = obj.optString("version").trim().trimStart('v')
                        val srvShell = obj.optString("shell_version").trim().takeIf { it.isNotEmpty() }
                        val apkReady = obj.optBoolean("apk_ready", false)
                        val relApk = obj.optString("apk_url").ifBlank { "/api/watch/apk" }
                        val fullApkUrl = if (relApk.startsWith("http")) relApk else "$baseUrl$relApk"
                        if (apkReady && srvVer.isNotBlank() && isNewerVersion(srvVer, currentVersion)) {
                            val reqShell = srvShell != null && isNewerVersion(srvShell, currentShellVersion)
                            return@withContext Result.success(
                                WatchUpdateInfo(
                                    version = srvVer,
                                    apkUrl = fullApkUrl,
                                    releaseUrl = "$baseUrl/",
                                    fromServer = true,
                                    shellVersion = srvShell,
                                    shellRequired = reqShell,
                                ),
                            )
                        }
                    }
                }
            } catch (_: Exception) {
            }
        }
        // 2. Fallback to GitHub Releases directly
        try {
            val req = Request.Builder()
                .url("https://api.github.com/repos/leothefleo49/MiniMuse/releases/latest")
                .header("Accept", "application/vnd.github+json")
                .header("User-Agent", "MiniMuse-Wear/$currentVersion")
                .build()
            http.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException("HTTP ${resp.code}"))
                }
                Result.success(parseLatestWearRelease(text, currentVersion, currentShellVersion))
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    suspend fun downloadApk(
        info: WatchUpdateInfo,
        destFile: java.io.File,
        onProgress: (Int) -> Unit,
    ): Result<java.io.File> = withContext(Dispatchers.IO) {
        try {
            destFile.parentFile?.mkdirs()
            val tmp = java.io.File(destFile.parentFile, destFile.name + ".part")
            if (tmp.exists()) tmp.delete()
            val reqBuilder = Request.Builder().url(info.apkUrl).get()
            if (info.fromServer && config.token.isNotBlank()) {
                reqBuilder.header("Authorization", "Bearer ${config.token}")
            }
            http.newCall(reqBuilder.build()).execute().use { resp ->
                if (!resp.isSuccessful) {
                    return@withContext Result.failure(IllegalStateException("HTTP ${resp.code}"))
                }
                val body = resp.body ?: return@withContext Result.failure(IllegalStateException("Empty APK"))
                val total = body.contentLength()
                var downloaded = 0L
                var lastPct = -1
                body.byteStream().use { input ->
                    tmp.outputStream().use { output ->
                        val buf = ByteArray(16 * 1024)
                        while (true) {
                            val read = input.read(buf)
                            if (read <= 0) break
                            output.write(buf, 0, read)
                            downloaded += read
                            if (total > 0L) {
                                val pct = ((downloaded * 100L) / total).toInt().coerceIn(0, 100)
                                if (pct != lastPct) {
                                    lastPct = pct
                                    onProgress(pct)
                                }
                            }
                        }
                    }
                }
                if (tmp.length() < 50_000L) {
                    tmp.delete()
                    return@withContext Result.failure(IllegalStateException("Incomplete watch APK"))
                }
                if (destFile.exists()) destFile.delete()
                if (!tmp.renameTo(destFile)) {
                    tmp.copyTo(destFile, overwrite = true)
                    tmp.delete()
                }
                Result.success(destFile)
            }
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    fun openCallWebSocket(
        patienceMs: Long,
        onEvent: (JSONObject) -> Unit,
        onClosed: (String?) -> Unit,
    ): WebSocket {
        val wsUrl = toCallWsUrl(config.serverUrl, patienceMs)
        val req = Request.Builder().url(wsUrl).build()
        return http.newWebSocket(
            req,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) {
                    if (config.token.isNotBlank()) {
                        webSocket.send(
                            JSONObject().put("kind", "auth").put("token", config.token).toString(),
                        )
                    }
                }

                override fun onMessage(webSocket: WebSocket, text: String) {
                    try {
                        onEvent(JSONObject(text))
                    } catch (_: Exception) {
                    }
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    onClosed(reason.ifBlank { null })
                }

                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    onClosed(t.message ?: "Connection failed")
                }
            },
        )
    }

    companion object {
        private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()

        /** Addresses to try for a pairing, best first, no duplicates; a bare host also tries the default port. */
        fun pairingCandidates(typed: String, saved: String, discovered: List<String>): List<String> {
            val out = LinkedHashSet<String>()
            fun add(raw: String) {
                val clean = raw.trim()
                if (clean.isEmpty()) return
                val url = WatchConfig.normalizeUrl(clean)
                val hostPart = url.substringAfter("://")
                if (!hostPart.contains(":") && !hostPart.contains("/")) out.add("$url:8787")
                out.add(url)
            }
            add(typed)
            add(saved)
            discovered.forEach { add(it) }
            return out.toList()
        }
        fun toCallWsUrl(serverUrl: String, patienceMs: Long): String {
            val base = WatchConfig.normalizeUrl(serverUrl)
            val wsBase = when {
                base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
                base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
                else -> "ws://$base"
            }
            return "$wsBase/ws/call?thread=main&patience_ms=${patienceMs.coerceIn(1500L, 15000L)}"
        }

        fun extractError(rawBody: String): String {
            return try {
                val obj = JSONObject(rawBody)
                obj.optString("detail").ifBlank { obj.optString("error") }
            } catch (_: Exception) {
                rawBody.take(120)
            }
        }

        fun isNewerVersion(candidate: String, current: String): Boolean {
            val cParts = candidate.trim().trimStart('v').split('.').mapNotNull { it.toIntOrNull() }
            val curParts = current.trim().trimStart('v').split('.').mapNotNull { it.toIntOrNull() }
            val maxLen = maxOf(cParts.size, curParts.size)
            for (i in 0 until maxLen) {
                val a = cParts.getOrElse(i) { 0 }
                val b = curParts.getOrElse(i) { 0 }
                if (a != b) return a > b
            }
            return false
        }

        fun parseLatestWearRelease(
            rawJson: String,
            currentVersion: String,
            currentShellVersion: String = currentVersion,
        ): WatchUpdateInfo? {
            return try {
                val obj = JSONObject(rawJson)
                val ver = obj.optString("tag_name").trim().trimStart('v')
                if (ver.isBlank() || !isNewerVersion(ver, currentVersion)) return null
                val assets: JSONArray = obj.optJSONArray("assets") ?: JSONArray()
                var apkUrl = ""
                for (i in 0 until assets.length()) {
                    val a = assets.optJSONObject(i) ?: continue
                    val name = a.optString("name")
                    val dl = a.optString("browser_download_url")
                    if (name.endsWith(".apk") && name.contains("wear", ignoreCase = true) && dl.isNotBlank()) {
                        apkUrl = dl
                        break
                    }
                }
                if (apkUrl.isBlank()) return null
                val body = obj.optString("body")
                val wearShellRe = Regex("""<!--\s*minimuse-(?:wear-)?shell:\s*([0-9]+(?:\.[0-9]+)+)\s*-->""", RegexOption.IGNORE_CASE)
                val shellVer = wearShellRe.find(body)?.groupValues?.getOrNull(1)
                val shellRequired = shellVer != null && isNewerVersion(shellVer, currentShellVersion)
                WatchUpdateInfo(
                    version = ver,
                    apkUrl = apkUrl,
                    releaseUrl = obj.optString("html_url"),
                    fromServer = false,
                    shellVersion = shellVer,
                    shellRequired = shellRequired,
                )
            } catch (_: Exception) {
                null
            }
        }
    }
}
