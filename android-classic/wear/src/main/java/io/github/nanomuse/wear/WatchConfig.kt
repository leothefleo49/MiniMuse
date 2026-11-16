package io.github.nanomuse.wear

import android.content.Context

class WatchConfig(context: Context) {
    private val prefs = context.getSharedPreferences("minimuse_watch", Context.MODE_PRIVATE)

    var serverUrl: String
        get() = prefs.getString("server_url", "") ?: ""
        set(value) { prefs.edit().putString("server_url", normalizeUrl(value)).apply() }

    var token: String
        get() = prefs.getString("token", "") ?: ""
        set(value) { prefs.edit().putString("token", value.trim()).apply() }

    var agentName: String
        get() = prefs.getString("agent_name", "MiniMuse") ?: "MiniMuse"
        set(value) {
            val clean = value.trim().ifBlank { "MiniMuse" }
            prefs.edit().putString("agent_name", clean).apply()
        }

    /** Keep watch screen awake while voice prompt or live call is active. */
    var keepScreenOnDuringVoice: Boolean
        get() = prefs.getBoolean("keep_screen_on", true)
        set(value) { prefs.edit().putBoolean("keep_screen_on", value).apply() }

    /** Silence wait before finalizing hands-free turn (ms). Default 4500ms so user isn't cut off. */
    var patienceMs: Long
        get() = prefs.getLong("patience_ms", 4500L).coerceIn(1500L, 15000L)
        set(value) { prefs.edit().putLong("patience_ms", value.coerceIn(1500L, 15000L)).apply() }

    val isPaired: Boolean
        get() = serverUrl.isNotBlank() && token.isNotBlank()

    fun clearPairing() {
        prefs.edit().remove("token").remove("server_url").apply()
    }

    companion object {
        fun normalizeUrl(raw: String): String {
            var s = raw.trim().trimEnd('/')
            if (s.isNotEmpty() && !s.startsWith("http://") && !s.startsWith("https://")) {
                s = "http://$s"
            }
            return s
        }
    }
}
