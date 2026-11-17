package io.github.nanomuse.app.assist

import android.app.Activity
import android.app.role.RoleManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import io.github.nanomuse.app.MainActivity

/**
 * MiniMuse as the phone's default assistant. Android lists an app under *Default digital assistant
 * app* only when it has both an activity for `ASSIST` and a voice-interaction service; choosing it
 * there makes the home-button hold, the power-button hold or the assist gesture open MiniMuse's
 * main chat instead of the stock assistant.
 */
object Assist {
    /** The main chat, in front. The same intent a notification uses to open a conversation. */
    fun openMainChat(context: Context): Intent =
        Intent(context, MainActivity::class.java)
            .putExtra(MainActivity.EXTRA_THREAD, "main")
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)

    /** "on" when MiniMuse is the chosen assistant, "off" when another one is, "unknown" when Android will not say. */
    fun state(context: Context): String {
        try {
            if (Build.VERSION.SDK_INT >= 29) {
                val roles = context.getSystemService(RoleManager::class.java)
                if (roles != null && roles.isRoleAvailable(RoleManager.ROLE_ASSISTANT)) {
                    return if (roles.isRoleHeld(RoleManager.ROLE_ASSISTANT)) "on" else "off"
                }
            }
            val chosen = Settings.Secure.getString(context.contentResolver, "assistant")
            if (chosen != null) return if (chosen.startsWith(context.packageName + "/")) "on" else "off"
        } catch (_: Exception) {
            // some phones hide the setting from apps
        }
        return "unknown"
    }

    /** Android's own *Default digital assistant app* page: the only place a person can pick one. */
    fun settingsIntent(): Intent = Intent(Settings.ACTION_VOICE_INPUT_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
}

/** The activity Android starts for `ASSIST` and `VOICE_COMMAND`: it opens the main chat and goes away. */
class AssistActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Assist.openMainChat(this))
        finish()
    }
}

/** What makes Android list MiniMuse as an assistant; it has no work of its own to do. */
class AssistInteractionService : VoiceInteractionService()

class AssistSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistSession(this)
}

/** Shown when the assist gesture fires: hand over to the main chat. */
class AssistSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        startAssistantActivity(Assist.openMainChat(context))
        finish()
    }
}
