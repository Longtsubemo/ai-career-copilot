import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import App from "./App";
import { Spinner } from "./components/ui/Spinner";
import { api, clearApiCapability, installApiCapability } from "./api/client";
import "./index.css";

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root container #root not found");
}

type BackendStatus = {
  phase: "starting" | "ready" | "failed" | "stopping";
  api_url: string | null;
  message: string | null;
};

function DesktopBootstrap() {
  const [status, setStatus] = useState<BackendStatus>({
    phase: "starting",
    api_url: null,
    message: null,
  });

  useEffect(() => {
    let active = true;
    let capabilityInstalled = false;
    let unlistenError: (() => void) | undefined;
    void listen<string>("desktop-runtime-error", (event) => {
      if (active) {
        clearApiCapability();
        capabilityInstalled = false;
        setStatus({
          phase: "failed",
          api_url: null,
          message: event.payload,
        });
      }
    }).then((unlisten) => {
      if (active) unlistenError = unlisten;
      else unlisten();
    }).catch(() => undefined);

    const poll = async () => {
      try {
        const next = await invoke<BackendStatus>("backend_status");
        if (!active) return;
        if (next.phase === "ready" && next.api_url) {
          if (!capabilityInstalled) {
            const capability = await invoke<string>("api_capability");
            if (!active) return;
            installApiCapability(capability);
            capabilityInstalled = true;
          }
          api.defaults.baseURL = next.api_url;
        } else if (next.phase === "failed" || next.phase === "stopping") {
          clearApiCapability();
          capabilityInstalled = false;
        }
        setStatus(next);
      } catch {
        if (active) {
          clearApiCapability();
          capabilityInstalled = false;
          setStatus({
            phase: "failed",
            api_url: null,
            message: "The local services could not be started. Your saved data was preserved.",
          });
        }
      }
    };

    void poll();
    const timer = window.setInterval(() => void poll(), 300);
    return () => {
      active = false;
      window.clearInterval(timer);
      clearApiCapability();
      unlistenError?.();
    };
  }, []);

  if (status.phase === "ready" && status.api_url) {
    return <App />;
  }
  if (status.phase === "failed") {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
        <p className="text-sm text-slate-700">{status.message}</p>
        <button
          type="button"
          onClick={() => {
            setStatus({ phase: "starting", api_url: null, message: null });
            void invoke("retry_backend").catch(() => {
              setStatus({
                phase: "failed",
                api_url: null,
                message: "The local services could not be started. Your saved data was preserved.",
              });
            });
          }}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          Retry
        </button>
      </div>
    );
  }
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-3 text-sm text-slate-600">
      <Spinner className="h-8 w-8 text-indigo-500" />
      <span>{status.phase === "stopping" ? "Stopping local services…" : "Starting local services…"}</span>
    </div>
  );
}

function Root() {
  const useNativeBootstrap = isTauri() &&
    (import.meta.env.DEV || window.location.hostname === "tauri.localhost");
  return useNativeBootstrap ? (
    <DesktopBootstrap />
  ) : (
    <App />
  );
}

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </StrictMode>,
);
