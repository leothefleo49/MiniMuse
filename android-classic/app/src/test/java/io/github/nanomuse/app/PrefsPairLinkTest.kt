package io.github.nanomuse.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A pairing code QR (`nanomuse://pair?...`, the one Devices shows under "Show pairing code")
 * carries no access token, so it must route to the pairing claim and never to the
 * server-address parser, which rejects it as "not a server address".
 */
class PrefsPairLinkTest {
    @Test
    fun pairing_qr_payloads_are_recognised() {
        assertTrue(Prefs.isPairLink("nanomuse://pair?code=ABC123&id=dev&name=PC&url=http://192.168.1.5:8787"))
        assertTrue(Prefs.isPairLink("  NANOMUSE://PAIR?code=ABC123  "))
        assertTrue(Prefs.isPairLink("nanomuse-pair:code=ABC123"))
    }

    @Test
    fun server_links_and_junk_are_not_pair_links() {
        assertFalse(Prefs.isPairLink("http://192.168.1.5:8787/#token=xyz"))
        assertFalse(Prefs.isPairLink("192.168.1.5:8787"))
        assertFalse(Prefs.isPairLink(""))
        assertFalse(Prefs.isPairLink("nanomuse://settings"))
    }
}
