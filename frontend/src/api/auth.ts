import type { LoginRequest, RegisterRequest, User } from "../types/api";

import { api } from "./client";

export async function register(payload: RegisterRequest): Promise<User> {
  const { data } = await api.post<User>("/api/auth/register", payload);
  return data;
}

export async function login(payload: LoginRequest): Promise<User> {
  const { data } = await api.post<User>("/api/auth/login", payload);
  return data;
}

export async function logout(): Promise<void> {
  await api.post("/api/auth/logout");
}

export async function getCurrentUser(): Promise<User> {
  const { data } = await api.get<User>("/api/auth/me");
  return data;
}
