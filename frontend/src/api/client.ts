import axios, { type AxiosError } from "axios";

import { fromAxiosError } from "./errors";
import { CSRF_COOKIE_NAME, csrfHeader } from "./session";

export const UNAUTHORIZED_EVENT = "auth:unauthorized";

function resolveBaseUrl(): string {
  const configured = import.meta.env?.VITE_API_BASE_URL;
  if (!configured) {
    return "";
  }
  return configured.trim().replace(/\/+$/, "");
}

function resolveCsrfCookieName(): string {
  const configured = import.meta.env?.VITE_CSRF_COOKIE_NAME;
  return configured && configured.trim() ? configured.trim() : CSRF_COOKIE_NAME;
}

export const API_BASE_URL = resolveBaseUrl();

const CSRF_COOKIE = resolveCsrfCookieName();

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { Accept: "application/json" },
  timeout: 0,
  withCredentials: true,
});

api.interceptors.request.use((config) => {
  if (typeof document !== "undefined") {
    for (const [name, value] of Object.entries(
      csrfHeader(document.cookie, config.method, CSRF_COOKIE),
    )) {
      config.headers.set(name, value);
    }
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401 && typeof window !== "undefined") {
      window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    }
    return Promise.reject(fromAxiosError(error));
  },
);
