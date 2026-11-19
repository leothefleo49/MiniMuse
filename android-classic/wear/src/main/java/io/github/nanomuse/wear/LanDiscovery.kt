package io.github.nanomuse.wear

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.HttpURLConnection
import java.net.Inet4Address
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.URL

data class DiscoveredServer(
    val id: String,
    val name: String,
    val url: String,
    val version: String,
)

object LanDiscovery {
    const val BEACON_PORT = 48787
    const val BEACON_TYPE = "nanomuse_peer"

    fun parsePacket(rawJson: String, senderIp: String? = null): DiscoveredServer? {
        return try {
            val obj = JSONObject(rawJson)
            if (obj.optString("type") != BEACON_TYPE) return null
            val urls = obj.optJSONArray("urls")
            var chosen = ""
            if (urls != null) {
                for (i in 0 until urls.length()) {
                    val u = urls.optString(i).trim()
                    if (u.startsWith("http://") || u.startsWith("https://")) {
                        chosen = u.trimEnd('/')
                        break
                    }
                }
            }
            if (chosen.isBlank() && !senderIp.isNullOrBlank()) {
                val port = obj.optInt("port", 8787)
                chosen = "http://$senderIp:$port"
            }
            if (chosen.isBlank()) return null
            DiscoveredServer(
                id = obj.optString("id").ifBlank { chosen },
                name = obj.optString("name").ifBlank { "MiniMuse" },
                url = chosen,
                version = obj.optString("version"),
            )
        } catch (_: Exception) {
            null
        }
    }

    private fun localIpv4Addresses(): List<String> {
        val out = mutableListOf<String>()
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return out
            while (ifaces.hasMoreElements()) {
                val nif = ifaces.nextElement()
                if (!nif.isUp || nif.isLoopback) continue
                val addrs = nif.inetAddresses
                while (addrs.hasMoreElements()) {
                    val addr = addrs.nextElement()
                    if (addr is Inet4Address && !addr.isLoopbackAddress) {
                        val ip = addr.hostAddress ?: continue
                        if (!ip.startsWith("127.") && !ip.startsWith("169.254.")) {
                            out.add(ip)
                        }
                    }
                }
            }
        } catch (_: Exception) {
        }
        return out
    }

    private suspend fun activeSubnetScan(port: Int = 8787): List<DiscoveredServer> = coroutineScope {
        val ownIps = localIpv4Addresses()
        val prefixes = LinkedHashSet<String>()
        for (ip in ownIps) {
            val parts = ip.split('.')
            if (parts.size == 4) prefixes.add("${parts[0]}.${parts[1]}.${parts[2]}")
        }
        val candidates = mutableListOf<String>()
        for (prefix in prefixes) {
            for (host in 1..254) {
                val ip = "$prefix.$host"
                if (ip !in ownIps) candidates.add(ip)
            }
        }
        if (candidates.isEmpty()) return@coroutineScope emptyList()
        val gate = Semaphore(36)
        candidates.map { ip ->
            async(Dispatchers.IO) {
                gate.withPermit {
                    try {
                        val base = "http://$ip:$port"
                        val conn = (URL("$base/v1/sync/hello").openConnection() as HttpURLConnection).apply {
                            connectTimeout = 450
                            readTimeout = 700
                            requestMethod = "GET"
                        }
                        try {
                            if (conn.responseCode == 200) {
                                val text = conn.inputStream.bufferedReader().use { it.readText() }
                                val obj = JSONObject(text)
                                val id = obj.optString("id").trim()
                                val name = obj.optString("name", "MiniMuse").ifBlank { "MiniMuse" }
                                val ver = obj.optString("version")
                                if (id.isNotEmpty() || obj.has("device_kind")) {
                                    DiscoveredServer(id = id.ifBlank { base }, name = name, url = base, version = ver)
                                } else null
                            } else null
                        } finally {
                            conn.disconnect()
                        }
                    } catch (_: Exception) {
                        null
                    }
                }
            }
        }.awaitAll().filterNotNull()
    }

    suspend fun scan(context: Context, timeoutMs: Long = 3500L): List<DiscoveredServer> = withContext(Dispatchers.IO) {
        val found = LinkedHashMap<String, DiscoveredServer>()
        val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val lock = try { wifi?.createMulticastLock("minimuse_wear_discovery") } catch (_: Exception) { null }
        try {
            lock?.setReferenceCounted(false)
            lock?.acquire()
        } catch (_: Exception) {
        }
        var socket: DatagramSocket? = null
        try {
            socket = DatagramSocket(null).apply {
                reuseAddress = true
                broadcast = true
                soTimeout = 450
                bind(InetSocketAddress(BEACON_PORT))
            }
            val deadline = System.currentTimeMillis() + minOf(timeoutMs, 1800L)
            val buf = ByteArray(4096)
            while (System.currentTimeMillis() < deadline) {
                val pkt = DatagramPacket(buf, buf.size)
                try {
                    socket.receive(pkt)
                    val text = String(pkt.data, pkt.offset, pkt.length, Charsets.UTF_8)
                    val parsed = parsePacket(text, pkt.address?.hostAddress)
                    if (parsed != null) found[parsed.url] = parsed
                } catch (_: Exception) {
                    // socket read slice timeout — keep scanning until deadline
                }
            }
        } catch (_: Exception) {
        } finally {
            try { socket?.close() } catch (_: Exception) {}
            try { if (lock?.isHeld == true) lock.release() } catch (_: Exception) {}
        }
        if (found.isEmpty()) {
            // Many routers/hotspots block UDP broadcasts to watches: fall back to active /24 Wi-Fi probe.
            for (srv in activeSubnetScan()) {
                found[srv.url] = srv
            }
        }
        found.values.toList()
    }
}
