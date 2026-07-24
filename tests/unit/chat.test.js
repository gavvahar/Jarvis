import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

function buildDom({ online = true } = {}) {
  document.body.className = online ? "mode-awake" : "";
  document.body.innerHTML = `
    <div id="chat-panel">
      <div id="chat-head"></div>
      <div id="chat-log"></div>
      <form id="chat-form">
        <input id="chat-text" />
      </form>
      <button id="chat-close" class="chat-close"></button>
      <div id="chat-typing"></div>
    </div>
  `;
}

function $(id) {
  return document.getElementById(id);
}

async function loadChat(opts) {
  buildDom(opts);
  await import("../../static/v2/js/chat.js");
  return window.__chat;
}

describe("chat.js", () => {
  beforeEach(() => {
    vi.resetModules();
    delete window.__chat;
    delete window.__chatOpen;
    delete window.__justTyped;
    delete window.__sendMessage;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("does nothing if #chat-panel is missing from the page", async () => {
    document.body.innerHTML = "";
    await import("../../static/v2/js/chat.js");
    expect(window.__chat).toBeUndefined();
  });

  it("exposes window.__chat with the expected surface", async () => {
    const chat = await loadChat();
    expect(chat.addMsg).toBeTypeOf("function");
    expect(chat.updateMsg).toBeTypeOf("function");
    expect(chat.setTyping).toBeTypeOf("function");
    expect(chat.isOpen).toBeTypeOf("function");
    expect(chat.setOpen).toBeTypeOf("function");
  });

  describe("addMsg / updateMsg", () => {
    it("renders an outgoing bubble with a YOU label", async () => {
      const chat = await loadChat();
      const el = chat.addMsg("Hello there", "out");

      expect(el.className).toBe("msg out");
      expect(el.querySelector(".bubble").textContent).toBe("Hello there");
      expect(el.querySelector(".meta").textContent).toContain("YOU");
      expect($("chat-log").contains(el)).toBe(true);
    });

    it("renders an incoming bubble with a J.A.R.V.I.S label", async () => {
      const chat = await loadChat();
      const el = chat.addMsg("At your service.", "in");

      expect(el.className).toBe("msg in");
      expect(el.querySelector(".meta").textContent).toContain("J.A.R.V.I.S");
    });

    it("updateMsg replaces the bubble text on an existing message", async () => {
      const chat = await loadChat();
      const el = chat.addMsg("...", "in");

      chat.updateMsg(el, "Final answer.");

      expect(el.querySelector(".bubble").textContent).toBe("Final answer.");
    });

    it("updateMsg is a no-op when passed a falsy element", async () => {
      const chat = await loadChat();
      expect(() => chat.updateMsg(null, "text")).not.toThrow();
    });

    it("updateMsg tolerates a message element with no .bubble child", async () => {
      const chat = await loadChat();
      const el = document.createElement("div");
      expect(() => chat.updateMsg(el, "text")).not.toThrow();
    });
  });

  it("setTyping toggles the 'show' class on the typing indicator", async () => {
    const chat = await loadChat();
    chat.setTyping(true);
    expect($("chat-typing").classList.contains("show")).toBe(true);
    chat.setTyping(false);
    expect($("chat-typing").classList.contains("show")).toBe(false);
  });

  describe("open/close", () => {
    it("setOpen(true) opens the panel while online", async () => {
      const chat = await loadChat({ online: true });
      chat.setOpen(true);

      expect(chat.isOpen()).toBe(true);
      expect(window.__chatOpen).toBe(true);
      expect($("chat-panel").classList.contains("chat-open")).toBe(true);
      expect($("chat-panel").classList.contains("chat-hidden")).toBe(false);
      expect($("chat-panel").dataset.tab).toBe("Chat Tab");
    });

    it("setOpen(true) is a no-op while offline (not on the Online screen)", async () => {
      const chat = await loadChat({ online: false });
      chat.setOpen(true);
      expect(chat.isOpen()).toBe(false);
    });

    it("the close button closes the panel", async () => {
      const chat = await loadChat();
      chat.setOpen(true);
      $("chat-close").click();
      expect(chat.isOpen()).toBe(false);
      expect($("chat-panel").classList.contains("chat-hidden")).toBe(true);
    });

    it("pressing 'c' toggles the panel open, and again closes it", async () => {
      const chat = await loadChat();
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "c" }));
      expect(chat.isOpen()).toBe(true);

      window.dispatchEvent(new KeyboardEvent("keydown", { key: "C" }));
      expect(chat.isOpen()).toBe(false);
    });

    it("ignores the 'c' shortcut while typing in a field", async () => {
      const chat = await loadChat();
      const input = document.createElement("input");
      document.body.appendChild(input);
      const evt = new KeyboardEvent("keydown", { key: "c" });
      Object.defineProperty(evt, "target", { value: input });
      window.dispatchEvent(evt);

      expect(chat.isOpen()).toBe(false);
    });

    it("ignores the 'c' shortcut while typing in a textarea", async () => {
      const chat = await loadChat();
      const textarea = document.createElement("textarea");
      document.body.appendChild(textarea);
      const evt = new KeyboardEvent("keydown", { key: "c" });
      Object.defineProperty(evt, "target", { value: textarea });
      window.dispatchEvent(evt);

      expect(chat.isOpen()).toBe(false);
    });

    it("ignores the 'c' shortcut inside a contenteditable element", async () => {
      const chat = await loadChat();
      const div = document.createElement("div");
      const evt = new KeyboardEvent("keydown", { key: "c" });
      Object.defineProperty(evt, "target", {
        value: Object.assign(div, { isContentEditable: true }),
      });
      window.dispatchEvent(evt);

      expect(chat.isOpen()).toBe(false);
    });

    it("Escape in the chat input closes the panel", async () => {
      const chat = await loadChat();
      chat.setOpen(true);
      $("chat-text").dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", cancelable: true }),
      );
      expect(chat.isOpen()).toBe(false);
    });

    it("auto-closes when the app leaves Online mode", async () => {
      const chat = await loadChat({ online: true });
      chat.setOpen(true);
      expect(chat.isOpen()).toBe(true);

      document.body.classList.remove("mode-awake");
      await new Promise((r) => setTimeout(r, 10));

      expect(chat.isOpen()).toBe(false);
    });
  });

  describe("submitting a message", () => {
    it("adds an outgoing bubble, records __justTyped, and forwards to __sendMessage", async () => {
      await loadChat();
      window.__sendMessage = vi.fn();
      $("chat-text").value = "  What's the weather?  ";

      $("chat-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );

      expect($("chat-log").textContent).toContain("What's the weather?");
      expect($("chat-text").value).toBe("");
      expect(window.__justTyped.text).toBe("What's the weather?");
      expect(window.__sendMessage).toHaveBeenCalledWith("What's the weather?");
    });

    it("does nothing when the input is blank", async () => {
      await loadChat();
      window.__sendMessage = vi.fn();
      $("chat-text").value = "   ";

      $("chat-form").dispatchEvent(
        new Event("submit", { bubbles: true, cancelable: true }),
      );

      expect($("chat-log").children.length).toBe(0);
      expect(window.__sendMessage).not.toHaveBeenCalled();
    });

    it("still renders the bubble locally when window.__sendMessage isn't wired up yet", async () => {
      await loadChat();
      $("chat-text").value = "hello";

      expect(() =>
        $("chat-form").dispatchEvent(
          new Event("submit", { bubbles: true, cancelable: true }),
        ),
      ).not.toThrow();

      expect($("chat-log").textContent).toContain("hello");
    });
  });

  describe("dragging the panel by its header", () => {
    function pointerEvent(type, { clientX = 0, clientY = 0, pointerId = 1, target } = {}) {
      const evt = new Event(type, { bubbles: true, cancelable: true });
      Object.defineProperty(evt, "clientX", { value: clientX });
      Object.defineProperty(evt, "clientY", { value: clientY });
      Object.defineProperty(evt, "pointerId", { value: pointerId });
      if (target) Object.defineProperty(evt, "target", { value: target });
      return evt;
    }

    beforeEach(async () => {
      await loadChat();
      $("chat-head").setPointerCapture = vi.fn();
    });

    it("ignores pointerdown that originates on the close button", () => {
      $("chat-head").dispatchEvent(
        pointerEvent("pointerdown", { target: $("chat-close") }),
      );
      $("chat-head").dispatchEvent(pointerEvent("pointermove", { clientX: 50, clientY: 50 }));

      expect($("chat-panel").classList.contains("dragging")).toBe(false);
      expect($("chat-panel").style.left).toBe("");
    });

    it("pointermove before any pointerdown does nothing", () => {
      $("chat-head").dispatchEvent(pointerEvent("pointermove", { clientX: 50, clientY: 50 }));
      expect($("chat-panel").style.left).toBe("");
    });

    it("drags the panel, clamped within the window bounds, then stops on pointerup", () => {
      $("chat-head").dispatchEvent(pointerEvent("pointerdown", { clientX: 10, clientY: 10 }));
      expect($("chat-panel").classList.contains("dragging")).toBe(true);
      expect($("chat-head").setPointerCapture).toHaveBeenCalledWith(1);

      $("chat-head").dispatchEvent(pointerEvent("pointermove", { clientX: 40, clientY: 30 }));
      expect($("chat-panel").style.left).not.toBe("");
      expect($("chat-panel").style.top).not.toBe("");

      $("chat-head").dispatchEvent(pointerEvent("pointerup"));
      expect($("chat-panel").classList.contains("dragging")).toBe(false);

      // now that drag is cleared, further pointermove is a no-op
      const leftBefore = $("chat-panel").style.left;
      $("chat-head").dispatchEvent(pointerEvent("pointermove", { clientX: 999, clientY: 999 }));
      expect($("chat-panel").style.left).toBe(leftBefore);
    });

    it("pointercancel also ends the drag", () => {
      $("chat-head").dispatchEvent(pointerEvent("pointerdown", { clientX: 10, clientY: 10 }));
      $("chat-head").dispatchEvent(pointerEvent("pointercancel"));
      expect($("chat-panel").classList.contains("dragging")).toBe(false);
    });
  });
});
