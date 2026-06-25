package dev.jarvis.messages

import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate

class MainActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        AppCompatDelegate.setDefaultNightMode(AppCompatDelegate.MODE_NIGHT_YES)
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        val prefs = getSharedPreferences(MessageListenerService.PREFS, MODE_PRIVATE)
        val urlInput = findViewById<EditText>(R.id.webhook_url)
        val tokenInput = findViewById<EditText>(R.id.webhook_token)
        val saveBtn = findViewById<Button>(R.id.save_btn)
        val grantBtn = findViewById<Button>(R.id.grant_btn)
        val statusText = findViewById<TextView>(R.id.status_text)

        urlInput.setText(prefs.getString(MessageListenerService.KEY_URL, ""))
        tokenInput.setText(prefs.getString(MessageListenerService.KEY_TOKEN, ""))

        saveBtn.setOnClickListener {
            val url = urlInput.text.toString().trim()
            val token = tokenInput.text.toString().trim()
            if (url.isBlank() || token.isBlank()) {
                Toast.makeText(this, "Both fields are required", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            prefs.edit()
                .putString(MessageListenerService.KEY_URL, url)
                .putString(MessageListenerService.KEY_TOKEN, token)
                .apply()
            Toast.makeText(this, "Saved", Toast.LENGTH_SHORT).show()
        }

        grantBtn.setOnClickListener {
            startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
        }

        refreshStatus(statusText, grantBtn)
    }

    override fun onResume() {
        super.onResume()
        refreshStatus(
            findViewById(R.id.status_text),
            findViewById(R.id.grant_btn),
        )
    }

    private fun refreshStatus(statusText: TextView, grantBtn: Button) {
        val granted = isListenerEnabled()
        if (granted) {
            statusText.text = getString(R.string.status_active)
            statusText.setTextColor(0xFF1fb6ef.toInt())
            grantBtn.visibility = View.GONE
        } else {
            statusText.text = getString(R.string.status_inactive)
            statusText.setTextColor(0xFFffb648.toInt())
            grantBtn.visibility = View.VISIBLE
        }
    }

    private fun isListenerEnabled(): Boolean {
        val enabled = Settings.Secure.getString(
            contentResolver,
            "enabled_notification_listeners",
        ) ?: return false
        return enabled.contains(packageName)
    }
}
