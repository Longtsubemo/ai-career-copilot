// Headless UI check using Chromium's built-in DevTools protocol.
// Start the local API and Vite first, then launch Chrome with
// --headless=new --remote-debugging-port=9222 and pass a screenshot path.

import fs from "node:fs";

const screenshotPath = process.argv[2];
if (!screenshotPath) {
  throw new Error("Pass an absolute path for the dashboard screenshot.");
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
let socket;

async function main() {
  const target = await waitFor(async () => {
    try {
      const response = await fetch("http://127.0.0.1:9222/json/list");
      if (!response.ok) return null;
      const targets = await response.json();
      return targets.find(
        (item) => item.type === "page" && item.webSocketDebuggerUrl,
      );
    } catch {
      return null;
    }
  }, "headless browser");

  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  let nextId = 0;
  const pending = new Map();
  const javascriptErrors = [];
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
      javascriptErrors.push(message.params.exceptionDetails.text);
    } else if (
      message.method === "Runtime.consoleAPICalled" &&
      message.params.type === "error"
    ) {
      javascriptErrors.push(
        message.params.args
          .map((argument) => argument.value ?? argument.description ?? "")
          .join(" "),
      );
    }
  });

  function command(method, params = {}) {
    const id = ++nextId;
    socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
  }

  async function evaluate(expression) {
    const result = await command("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text);
    }
    return result.result?.value;
  }

  await command("Page.enable");
  await command("Runtime.enable");
    await command("Page.navigate", { url: "http://localhost:5173/" });

  let dashboard;
  try {
    dashboard = await waitFor(async () => {
      const text = await evaluate("document.body.innerText");
      return text.includes("Dashboard") && !text.includes("Loading") ? text : null;
    }, "dashboard render");
  } catch (error) {
    console.error(`Current page: ${await evaluate("location.href")}`);
    console.error(`Rendered text: ${await evaluate("document.body.innerText")}`);
    console.error(`Browser errors: ${javascriptErrors.join(" | ")}`);
    throw error;
  }
  if (/sign in|log in|register|create account/i.test(dashboard)) {
    throw new Error("Authentication UI appeared on startup.");
  }
  await waitFor(async () => {
    const text = await evaluate("document.body.innerText");
    return (
      text.includes("Search for your first set of jobs") &&
      text.includes("Applications you choose to track") &&
      !text.includes("…")
    );
  }, "dashboard data to finish loading");

  const screenshot = await command("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
  });
  fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, "base64"));

  async function openRoute(label, path, marker) {
    const clicked = await evaluate(`(() => {
      const link = [...document.querySelectorAll("a")].find(
        (item) => item.textContent.trim() === ${JSON.stringify(label)},
      );
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
  await openRoute("Profile", "/profile", "Edit profile");
  await evaluate(`(() => {
    const button = [...document.querySelectorAll("button")].find(
      (item) => item.textContent.trim() === "Edit profile",
    );
    button?.click();
  })()`);
  await waitFor(() => evaluate("Boolean(document.querySelector('#candidate-name'))"), "profile editor");

  await evaluate("document.querySelector('#candidate-name').select()");
  await command("Input.insertText", { text: "Headless Local Profile" });
  await evaluate("document.querySelector('#candidate-headline').select()");
  await command("Input.insertText", { text: "UI verification profile" });
  await evaluate(`(() => {
    const button = [...document.querySelectorAll("button")].find(
      (item) => item.textContent.trim() === "Save changes",
    );
    button?.click();
  })()`);
  await waitFor(async () => {
    const text = await evaluate("document.body.innerText");
    return text.includes("Headless Local Profile") && !text.includes("Save changes");
  }, "profile save");

  const profile = await evaluate(
    "fetch('/api/candidates/me').then((response) => response.json())",
  );
  if (
    profile.name !== "Headless Local Profile" ||
    profile.headline !== "UI verification profile"
  ) {
    throw new Error("Profile edit did not persist through the API.");
  }
  if (javascriptErrors.length) {
    throw new Error(`Frontend errors: ${javascriptErrors.join(" | ")}`);
  }

  console.log(
    JSON.stringify({
      dashboard: true,
      noAuthenticationUi: true,
      routes: ["/jobs", "/applications", "/resume", "/profile"],
      profileEditPersisted: true,
      javascriptErrors: javascriptErrors.length,
      screenshot: screenshotPath,
    }),
  );
  socket.close();
}

async function waitFor(check, label, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const result = await check();
    if (result) return result;
    await delay(200);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

main().catch((error) => {
  socket?.close();
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
