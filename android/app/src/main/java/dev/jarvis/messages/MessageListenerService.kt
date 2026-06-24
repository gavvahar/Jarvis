package dev.jarvis.messages

import android.app.Notification
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class MessageListenerService : NotificationListenerService() {

    // Deduplicate: track last 64 (sender+text) hashes to avoid double-posting
    // when an app updates an existing notification.
    private val recentHashes = ArrayDeque<Int>(64)

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        if (sbn.packageName !in MESSAGING_PACKAGES) return

        val extras = sbn.notification.extras ?: return
        val sender = extras.getString(Notification.EXTRA_TITLE) ?: return
        val text = extras.getCharSequence(Notification.EXTRA_TEXT)?.toString() ?: return
        if (text.isBlank()) return

        val hash = (sender + text).hashCode()
        synchronized(recentHashes) {
            if (hash in recentHashes) return
            if (recentHashes.size >= 64) recentHashes.removeFirst()
            recentHashes.addLast(hash)
        }

        val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val url = prefs.getString(KEY_URL, "").orEmpty().trim()
        val token = prefs.getString(KEY_TOKEN, "").orEmpty().trim()
        if (url.isBlank() || token.isBlank()) return

        postToJarvis(url, token, sender, text)
    }

    private fun postToJarvis(url: String, token: String, sender: String, text: String) {
        Thread {
            try {
                val body = JSONObject().apply {
                    put("sender", sender)
                    put("text", text)
                }.toString().toByteArray(Charsets.UTF_8)

                val conn = URL(url).openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("Authorization", "Bearer $token")
                conn.doOutput = true
                conn.connectTimeout = 10_000
                conn.readTimeout = 10_000
                conn.outputStream.use { it.write(body) }
                conn.responseCode // flush and execute
                conn.disconnect()
            } catch (_: Exception) {
                // Silent fail — transient network errors don't need UI feedback
            }
        }.start()
    }

    companion object {
        const val PREFS = "jarvis"
        const val KEY_URL = "webhook_url"
        const val KEY_TOKEN = "webhook_token"

        val MESSAGING_PACKAGES = setOf(
            "com.google.android.apps.messaging", // Google Messages (RCS + SMS)
            "com.samsung.android.messaging",     // Samsung Messages
            "com.android.mms",                   // AOSP MMS
            "com.android.messaging",             // AOSP Messaging
            "org.thoughtcrime.securesms",        // Signal
            "com.whatsapp",                      // WhatsApp
            "com.whatsapp.w4b",                  // WhatsApp Business
            "org.telegram.messenger",            // Telegram
            "com.facebook.orca",                 // Messenger
        )
    }
}
