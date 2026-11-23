import axios from "axios";

import { fromAxiosError } from "./errors";

function resolveBaseUrl(): string {
  const configured = import.meta.env?.VITE_API_BASE_URL;
  if (!configured) {
    return "";
  }
  return configured.trim().replace(/\/+$/, "");
}

export const API_BASE_URL = resolveBaseUrl();

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: { Accept: "application/json" },
  timeout: 0,
});

const API_CAPABILITY_PATTERN = /^[0-9a-f]{64}$/;

export function installApiCapability(capability: string): void {
  if (!API_CAPABILITY_PATTERN.test(capability)) {
    throw new Error("The local API capability is invalid.");
  }
  api.defaults.headers.common.Authorization = `Bearer ${capability}`;
}

export function clearApiCapability(): void {
  delete api.defaults.headers.common.Authorization;
}

api.interceptors.response.use(
  (response) => response,
  (error) => Promise.reject(fromAxiosError(error)),
);
