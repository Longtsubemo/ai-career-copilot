import type { ApplicationStatus } from "../types/api";

export interface WorkflowStep {
  title: string;
  detail: string;
}

export const APPLICATION_WORKFLOW_STEPS: WorkflowStep[] = [
  {
    title: "Prepare your materials",
    detail:
      "Analyze the job, match it against your resume, and generate a tailored cover letter.",
  },
  {
    title: "Apply on the employer's site",
    detail:
      "Open the original posting and submit your application there. This app never submits for you.",
  },
  {
    title: "Record what you did",
    detail:
      "Mark the application as applied so you have an honest record of when you sent it. Nothing is marked for you.",
  },
];

export function requiresAppliedConfirmation(
  current: ApplicationStatus,
  next: ApplicationStatus,
): boolean {
  return next === "applied" && current !== "applied";
}

export function hasRecordedSubmission(
  appliedAt: string | null,
): boolean {
  return Boolean(appliedAt);
}
