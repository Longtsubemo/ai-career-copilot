import type { AvailabilityStatus, Job, JobListPage, JobVerificationResult } from "../types/api";

import { api } from "./client";

export interface JobQuery {
  location?: string;
  experience_level?: string;
  is_active?: boolean;
  source?: string;
  availability?: AvailabilityStatus;
  limit?: number;
  offset?: number;
}

export async function listJobs(params: JobQuery = {}): Promise<JobListPage> {
  const { data } = await api.get<JobListPage>("/api/jobs", { params });
  return data;
}

export async function getJob(jobId: string): Promise<Job> {
  const { data } = await api.get<Job>(`/api/jobs/${jobId}`);
  return data;
}

export async function verifyJob(jobId: string): Promise<JobVerificationResult> {
  const { data } = await api.post<JobVerificationResult>(`/api/jobs/${jobId}/verify`);
  return data;
}
