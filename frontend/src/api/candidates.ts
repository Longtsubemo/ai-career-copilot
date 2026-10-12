import type {
  Candidate,
  CandidateCreateRequest,
  CandidateUpdateRequest,
} from "../types/api";

import { api } from "./client";

export async function listCandidates(): Promise<Candidate[]> {
  const { data } = await api.get<Candidate[]>("/api/candidates");
  return data;
}

export async function getCurrentCandidate(): Promise<Candidate> {
  const { data } = await api.get<Candidate>("/api/candidates/me");
  return data;
}

export async function createCandidate(
  payload: CandidateCreateRequest,
): Promise<Candidate> {
  const { data } = await api.post<Candidate>("/api/candidates", payload);
  return data;
}

export async function updateCurrentCandidate(
  payload: CandidateUpdateRequest,
): Promise<Candidate> {
  const { data } = await api.patch<Candidate>("/api/candidates/me", payload);
  return data;
}
