import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function makeSocketMock() {
  const handlers = {};
  return {
    on: vi.fn((event, cb) => {
      handlers[event] = cb;
    }),
    emit: vi.fn(),
    trigger: (event, payload) => handlers[event]?.(payload),
  };
}

let socketMock;

vi.mock("../../static/v2/js/app/core.js", () => ({
  $: (id) => document.getElementById(id),
  get socket() {
    return socketMock;
  },
}));

function buildDom() {
  document.body.innerHTML = `
    <button id="meeting-btn"></button>
    <div id="meeting-panel" class="meeting-hidden">
      <div id="meeting-log"></div>
      <span id="meeting-timer"></span>
      <button id="meeting-end-btn"></button>
      <button id="meeting-panel-close"></button>
      <span id="meeting-status-line"></span>
    </div>
    <div id="meeting-notes-modal" class="setup-hidden">
      <div id="meeting-notes-card"></div>
      <div id="meeting-notes-content"></div>
      <span id="meeting-notes-date"></span>
      <button id="meeting-notes-copy"></button>
      <button id="meeting-notes-export"></button>
      <button id="meeting-notes-transcript-btn">SHOW TRANSCRIPT</button>
      <button id="meeting-notes-close"></button>
      <span id="meeting-notes-msg"></span>
      <div id="meeting-transcript-wrap" style="display: none"></div>
      <div id="meeting-transcript-content"></div>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

function flush() {
  return new Promise((r) => setTimeout(r, 10));
}

class MockMediaRecorder {
  constructor(stream, opts) {
    this.stream = stream;
    this.mimeType = (opts && opts.mimeType) || "";
    this.state = "inactive";
  }
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable && this.ondataavailable({ data: { size: 0 } });
    this.onstop && this.onstop();
  }
}
MockMediaRecorder.isTypeSupported = () => true;

function stubMediaApis({ mic = true, displayAudio = true } = {}) {
  const micStream = {
    getTracks: () => [{ stop: vi.fn() }],
  };
  const sysStream = displayAudio
    ? {
        getVideoTracks: () => [{ stop: vi.fn() }],
        getAudioTracks: () => [{}],
        getTracks: () => [{ stop: vi.fn() }],
      }
    : null;

  vi.stubGlobal("navigator", {
    ...global.navigator,
    mediaDevices: {
      getUserMedia: mic
        ? vi.fn().mockResolvedValue(micStream)
        : vi.fn().mockRejectedValue(new Error("denied")),
      getDisplayMedia: sysStream
        ? vi.fn().mockResolvedValue(sysStream)
        : vi.fn().mockRejectedValue(new Error("declined")),
    },
    clipboard: { writeText: vi.fn().mockResolvedValue() },
  });

  class MockAudioContext {
    createMediaStreamDestination() {
      return { stream: {} };
    }
    createMediaStreamSource() {
      return { connect: vi.fn() };
    }
    close() {}
  }
  vi.stubGlobal("AudioContext", MockAudioContext);
  vi.stubGlobal("MediaRecorder", MockMediaRecorder);
}

describe("meeting.js", () => {
  beforeEach(async () => {
    vi.resetModules();
    socketMock = makeSocketMock();
    buildDom();
    window.__chat = { addMsg: vi.fn() };
    await import("../../static/v2/js/app/meeting.js");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete window.__chat;
  });

  describe("socket events", () => {
    it("meeting_started sets the status line", () => {
      socketMock.trigger("meeting_started");
      expect($("meeting-status-line").textContent).toBe("Listening…");
    });

    it("meeting_transcript_update hides the status line and appends a log segment", () => {
      socketMock.trigger("meeting_transcript_update", { segment: "Hello there" });

      expect($("meeting-status-line").style.display).toBe("none");
      expect($("meeting-log").textContent).toContain("Hello there");
      expect($("meeting-log").firstChild.className).toBe("meeting-seg");
    });

    it("meeting_notes_ready hides the panel and shows the notes modal populated", () => {
      $("meeting-panel").classList.remove("meeting-hidden");
      $("meeting-end-btn").disabled = true;

      socketMock.trigger("meeting_notes_ready", {
        notes: "# Summary\nDid stuff.",
        transcript: "raw transcript text",
      });

      expect($("meeting-panel").classList.contains("meeting-hidden")).toBe(true);
      expect($("meeting-end-btn").disabled).toBe(false);
      expect($("meeting-notes-content").textContent).toBe("# Summary\nDid stuff.");
      expect($("meeting-transcript-content").textContent).toBe("raw transcript text");
      expect($("meeting-transcript-wrap").style.display).toBe("none");
      expect($("meeting-notes-transcript-btn").textContent).toBe("SHOW TRANSCRIPT");
      expect($("meeting-notes-modal").classList.contains("setup-hidden")).toBe(false);
    });

    it("meeting_notes_ready defaults empty notes/transcript to blank strings", () => {
      socketMock.trigger("meeting_notes_ready", {});
      expect($("meeting-notes-content").textContent).toBe("");
      expect($("meeting-transcript-content").textContent).toBe("");
    });

    it("meeting_error reports the error to chat", () => {
      socketMock.trigger("meeting_error", { error: "transcription service down" });
      expect(window.__chat.addMsg).toHaveBeenCalledWith(
        "Meeting: transcription service down",
        "in",
      );
    });
  });

  describe("notes modal buttons", () => {
    it("toggles the transcript panel open then closed", () => {
      $("meeting-notes-transcript-btn").click();
      expect($("meeting-transcript-wrap").style.display).toBe("");
      expect($("meeting-notes-transcript-btn").textContent).toBe("HIDE TRANSCRIPT");

      $("meeting-notes-transcript-btn").click();
      expect($("meeting-transcript-wrap").style.display).toBe("none");
      expect($("meeting-notes-transcript-btn").textContent).toBe("SHOW TRANSCRIPT");
    });

    it("closes the notes modal", () => {
      $("meeting-notes-modal").classList.remove("setup-hidden");
      $("meeting-notes-close").click();
      expect($("meeting-notes-modal").classList.contains("setup-hidden")).toBe(true);
    });

    it("copies notes to the clipboard and shows a success message", async () => {
      vi.stubGlobal("navigator", {
        clipboard: { writeText: vi.fn().mockResolvedValue() },
      });

      $("meeting-notes-copy").click();
      await flush();

      expect(navigator.clipboard.writeText).toHaveBeenCalled();
      expect($("meeting-notes-msg").className).toBe("ok");
      expect($("meeting-notes-msg").textContent).toBe("Copied to clipboard.");
    });

    it("shows an error message when the clipboard is unavailable", async () => {
      vi.stubGlobal("navigator", {
        clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
      });

      $("meeting-notes-copy").click();
      await flush();

      expect($("meeting-notes-msg").className).toBe("err");
      expect($("meeting-notes-msg").textContent).toBe("Clipboard unavailable.");
    });

    it("exports notes as a downloadable markdown file", () => {
      vi.stubGlobal("URL", { createObjectURL: vi.fn(() => "blob:mock"), revokeObjectURL: vi.fn() });
      let captured = null;
      vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () {
        captured = { href: this.href, download: this.download };
      });

      socketMock.trigger("meeting_notes_ready", { notes: "Some notes", transcript: "Some transcript" });
      $("meeting-notes-export").click();

      expect(captured.href).toBe("blob:mock");
      expect(captured.download).toMatch(/^meeting-.*\.md$/);
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:mock");
    });
  });

  describe("panel minimise toggle", () => {
    it("toggles the log's visibility on each close-button click", () => {
      $("meeting-panel-close").click();
      expect($("meeting-log").style.display).toBe("none");

      $("meeting-panel-close").click();
      expect($("meeting-log").style.display).toBe("");
    });
  });

  describe("start/end meeting", () => {
    it("declines gracefully when the microphone is denied", async () => {
      stubMediaApis({ mic: false });

      $("meeting-btn").click();
      await flush();

      expect(window.__chat.addMsg).toHaveBeenCalledWith(
        "Microphone access is required to record meetings, sir.",
        "in",
      );
      expect(socketMock.emit).not.toHaveBeenCalledWith("start_meeting");
      expect($("meeting-panel").classList.contains("meeting-hidden")).toBe(true);
    });

    it("starts mic-only when screen-share audio is declined, then ends cleanly", async () => {
      stubMediaApis({ mic: true, displayAudio: false });

      $("meeting-btn").click();
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("start_meeting");
      expect($("meeting-panel").classList.contains("meeting-hidden")).toBe(false);
      expect($("meeting-btn").classList.contains("meeting-live")).toBe(true);
      expect($("meeting-status-line").textContent).toBe("Recording (mic only)…");

      $("meeting-end-btn").click();
      await flush();

      expect(socketMock.emit).toHaveBeenCalledWith("end_meeting");
      expect($("meeting-btn").classList.contains("meeting-live")).toBe(false);
      expect($("meeting-status-line").textContent).toBe("Generating notes…");
    });

    it("starts with mic + system audio when screen-share audio is granted", async () => {
      stubMediaApis({ mic: true, displayAudio: true });

      $("meeting-btn").click();
      await flush();

      expect($("meeting-status-line").textContent).toBe("Recording (mic + system audio)…");

      $("meeting-end-btn").click();
      await flush();
    });

    it("clicking the meeting button while active ends the meeting instead of starting another", async () => {
      stubMediaApis({ mic: true, displayAudio: false });

      $("meeting-btn").click();
      await flush();
      expect($("meeting-btn").classList.contains("meeting-live")).toBe(true);

      $("meeting-btn").click(); // now active -> should end, not start again
      await flush();

      expect($("meeting-btn").classList.contains("meeting-live")).toBe(false);
      expect(socketMock.emit).toHaveBeenCalledWith("end_meeting");
    });
  });
});
