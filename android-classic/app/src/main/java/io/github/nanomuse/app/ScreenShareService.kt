package io.github.nanomuse.app

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.util.Base64
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicReference

/**
 * Foreground service hosting an Android MediaProjection session for Live Call screen sharing.
 *
 * Compliant with Android 14–17: starts foreground with `FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION`
 * before acquiring [MediaProjection] and registers [MediaProjection.Callback] before creating
 * the [VirtualDisplay].
 */
class ScreenShareService : Service() {
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    private var handlerThread: HandlerThread? = null
    private var captureHandler: Handler? = null

    private val projectionCallback = object : MediaProjection.Callback() {
        override fun onStop() {
            Log.i(TAG, "MediaProjection stopped by system/user")
            teardown()
            stopSelf()
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            teardown()
            stopSelf()
            return START_NOT_STICKY
        }

        val resultCode = intent?.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED) ?: Activity.RESULT_CANCELED
        val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
            intent?.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent?.getParcelableExtra(EXTRA_RESULT_DATA)
        }
        if (resultCode != Activity.RESULT_OK || data == null) {
            Log.w(TAG, "Missing MediaProjection grant; stopping service")
            stopSelf()
            return START_NOT_STICKY
        }

        ensureChannel()
        val notif = buildNotification()
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
            } else {
                startForeground(NOTIF_ID, notif)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start MediaProjection foreground service", e)
            onStoppedListener?.invoke("Could not start screen sharing service")
            stopSelf()
            return START_NOT_STICKY
        }

        try {
            teardown()
            val mgr = getSystemService(Context.MEDIA_PROJECTION_SERVICE) as MediaProjectionManager
            val proj = mgr.getMediaProjection(resultCode, data)
            if (proj == null) {
                onStoppedListener?.invoke("Screen share permission rejected")
                stopSelf()
                return START_NOT_STICKY
            }
            val ht = HandlerThread("MiniMuseScreenShare").apply { start() }
            val handler = Handler(ht.looper)
            handlerThread = ht
            captureHandler = handler
            projection = proj
            proj.registerCallback(projectionCallback, handler)

            val dm = resources.displayMetrics
            val rawW = dm.widthPixels.coerceAtLeast(360)
            val rawH = dm.heightPixels.coerceAtLeast(360)
            val maxDim = 720f
            val scale = minOf(1f, maxDim / maxOf(rawW, rawH).toFloat())
            val capW = ((rawW * scale).toInt() / 2) * 2
            val capH = ((rawH * scale).toInt() / 2) * 2
            val w = capW.coerceAtLeast(240)
            val h = capH.coerceAtLeast(240)

            val reader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2)
            imageReader = reader
            reader.setOnImageAvailableListener({ r ->
                val img = try {
                    r.acquireLatestImage()
                } catch (_: Exception) {
                    null
                } ?: return@setOnImageAvailableListener
                try {
                    val plane = img.planes.firstOrNull() ?: return@setOnImageAvailableListener
                    val buffer = plane.buffer ?: return@setOnImageAvailableListener
                    val pixelStride = plane.pixelStride
                    val rowStride = plane.rowStride
                    val rowPadding = rowStride - pixelStride * img.width
                    val rawBmp = Bitmap.createBitmap(
                        img.width + rowPadding / pixelStride,
                        img.height,
                        Bitmap.Config.ARGB_8888,
                    )
                    rawBmp.copyPixelsFromBuffer(buffer)
                    val cropped = if (rawBmp.width != img.width) {
                        Bitmap.createBitmap(rawBmp, 0, 0, img.width, img.height).also { rawBmp.recycle() }
                    } else {
                        rawBmp
                    }
                    val baos = ByteArrayOutputStream()
                    cropped.compress(Bitmap.CompressFormat.JPEG, 68, baos)
                    cropped.recycle()
                    latestJpegRef.set(Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP))
                } catch (e: Exception) {
                    Log.w(TAG, "Frame encode skipped", e)
                } finally {
                    img.close()
                }
            }, handler)

            virtualDisplay = proj.createVirtualDisplay(
                "MiniMuseLiveCall",
                w,
                h,
                dm.densityDpi,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.surface,
                null,
                handler,
            )
            running = true
            onStartedListener?.invoke()
        } catch (e: Exception) {
            Log.e(TAG, "MediaProjection setup failed", e)
            teardown()
            onStoppedListener?.invoke(e.message ?: "Screen sharing failed")
            stopSelf()
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        teardown()
        super.onDestroy()
    }

    private fun teardown() {
        val wasRunning = running
        running = false
        latestJpegRef.set(null)
        runCatching { virtualDisplay?.release() }
        virtualDisplay = null
        runCatching { imageReader?.setOnImageAvailableListener(null, null); imageReader?.close() }
        imageReader = null
        runCatching { projection?.unregisterCallback(projectionCallback); projection?.stop() }
        projection = null
        runCatching { handlerThread?.quitSafely() }
        handlerThread = null
        captureHandler = null
        if (wasRunning) {
            onStoppedListener?.invoke(null)
        }
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL_ID) == null) {
            val ch = NotificationChannel(
                CHANNEL_ID,
                "Live Call Screen Sharing",
                NotificationManager.IMPORTANCE_LOW,
            ).apply {
                description = "Active while sharing your screen during a MiniMuse live call"
                setShowBadge(false)
            }
            nm.createNotificationChannel(ch)
        }
    }

    private fun buildNotification(): Notification {
        val openIntent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this,
            0,
            openIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("MiniMuse Live Call")
            .setContentText("Sharing your screen with MiniMuse")
            .setOngoing(true)
            .setSilent(true)
            .setContentIntent(pi)
            .build()
    }

    companion object {
        private const val TAG = "ScreenShareService"
        private const val CHANNEL_ID = "minimuse_screen_share"
        private const val NOTIF_ID = 4192
        const val ACTION_START = "io.github.nanomuse.app.START_SCREEN_SHARE"
        const val ACTION_STOP = "io.github.nanomuse.app.STOP_SCREEN_SHARE"
        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"

        @Volatile
        var running: Boolean = false
            private set

        private val latestJpegRef = AtomicReference<String?>(null)

        @Volatile
        var onStartedListener: (() -> Unit)? = null

        @Volatile
        var onStoppedListener: ((String?) -> Unit)? = null

        fun captureLatestJpeg(): String? = latestJpegRef.get()

        fun start(context: Context, resultCode: Int, data: Intent) {
            val intent = Intent(context, ScreenShareService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_RESULT_CODE, resultCode)
                putExtra(EXTRA_RESULT_DATA, data)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            if (!running) return
            val intent = Intent(context, ScreenShareService::class.java).apply {
                action = ACTION_STOP
            }
            runCatching { context.startService(intent) }
        }
    }
}
