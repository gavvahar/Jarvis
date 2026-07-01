/* ===========================================================
   MEETING RECORDING
   =========================================================== */
import { $, socket } from "./core.js";

const meetingBtn = $("meeting-btn");
const meetingPanel = $("meeting-panel");
const meetingLog = $("meeting-log");
const meetingTimerEl = $("meeting-timer");
const meetingEndBtn = $("meeting-end-btn");
const meetingPanelClose = $("meeting-panel-close");
const meetingNotesModal = $("meeting-notes-modal");
const meetingNotesCard = $("meeting-notes-card");
const meetingNotesContent = $("meeting-notes-content");
const meetingNotesDate = $("meeting-notes-date");
const meetingNotesCopy = $("meeting-notes-copy");
const meetingNotesExport = $("meeting-notes-export");
const meetingNotesTranscriptBtn = $("meeting-notes-transcript-btn");
const meetingNotesClose = $("meeting-notes-close");
const meetingNotesMsg = $("meeting-notes-msg");
const meetingTranscriptWrap = $("meeting-transcript-wrap");
const meetingTranscriptContent = $("meeting-transcript-content");
const meetingStatusLine = $("meeting-status-line");

let _meetingActive = false;
let _meetingStreams = [];
let _meetingAudioCtx = null;
let _meetingTimerInterval = null;
let _meetingStartTime = null;
let _meetingLoopPromise = null;
let _stopCurrentChunk = null;
let _meetingLastNotes = "";
let _meetingLastTranscript = "";
let _meetingPanelMinimised = false;

function _meetingLog(text, cls) {
  if (!meetingLog) return;
  const el = document.createElement("div");
  el.className = cls || "meeting-seg";
  el.textContent = text;
  meetingLog.appendChild(el);
  meetingLog.scrollTop = meetingLog.scrollHeight;
}

function _updateMeetingTimer() {
  if (!meetingTimerEl || !_meetingStartTime) return;
  const s = Math.floor((Date.now() - _meetingStartTime) / 1000);
  const mm = String(Math.floor(s / 60)).padStart(2, "0");
  const ss = String(s % 60).padStart(2, "0");
  meetingTimerEl.textContent = mm + ":" + ss;
}

function _setMeetingStatus(text) {
  if (meetingStatusLine) meetingStatusLine.textContent = text;
}

async function startMeeting() {
  if (_meetingActive) return;

  let micStream;
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: true,
      video: false,
    });
  } catch (e) {
    if (window.__chat)
      window.__chat.addMsg(
        "Microphone access is required to record meetings, sir.",
        "in",
      );
    return;
  }

  // Request system audio via screen share; video required by most browsers
  // — we stop the video track immediately after
  let sysStream = null;
  try {
    sysStream = await navigator.mediaDevices.getDisplayMedia({
      video: { width: 1, height: 1 },
      audio: true,
    });
    sysStream.getVideoTracks().forEach((t) => t.stop());
    if (sysStream.getAudioTracks().length === 0) sysStream = null;
  } catch (e) {
    // User declined or browser doesn't support — mic-only fallback
    sysStream = null;
  }

  // Mix mic + system audio into a single stream
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const dest = ctx.createMediaStreamDestination();
  ctx.createMediaStreamSource(micStream).connect(dest);
  if (sysStream) ctx.createMediaStreamSource(sysStream).connect(dest);

  _meetingActive = true;
  _meetingStreams = [micStream, sysStream].filter(Boolean);
  _meetingAudioCtx = ctx;
  _meetingStartTime = Date.now();
  _meetingTimerInterval = setInterval(_updateMeetingTimer, 1000);

  socket.emit("start_meeting");

  // Show panel
  if (meetingPanel) meetingPanel.classList.remove("meeting-hidden");
  if (meetingLog) meetingLog.innerHTML = "";
  const src = sysStream ? "mic + system audio" : "mic only";
  _setMeetingStatus("Recording (" + src + ")…");
  if (meetingBtn) meetingBtn.classList.add("meeting-live");

  const mime =
    ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"].find((t) =>
      MediaRecorder.isTypeSupported(t),
    ) || "";

  _meetingLoopPromise = _runMeetingChunks(dest.stream, mime);
}

async function _runMeetingChunks(stream, mimeType) {
  const CHUNK_MS = 30000;
  while (_meetingActive) {
    await new Promise((resolve) => {
      const rec = new MediaRecorder(stream, mimeType ? { mimeType } : {});
      const chunks = [];
      rec.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      rec.onstop = async () => {
        if (chunks.length) {
          const blob = new Blob(chunks, { type: rec.mimeType });
          const buf = await blob.arrayBuffer();
          socket.emit("meeting_audio_chunk", buf);
        }
        resolve();
      };
      rec.start();
      _stopCurrentChunk = () => {
        if (rec.state === "recording") rec.stop();
      };
      setTimeout(_stopCurrentChunk, CHUNK_MS);
    });
  }
}

