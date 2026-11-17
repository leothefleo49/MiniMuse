package io.github.nanomuse.wear.assist

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService
import io.github.nanomuse.wear.MainActivity

/**
 * MiniMuse as the watch's assistant. Wear OS lets a watch choose its assistant the same way a phone
 * does: an app with an `ASSIST` activity and a voice-interaction service is offered in the watch's
 * settings, and the side button or the assist gesture then opens it.
 */
object Assist {
    fun open(context: Context): Intent =
        Intent(context, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
}

class AssistActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        startActivity(Assist.open(this))
        finish()
    }
}

class AssistInteractionService : VoiceInteractionService()

class AssistSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = AssistSession(this)
}

class AssistSession(context: Context) : VoiceInteractionSession(context) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        startAssistantActivity(Assist.open(context))
        finish()
    }
}
