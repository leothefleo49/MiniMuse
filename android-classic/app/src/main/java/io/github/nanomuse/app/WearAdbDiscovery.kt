package io.github.nanomuse.app

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Discovers Wear OS watches advertising Wireless Debugging over local Wi-Fi mDNS
 * (`_adb-tls-connect._tcp.` and `_adb-tls-pairing._tcp.`). Runs natively on Android's
 * NsdManager so PRoot Linux environments without mDNS daemons can still find watches.
 */
object WearAdbDiscovery {
    private const val CONNECT_TYPE = "_adb-tls-connect._tcp."
    private const val PAIRING_TYPE = "_adb-tls-pairing._tcp."

    fun discoverJson(context: Context, timeoutMs: Long = 2200L): String {
        val out = JSONObject()
        val connectArr = JSONArray()
        val pairingArr = JSONArray()
        try {
            val appCtx = context.applicationContext
            val nsd = appCtx.getSystemService(Context.NSD_SERVICE) as? NsdManager
            if (nsd == null) {
                out.put("connect", connectArr)
                out.put("pairing", pairingArr)
                return out.toString()
            }
            val wifi = appCtx.getSystemService(Context.WIFI_SERVICE) as? WifiManager
            val lock = try {
                wifi?.createMulticastLock("minimuse-wear-adb")?.apply {
                    setReferenceCounted(true)
                    acquire()
                }
            } catch (_: Exception) {
                null
            }
            try {
                val connectMap = scanType(nsd, CONNECT_TYPE, timeoutMs / 2)
                val pairingMap = scanType(nsd, PAIRING_TYPE, timeoutMs / 2)
                for (item in connectMap.values) connectArr.put(item)
                for (item in pairingMap.values) pairingArr.put(item)
            } finally {
                try {
                    if (lock?.isHeld == true) lock.release()
                } catch (_: Exception) {
                }
            }
        } catch (_: Exception) {
        }
        out.put("connect", connectArr)
        out.put("pairing", pairingArr)
        return out.toString()
    }

    private fun scanType(
        nsd: NsdManager,
        serviceType: String,
        waitMs: Long,
    ): Map<String, JSONObject> {
        val found = ConcurrentHashMap<String, JSONObject>()
        val resolveSem = java.util.concurrent.Semaphore(1)
        val latch = CountDownLatch(1)

        val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(regType: String) {}
            override fun onDiscoveryStopped(serviceType: String) {}
            override fun onStartDiscoveryFailed(serviceType: String, errorCode: Int) {
                latch.countDown()
            }
            override fun onStopDiscoveryFailed(serviceType: String, errorCode: Int) {}
            override fun onServiceLost(serviceInfo: NsdServiceInfo) {}
            override fun onServiceFound(serviceInfo: NsdServiceInfo) {
                try {
                    if (!resolveSem.tryAcquire(600, TimeUnit.MILLISECONDS)) return
                    @Suppress("DEPRECATION")
                    nsd.resolveService(
                        serviceInfo,
                        object : NsdManager.ResolveListener {
                            override fun onResolveFailed(si: NsdServiceInfo, errorCode: Int) {
                                resolveSem.release()
                            }
                            override fun onServiceResolved(resolved: NsdServiceInfo) {
                                try {
                                    @Suppress("DEPRECATION")
                                    val host = resolved.host
                                    val addr = if (host is Inet4Address) {
                                        host.hostAddress
                                    } else {
                                        host?.hostAddress
                                    }
                                    val port = resolved.port
                                    if (!addr.isNullOrBlank() && port > 0 && !addr.contains(":")) {
                                        val hp = "$addr:$port"
                                        val item = JSONObject()
                                            .put("name", resolved.serviceName ?: hp)
                                            .put("host", addr)
                                            .put("port", port)
                                            .put("host_port", hp)
                                        found[hp] = item
                                    }
                                } finally {
                                    resolveSem.release()
                                }
                            }
                        },
                    )
                } catch (_: Exception) {
                    try {
                        resolveSem.release()
                    } catch (_: Exception) {
                    }
                }
            }
        }

        try {
            nsd.discoverServices(serviceType, NsdManager.PROTOCOL_DNS_SD, listener)
            latch.await(waitMs.coerceIn(400L, 2500L), TimeUnit.MILLISECONDS)
        } catch (_: Exception) {
        } finally {
            try {
                nsd.stopServiceDiscovery(listener)
            } catch (_: Exception) {
            }
        }
        return found
    }
}
