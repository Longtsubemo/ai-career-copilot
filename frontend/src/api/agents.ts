import type {
  AgentRun,
  CoverLetterEnvelope,
  DiscoveryOutput,
  DiscoveryRequest,
  JobAnalysisOutput,
  MatchEnvelope,
} from "../types/api";

import { api } from "./client";

export interface CoverLetterRequest {
  candidate_id?: string;
  resume_document_id?: string;
  match_id?: string;
}

export interface MatchRequest {
  candidate_id?: string;
  resume_document_id?: string;
}

export async function discoverJobs(
  request: DiscoveryRequest,
): Promise<AgentRun<DiscoveryOutput>> {
  const { data } = await api.post<AgentRun<DiscoveryOutput>>(
    "/api/jobs/discover",
    request,
  );
  return data;
}

export async function analyzeJob(
  jobId: string,
): Promise<AgentRun<JobAnalysisOutput>> {
  const { data } = await api.post<AgentRun<JobAnalysisOutput>>(
    `/api/jobs/${jobId}/analyze`,
  );
  return data;
}

export async function matchJob(
  jobId: string,
  request: MatchRequest = {},
): Promise<AgentRun<MatchEnvelope>> {
  const { data } = await api.post<AgentRun<MatchEnvelope>>(
    `/api/jobs/${jobId}/match`,
    request,
  );
  return data;
}

export async function generateCoverLetter(
  jobId: string,
  request: CoverLetterRequest = {},
): Promise<AgentRun<CoverLetterEnvelope>> {
  const { data } = await api.post<AgentRun<CoverLetterEnvelope>>(
    `/api/jobs/${jobId}/cover-letter`,
    request,
  );
  return data;
}
