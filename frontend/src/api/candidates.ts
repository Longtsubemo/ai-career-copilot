import type {
  Candidate,
  CandidateUpdateRequest,
} from "../types/api";

import { api } from "./client";

export async function getCurrentCandidate(): Promise<Candidate> {
  const { data } = await api.get<Candidate>("/api/candidates/me");
  return data;
}

export async function updateCurrentCandidate(
  payload: CandidateUpdateRequest,
): Promise<Candidate> {
  const { data } = await api.patch<Candidate>("/api/candidates/me", payload);
  return data;
}
