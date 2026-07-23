import { afterEach } from "vitest";

// Real production code loads socket.io-client as a page-level <script>, giving
// core.js a global `io()`. Feature modules import from core.js purely for its
// exports ($, socket, ...); tests that need those modules mock core.js itself
// (see doorbell/vision tests) rather than relying on this stub, but it keeps
// any incidental core.js import from throwing.
globalThis.io = () => ({
  on: () => {},
  emit: () => {},
});

afterEach(() => {
  document.body.innerHTML = "";
});
