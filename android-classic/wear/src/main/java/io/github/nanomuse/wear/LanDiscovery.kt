package io.github.nanomuse.wear

import android.content.Context
import android.net.wifi.WifiManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress

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
            val deadline = System.currentTimeMillis() + timeoutMs
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
        found.values.toList()
    }
}
