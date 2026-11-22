// Headless checks for an isolated packaged Tauri app through its local WebView2 CDP endpoint.
// Usage: node tools/headless_tauri_smoke.mjs <port> <mode> [screenshot-path]

import fs from "node:fs";

const port = Number(process.argv[2]);
const mode = process.argv[3];
const screenshotPath = process.argv[4];
if (!Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error("Pass the WebView2 remote-debugging port.");
}
if (!["fresh", "relaunch", "close"].includes(mode)) {
  throw new Error("Mode must be fresh, relaunch, or close.");
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
const base = `http://127.0.0.1:${port}`;
let socket;

async function waitFor(check, label, timeout = 90_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await delay(250);
  }
  throw new Error(`Timed out waiting for ${label}.`);
}

async function main() {
  const target = await waitFor(async () => {
    try {
      const response = await fetch(`${base}/json/list`);
      if (!response.ok) return null;
      const targets = await response.json();
      return targets.find((item) => item.type === "page" && item.webSocketDebuggerUrl);
    } catch {
      return null;
    }
  }, "Tauri WebView2 CDP target");

  const cdpUrl = new URL(target.webSocketDebuggerUrl);
  if (cdpUrl.hostname !== "127.0.0.1" && cdpUrl.hostname !== "localhost" && cdpUrl.hostname !== "::1") {
    throw new Error(`CDP target is not loopback-bound: ${cdpUrl.hostname}`);
  }

  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let nextId = 0;
  const pending = new Map();
  const frontendErrors = [];
  const apiResponses = [];
  socket.addEventListener("message", ({ data }) => {
    const message = JSON.parse(data);
    if (message.id && pending.has(message.id)) {
      const handlers = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) handlers.reject(new Error(message.error.message));
      else handlers.resolve(message.result);
      return;
    }
    if (message.method === "Runtime.exceptionThrown") {
      frontendErrors.push(message.params.exceptionDetails.text);
    } else if (
      message.method === "Runtime.consoleAPICalled" &&
      message.params.type === "error"
    ) {
      frontendErrors.push(
        message.params.args.map((argument) => argument.value ?? argument.description ?? "").join(" "),
      );
    } else if (message.method === "Network.responseReceived") {
      const response = message.params.response;
      if (response.url.includes("/api/")) {
        const path = new URL(response.url).pathname;
        apiResponses.push({ path, status: response.status });
      }
    }
  });

  function command(method, params = {}) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      socket.send(JSON.stringify({ id, method, params }));
    });
  }

  async function evaluate(expression) {
    const response = await command("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text);
    return response.result?.value;
  }

  await command("Page.enable");
  await command("Runtime.enable");
  await command("Network.enable");

  const initial = await waitFor(async () => {
    const state = await evaluate(`(() => {
      const internals = window.__TAURI_INTERNALS__;
      return {
        title: document.title,
        origin: location.origin,
        text: document.body?.innerText ?? "",
        invokeAvailable: typeof internals?.invoke === "function",
      };
    })()`);
    return state?.invokeAvailable && state.text ? state : null;
  }, "native WebView and application shell");

  if (initial.origin !== "http://tauri.localhost") {
    throw new Error(`Unexpected packaged WebView origin: ${initial.origin}`);
  }
  const status = await evaluate("window.__TAURI_INTERNALS__.invoke('backend_status')");
  if (mode === "close") {
    await evaluate("window.__TAURI_INTERNALS__.invoke('plugin:window|close', { label: 'main' })");
    console.log(JSON.stringify({ mode, closeRequested: true, backendPhase: status?.phase ?? "unknown" }));
    socket.close();
    return;
  }
  if (status?.phase !== "ready" || !/^http:\/\/(?:localhost|127\.0\.0\.1):\d+$/.test(status.api_url ?? "")) {
    throw new Error(`Local backend did not become ready (phase=${status?.phase ?? "unknown"}; message=${status?.message ?? "none"}).`);
  }
  const unauthenticated = await fetch(`${status.api_url}/health`);
  if (unauthenticated.status !== 401) {
    throw new Error(`Unauthenticated local health request returned ${unauthenticated.status}, expected 401.`);
  }
  const unauthorizedHeaders = {
    cacheControl: unauthenticated.headers.get("cache-control"),
    challenge: unauthenticated.headers.get("www-authenticate"),
  };
  if (unauthorizedHeaders.cacheControl !== "no-store" || unauthorizedHeaders.challenge !== "Bearer") {
    throw new Error("Unauthorized health response did not include the expected no-store bearer challenge.");
  }

  const storage = await evaluate(`(() => {
    const values = [...Object.values(localStorage), ...Object.values(sessionStorage)];
    return { keys: [...Object.keys(localStorage), ...Object.keys(sessionStorage)], tokenPersisted: values.some((v) => /^[0-9a-f]{64}$/.test(v)) };
  })()`);
  if (storage.tokenPersisted) throw new Error("The local API capability was persisted in browser storage.");

  async function waitForDashboard() {
    return waitFor(async () => {
      const text = await evaluate("document.body.innerText");
      return text.includes("Dashboard") && !text.includes("Starting local services") && !text.includes("Loading") ? text : null;
    }, "dashboard render");
  }

  const dashboardText = await waitForDashboard();
  if (/sign in|log in|register|create account/i.test(dashboardText)) {
    throw new Error("Login or registration UI appeared on startup.");
  }
  await waitFor(async () => {
    const text = await evaluate("document.body.innerText");
    return text.includes("Search for your first set of jobs") && text.includes("Applications you choose to track");
  }, "dashboard data");

  if (mode === "fresh") {
    if (!screenshotPath) throw new Error("Fresh mode requires an absolute screenshot path.");
    const image = await command("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: true,
    });
    fs.writeFileSync(screenshotPath, Buffer.from(image.data, "base64"));
  }

  async function openRoute(label, path, marker) {
    const clicked = await evaluate(`(() => {
      const link = [...document.querySelectorAll("a")].find((item) => item.textContent.trim() === ${JSON.stringify(label)});
      if (!link) return false;
      link.click();
      return true;
    })()`);
    if (!clicked) throw new Error(`Navigation link ${label} was missing.`);
    return waitFor(async () => {
      const currentPath = await evaluate("location.pathname");
      const text = await evaluate("document.body.innerText");
      return currentPath === path && text.includes(marker) ? text : null;
    }, `${label} page`);
  }

  await openRoute("Jobs", "/jobs", "Jobs");
  await openRoute("Applications", "/applications", "Applications");
  await openRoute("Resume", "/resume", "Resume");
  await openRoute("Profile", "/profile", "Profile");

  if (mode === "fresh") {
    const editClicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll("button")].find((item) => item.textContent.trim() === "Edit profile");
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (!editClicked) throw new Error("Profile editor button was missing.");
    await waitFor(() => evaluate("Boolean(document.querySelector('#candidate-name'))"), "profile editor");
    await evaluate("document.querySelector('#candidate-name').focus(); document.querySelector('#candidate-name').select()");
    await command("Input.insertText", { text: "Headless Local Profile" });
    await evaluate("document.querySelector('#candidate-headline').focus(); document.querySelector('#candidate-headline').select()");
    await command("Input.insertText", { text: "UI verification profile" });
    const saved = await evaluate(`(() => {
      const form = document.querySelector("form");
      const button = [...form.querySelectorAll("button")].find((item) => item.textContent.trim() === "Save changes");
      if (!button) return false;
      button.click();
      return true;
    })()`);
    if (!saved) throw new Error("Profile save button was missing.");
    await waitFor(async () => {
      const text = await evaluate("document.body.innerText");
      return text.includes("Headless Local Profile") && text.includes("UI verification profile") && !text.includes("Save changes");
    }, "synthetic profile save");
  } else {
    const restored = await waitFor(async () => {
      const text = await evaluate("document.body.innerText");
      return text.includes("Headless Local Profile") && text.includes("UI verification profile") ? text : null;
    }, "synthetic profile after relaunch");
    if (!restored) throw new Error("The synthetic profile did not persist after relaunch.");
  }

  const apiStatuses = apiResponses.map((item) => item.status);
  if (!apiResponses.length || apiStatuses.some((code) => code === 401 || code === 403)) {
    throw new Error(`Trusted WebView API requests did not all authenticate: ${JSON.stringify(apiResponses)}`);
  }
  if (frontendErrors.length) {
    throw new Error(`Frontend runtime errors: ${frontendErrors.join(" | ")}`);
  }

  console.log(JSON.stringify({
    mode,
    windowTitle: initial.title,
    origin: initial.origin,
    apiUrlHost: new URL(status.api_url).hostname,
    apiUrlPort: Number(new URL(status.api_url).port),
    backendReady: true,
    dashboard: true,
    noAuthenticationUi: true,
    routes: ["/jobs", "/applications", "/resume", "/profile"],
    profileEditPersisted: mode === "fresh",
    profileRestoredAfterRelaunch: mode === "relaunch",
    unauthorizedHealthStatus: unauthenticated.status,
    unauthorizedHeaders,
    browserStorageKeys: storage.keys,
    capabilityPersisted: storage.tokenPersisted,
    apiResponses,
    frontendErrors: frontendErrors.length,
    screenshot: mode === "fresh" ? screenshotPath : null,
  }));
  socket.close();
}

main().catch((error) => {
  socket?.close();
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
