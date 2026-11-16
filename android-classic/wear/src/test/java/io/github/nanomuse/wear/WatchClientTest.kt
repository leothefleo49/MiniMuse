package io.github.nanomuse.wear

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WatchClientTest {
    @Test
    fun `toCallWsUrl builds wss or ws endpoint with patience`() {
        assertEquals(
            "ws://192.168.1.42:8787/ws/call?thread=main&patience_ms=4500",
            WatchClient.toCallWsUrl("http://192.168.1.42:8787/", 4500L),
        )
        assertEquals(
            "wss://example.trycloudflare.com/ws/call?thread=main&patience_ms=3500",
            WatchClient.toCallWsUrl("https://example.trycloudflare.com", 3500L),
        )
    }

    @Test
    fun `LanDiscovery parsePacket extracts MiniMuse server from UDP beacon`() {
        val raw = """
            {
              "v": 1,
              "type": "nanomuse_peer",
              "id": "pc-1",
              "name": "Home PC",
              "port": 8787,
              "urls": ["http://192.168.1.25:8787"],
              "version": "1.0.16"
            }
        """.trimIndent()
        val parsed = LanDiscovery.parsePacket(raw)
        assertNotNull(parsed)
        assertEquals("Home PC", parsed!!.name)
        assertEquals("http://192.168.1.25:8787", parsed.url)
        assertEquals("1.0.16", parsed.version)
    }

    @Test
    fun `WatchState fromJson parses approvals holds and recent messages`() {
        val raw = """
            {
              "agent_name": "MiniMuse",
              "version": "1.0.16",
              "busy": true,
              "call": {
                "realtime_supported": true,
                "realtime_kind": "openai",
                "chat_provider": "openai",
                "chat_model": "gpt-4o-realtime-preview"
              },
              "pending_approvals": [
                {"id": "a1", "tool": "shell", "summary": "Run tests"}
              ],
              "pending_holds": [
                {"id": "h1", "tool": "hands", "reason": "Waiting for confirmation"}
              ],
              "recent": [
                {"role": "user", "text": "Hey MiniMuse"},
                {"role": "assistant", "text": "Hello!"}
              ]
            }
        """.trimIndent()
        val st = WatchState.fromJson(JSONObject(raw))
        assertEquals("MiniMuse", st.agentName)
        assertTrue(st.busy)
        assertTrue(st.call.realtimeSupported)
        assertEquals(2, st.approvals.size)
        assertEquals("approval", st.approvals[0].kind)
        assertEquals("hold", st.approvals[1].kind)
        assertEquals(2, st.recent.size)
    }

    @Test
    fun `parseLatestWearRelease picks wear APK only when newer`() {
        val json = """
            {
              "tag_name": "v1.0.17",
              "html_url": "https://github.com/leothefleo49/MiniMuse/releases/tag/v1.0.17",
              "assets": [
                {"name": "MiniMuse-1.0.17-arm64.apk", "browser_download_url": "https://example.com/phone.apk"},
                {"name": "MiniMuse-1.0.17-wear.apk", "browser_download_url": "https://example.com/wear.apk"}
              ]
            }
        """.trimIndent()
        val upd = WatchClient.parseLatestWearRelease(json, "1.0.16")
        assertNotNull(upd)
        assertEquals("https://example.com/wear.apk", upd!!.apkUrl)
        assertNull(WatchClient.parseLatestWearRelease(json, "1.0.17"))
        assertFalse(WatchClient.isNewerVersion("1.0.16", "1.0.16"))
    }
}
