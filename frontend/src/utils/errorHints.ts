import type { ApiError } from "../api/errors";

export function mentionsAnalysis(error: ApiError | null): boolean {
  return Boolean(error && /Analyze this job first/.test(error.message));
}

export function mentionsMatch(error: ApiError | null): boolean {
  return Boolean(error && /Match this job against your resume first/.test(error.message));
}

export function mentionsResume(error: ApiError | null): boolean {
  return Boolean(error && /Upload your resume first/.test(error.message));
}

export function mentionsProfile(error: ApiError | null): boolean {
  return Boolean(
    error && /Add a summary, skills or a headline/.test(error.message),
  );
}
