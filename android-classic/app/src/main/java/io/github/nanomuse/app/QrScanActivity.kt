package io.github.nanomuse.app

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.HapticFeedbackConstants
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import com.google.zxing.BarcodeFormat
import com.google.zxing.DecodeHintType
import com.google.zxing.ResultPoint
import com.google.zxing.client.android.Intents
import com.journeyapps.barcodescanner.BarcodeCallback
import com.journeyapps.barcodescanner.BarcodeResult
import com.journeyapps.barcodescanner.DefaultDecoderFactory
import com.journeyapps.barcodescanner.camera.CameraSettings
import io.github.nanomuse.app.databinding.ActivityQrScanBinding

/**
 * Portrait-first, rotation-friendly QR code scanner built on ZXing's [DecoratedBarcodeView] so
 * it never forces the phone into landscape, supports both white-background and dark-terminal
 * inverted QR codes, handles camera permissions gracefully, and respects Android 15+ edge-to-edge
 * insets.
 */
class QrScanActivity : AppCompatActivity() {
    private lateinit var ui: ActivityQrScanBinding
    private var torchOn = false
    private var decoded = false
    private var askedPermissionOnce = false

    private val requestCamera = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted) {
            showScanner()
        } else {
            showPermissionOverlay()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        askedPermissionOnce = savedInstanceState?.getBoolean(STATE_ASKED_PERMISSION) ?: false
        ui = ActivityQrScanBinding.inflate(layoutInflater)
        setContentView(ui.root)

        val topPad = ui.topBar.paddingTop
        val botPad = ui.bottomBar.paddingBottom
        val panelLeft = ui.permissionPanel.paddingLeft
        val panelTop = ui.permissionPanel.paddingTop
        val panelRight = ui.permissionPanel.paddingRight
        val panelBottom = ui.permissionPanel.paddingBottom
        ViewCompat.setOnApplyWindowInsetsListener(ui.qrRoot) { _, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            ui.topBar.updatePadding(top = topPad + bars.top, left = bars.left, right = bars.right)
            ui.bottomBar.updatePadding(bottom = botPad + bars.bottom, left = bars.left, right = bars.right)
            ui.permissionPanel.updatePadding(
                left = panelLeft + bars.left,
                top = panelTop + bars.top,
                right = panelRight + bars.right,
                bottom = panelBottom + bars.bottom,
            )
            insets
        }

        val hasFlash = packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_FLASH)
        ui.qrTorch.visibility = if (hasFlash) View.VISIBLE else View.INVISIBLE
        ui.qrTorch.setOnClickListener {
            torchOn = !torchOn
            if (torchOn) ui.barcodeScanner.setTorchOn() else ui.barcodeScanner.setTorchOff()
            ui.qrTorch.alpha = if (torchOn) 1f else 0.72f
        }
        ui.qrTorch.alpha = 0.72f

        val cancel = View.OnClickListener {
            setResult(Activity.RESULT_CANCELED)
            finish()
        }
        ui.qrClose.setOnClickListener(cancel)
        ui.enterManually.setOnClickListener(cancel)
        ui.permissionManual.setOnClickListener(cancel)

        ui.permissionAction.setOnClickListener {
            if (!ActivityCompat.shouldShowRequestPermissionRationale(this, Manifest.permission.CAMERA) && askedPermissionOnce) {
                startActivity(
                    Intent(
                        Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.fromParts("package", packageName, null),
                    )
                )
            } else {
                askedPermissionOnce = true
                requestCamera.launch(Manifest.permission.CAMERA)
            }
        }

        // Configure QR-only decoding in MIXED_SCAN mode (handles black-on-white AND terminal white-on-black QRs)
        val hints = mapOf(
            DecodeHintType.TRY_HARDER to true,
            DecodeHintType.ALSO_INVERTED to true,
        )
        ui.barcodeScanner.setStatusText("")
        ui.barcodeScanner.statusView?.visibility = View.GONE
        ui.barcodeScanner.barcodeView.decoderFactory = DefaultDecoderFactory(
            listOf(BarcodeFormat.QR_CODE),
            hints,
            null,
            Intents.Scan.MIXED_SCAN,
        )
        val settings = CameraSettings().apply {
            isAutoFocusEnabled = true
            isContinuousFocusEnabled = true
        }
        ui.barcodeScanner.barcodeView.cameraSettings = settings
        ui.barcodeScanner.decodeContinuous(object : BarcodeCallback {
            override fun barcodeResult(result: BarcodeResult) {
                if (decoded) return
                val text = result.text?.trim().orEmpty()
                if (text.isEmpty()) return
                decoded = true
                val haptic = if (Build.VERSION.SDK_INT >= 30) {
                    HapticFeedbackConstants.CONFIRM
                } else {
                    HapticFeedbackConstants.VIRTUAL_KEY
                }
                ui.barcodeScanner.performHapticFeedback(haptic)
                setResult(Activity.RESULT_OK, Intent().putExtra(EXTRA_QR_RESULT, text))
                finish()
            }

            override fun possibleResultPoints(resultPoints: MutableList<ResultPoint>?) = Unit
        })
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean(STATE_ASKED_PERMISSION, askedPermissionOnce)
    }

    override fun onResume() {
        super.onResume()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            showScanner()
        } else if (!askedPermissionOnce) {
            askedPermissionOnce = true
            requestCamera.launch(Manifest.permission.CAMERA)
        } else {
            showPermissionOverlay()
        }
    }

    override fun onPause() {
        if (torchOn) {
            torchOn = false
            runCatching { ui.barcodeScanner.setTorchOff() }
        }
        runCatching { ui.barcodeScanner.pause() }
        super.onPause()
    }

    private fun showScanner() {
        ui.permissionPanel.visibility = View.GONE
        ui.barcodeScanner.visibility = View.VISIBLE
        ui.topBar.visibility = View.VISIBLE
        ui.bottomBar.visibility = View.VISIBLE
        runCatching { ui.barcodeScanner.resume() }
    }

    private fun showPermissionOverlay() {
        runCatching { ui.barcodeScanner.pause() }
        ui.barcodeScanner.visibility = View.GONE
        ui.topBar.visibility = View.GONE
        ui.bottomBar.visibility = View.GONE
        ui.permissionPanel.visibility = View.VISIBLE
        val permanentlyDenied = askedPermissionOnce &&
            !ActivityCompat.shouldShowRequestPermissionRationale(this, Manifest.permission.CAMERA)
        ui.permissionAction.text = getString(
            if (permanentlyDenied) R.string.qr_open_settings else R.string.qr_grant_camera
        )
    }

    companion object {
        const val EXTRA_QR_RESULT = "qr_result"
        private const val STATE_ASKED_PERMISSION = "asked_permission"
    }
}