async function endMeeting() {
  if (!_meetingActive) return;
  _meetingActive = false;
  clearInterval(_meetingTimerInterval);

  // Stop the current recorder so the last chunk is sent
  if (_stopCurrentChunk) {
    _stopCurrentChunk();
    _stopCurrentChunk = null;
  }
  // Wait for the chunk loop to exit (last onstop resolves the loop)
  if (_meetingLoopPromise) await _meetingLoopPromise;

  socket.emit("end_meeting");

  // Update UI to "generating notes" state
  _setMeetingStatus("Generating notes…");
  if (meetingBtn) meetingBtn.classList.remove("meeting-live");
  if (meetingEndBtn) meetingEndBtn.disabled = true;

  // Cleanup audio
  _meetingStreams.forEach((s) => s.getTracks().forEach((t) => t.stop()));
  _meetingStreams = [];
  if (_meetingAudioCtx) {
    _meetingAudioCtx.close();
    _meetingAudioCtx = null;
  }
}

// Socket.IO meeting events
socket.on("meeting_started", () => {
  _setMeetingStatus("Listening…");
});

socket.on("meeting_transcript_update", ({ segment }) => {
  if (meetingStatusLine) meetingStatusLine.style.display = "none";
  _meetingLog(segment);
});

socket.on("meeting_notes_ready", ({ notes, transcript }) => {
  _meetingLastNotes = notes || "";
  _meetingLastTranscript = transcript || "";

  // Hide the meeting panel
  if (meetingPanel) meetingPanel.classList.add("meeting-hidden");
  if (meetingEndBtn) meetingEndBtn.disabled = false;

  // Show notes modal
  if (meetingNotesDate) {
    meetingNotesDate.textContent = new Date().toLocaleString();
  }
  if (meetingNotesContent) {
    meetingNotesContent.textContent = notes;
  }
  if (meetingTranscriptContent) {
    meetingTranscriptContent.textContent = transcript;
  }
  if (meetingTranscriptWrap) meetingTranscriptWrap.style.display = "none";
  if (meetingNotesTranscriptBtn)
    meetingNotesTranscriptBtn.textContent = "SHOW TRANSCRIPT";
  if (meetingNotesMsg) {
    meetingNotesMsg.textContent = "";
    meetingNotesMsg.className = "";
  }
  if (meetingNotesModal) meetingNotesModal.classList.remove("setup-hidden");
});

socket.on("meeting_error", ({ error }) => {
  if (window.__chat) window.__chat.addMsg("Meeting: " + error, "in");
});

// Meeting button wires
if (meetingBtn) {
  meetingBtn.addEventListener("click", () => {
    if (_meetingActive) endMeeting();
    else startMeeting();
  });
}
if (meetingEndBtn) {
  meetingEndBtn.addEventListener("click", endMeeting);
}
if (meetingPanelClose) {
  meetingPanelClose.addEventListener("click", () => {
    // Minimise (hide log, keep header)
    _meetingPanelMinimised = !_meetingPanelMinimised;
    if (meetingLog)
      meetingLog.style.display = _meetingPanelMinimised ? "none" : "";
  });
}

// Notes modal buttons
if (meetingNotesCopy) {
  meetingNotesCopy.addEventListener("click", () => {
    navigator.clipboard
      .writeText(_meetingLastNotes)
      .then(() => {
        if (meetingNotesMsg) {
          meetingNotesMsg.className = "ok";
          meetingNotesMsg.textContent = "Copied to clipboard.";
          setTimeout(() => {
            meetingNotesMsg.textContent = "";
          }, 2000);
        }
      })
      .catch(() => {
        if (meetingNotesMsg) {
          meetingNotesMsg.className = "err";
          meetingNotesMsg.textContent = "Clipboard unavailable.";
        }
      });
  });
}
if (meetingNotesExport) {
  meetingNotesExport.addEventListener("click", () => {
    const date = meetingNotesDate
      ? meetingNotesDate.textContent
      : new Date().toLocaleString();
    const slug = new Date().toISOString().slice(0, 16).replace("T", "-");
    const md =
      `# Meeting Notes\n**Date:** ${date}\n\n` +
      _meetingLastNotes +
      (_meetingLastTranscript
        ? `\n\n---\n## Transcript\n\n${_meetingLastTranscript}`
        : "");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([md], { type: "text/markdown" }));
    a.download = `meeting-${slug}.md`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
}
if (meetingNotesTranscriptBtn) {
  meetingNotesTranscriptBtn.addEventListener("click", () => {
    if (!meetingTranscriptWrap) return;
    const show = meetingTranscriptWrap.style.display === "none";
    meetingTranscriptWrap.style.display = show ? "" : "none";
    meetingNotesTranscriptBtn.textContent = show
      ? "HIDE TRANSCRIPT"
      : "SHOW TRANSCRIPT";
  });
}
if (meetingNotesClose) {
  meetingNotesClose.addEventListener("click", () => {
    if (meetingNotesModal) meetingNotesModal.classList.add("setup-hidden");
  });
}
