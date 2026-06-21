============================================================
  J.A.R.V.I.S.  —  STARTER KIT
  Your own Iron Man-style AI, running on your PC.
============================================================

WHAT THIS IS
------------
A clean, lightweight J.A.R.V.I.S. you talk to. He speaks and listens in
your own browser using your computer's built-in Windows voices, and he
thinks using an AI model of YOUR choice — Claude, ChatGPT, or almost any
other. No fancy graphics card required — this runs on an ordinary PC, or
even one with no dedicated GPU at all.

This is the Starter Kit: it's built for CONVERSATION. He has the full
J.A.R.V.I.S. personality, the holographic interface, the standby screen,
voice in and voice out. (He does not control your PC, browse the web, or
remember past sessions — those live in the bigger builds.)


WHICH AI CAN HE USE?
--------------------
You choose, on the first-run setup screen:
  • Anthropic (Claude) — Haiku (cheapest), Sonnet (most in-character),
                          or Opus (most capable).
  • OpenAI (ChatGPT)   — GPT-4o mini, GPT-4o, GPT-4.1, etc.
  • Other (advanced)   — any OpenAI-compatible service by entering its
                          "base URL": OpenRouter (gives you Claude, Gemini,
                          Llama and hundreds more from one key), Groq,
                          Together, or even a local model via Ollama /
                          LM Studio. Pick the provider "Other", type the
                          model name and the base URL, and paste that
                          service's key.

You only pay the provider you choose, for what you use. Haiku / GPT-4o mini
are the cheapest places to start.


WHAT YOU NEED
-------------
1. A Windows PC.
2. Python 3.10 or newer — start.bat installs this for you automatically if you
   don't already have it (no admin needed). You can also get it yourself at
   https://python.org (during install, TICK "Add Python to PATH").
3. Google Chrome or Microsoft Edge (for the microphone + voice).
4. An API key from whichever provider you picked:
     • Claude:  https://console.anthropic.com/settings/keys   (sk-ant-...)
     • OpenAI:  https://platform.openai.com/api-keys           (sk-...)
     • OpenRouter (for everything else):  https://openrouter.ai/keys


HOW TO START HIM
----------------
   (See the "Getting Started" PDF in this folder for a friendlier walkthrough.)

   TIP: After downloading, right-click the ZIP -> Properties -> tick "Unblock"
   -> Apply, THEN extract. Windows then won't warn about an "unverified
   publisher" when you run start.bat. (If it ever does, it's harmless -
   click  More info  ->  Run anyway.)

1. Double-click  start.bat
   The first time, it installs a few small components automatically
   (this needs an internet connection and takes a minute).
2. Your browser opens to J.A.R.V.I.S.
3. The FIRST time, he'll ask you to pick a provider + model and paste that
   provider's API key. Click CONNECT. He verifies it and remembers your
   choice from then on.
4. Allow microphone access when the browser asks.


HOW TO TALK TO HIM
------------------
- He starts in STANDBY (the dim lock screen).
- Say "JARVIS" to wake him.  (Or press the SPACEBAR.)
- Then just talk — ask him anything.
- Say "standby" or "go to sleep" to send him back to the lock screen.
- Prefer typing? Press the  C  key to open the chat panel and type to him.


CHANGING HIS VOICE
------------------
J.A.R.V.I.S. uses the voices installed in Windows. To add the British
male voice (closest to the films):
  Windows Settings -> Time & Language -> Speech -> Manage voices
  -> Add  ->  "English (United Kingdom)".
He'll automatically prefer a British male voice if one is available.


SETTINGS (optional)
-------------------
After first run, a file called  config.json  appears in this folder. You can
open it in Notepad to change your "provider", "model", "api_key", or
"base_url" by hand — or just restart and use the setup screen again. Example
models: "claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-8",
"gpt-4o-mini", "gpt-4o".


TROUBLESHOOTING
---------------
- "Python was not found"  -> install Python and tick "Add to PATH", then retry.
- The mic doesn't work     -> use Chrome or Edge, and allow microphone access.
                              (Firefox doesn't support browser voice input — you
                              can still type to him with the C key.)
- "Key was rejected"       -> double-check the key, and that your Anthropic
                              account has credit.
- He talks over himself /
  hears himself             -> use headphones, or lower your speaker volume.


Your API key is stored only on your PC (in config.json) and is sent only to
Anthropic to power J.A.R.V.I.S. It is never shared with anyone else.

Enjoy, sir.
