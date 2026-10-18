import type { AxiosError } from "axios";

import type { AgentRun } from "../types/api";

const NETWORK_MESSAGE =
  "Cannot reach the API server. Check that the backend is running and that VITE_API_BASE_URL points at it.";

const FALLBACKS: Record<number, string> = {
  0: NETWORK_MESSAGE,
  400: "The request was rejected. Check the input and try again.",
  401: "You are not authorised to do that.",
  403: "That action is not allowed.",
  404: "We could not find what you were looking for.",
  409: "This conflicts with the current state. Refresh the page and try again.",
  413: "That file is too large. The maximum resume size is 2 MB.",
  415: "Unsupported file type. Upload a plain-text (.txt) or Markdown (.md) resume — PDF and DOCX are not supported yet.",
  422: "Some fields are invalid. Check your input and try again.",
  429: "Too many requests. Wait a moment and try again.",
  500: "The server hit an unexpected error. Please try again.",
  503: "An external service is unavailable right now. Please try again.",
};

const UNSUPPORTED_FILE =
  "Unsupported file type. Upload a plain-text (.txt) or Markdown (.md) resume — PDF and DOCX are not supported yet.";

const MESSAGE_RULES: Array<[RegExp, string]> = [
  [/no candidate profile exists yet/i, "Create your profile first."],
  [
    /no master resume uploaded yet/i,
    "Upload your resume first — matching and cover letters are grounded in it.",
  ],
  [
    /no match exists for this job/i,
    "Match this job against your resume first, then generate the cover letter.",
  ],
  [
    /JobAnalysisNotFoundError|no succeeded job analysis/i,
    "Analyze this job first — the match is based on that analysis.",
  ],
  [
    /InsufficientCandidateProfileError/i,
    "Add a summary, skills or a headline to your profile so there is something to match against.",
  ],
  [
    /InsufficientJobContentError/i,
    "This job has no usable description to analyze.",
  ],
  [
    /UnsupportedResumeFormatError|unsupported content type|binary PDF|binary DOCX/i,
    UNSUPPORTED_FILE,
  ],
  [
    /OversizedResumeError|file too large|maximum resume size/i,
    FALLBACKS[413],
  ],
  [/EmptyResumeError/i, "That resume is empty. Upload a file with actual text."],
  [
    /DuplicateCandidateError/i,
    "A profile already exists. Refresh the page to continue.",
  ],
  [
    /InvalidMasterResumeError|InvalidResumeDocumentError/i,
    "The resume on file cannot be used for this request. Upload a fresh master resume and try again.",
  ],
  [
    /InsufficientCandidateProfile|no profile evidence/i,
    "Add a summary, skills or a headline to your profile so there is something to match against.",
  ],
  [/JobNotFoundError|job .* does not exist/i, "That job no longer exists."],
  [
    /CandidateNotFoundError|candidate .* does not exist/i,
    "That candidate profile no longer exists.",
  ],
  [/SearXNGError/i, "The job search service is unavailable. Check that SearXNG is running, then try again."],
  [/FirecrawlSearchError/i, "The hosted job search service is unavailable. Try again later."],
  [/FirecrawlError/i, "The page scraping service is unavailable. Check that Firecrawl is running, then try again."],
  [/GeminiError/i, "The AI analysis service is unavailable. Check the backend Gemini configuration, then try again."],
];

export interface ApiErrorOptions {
  status: number;
  detail?: unknown;
  run?: AgentRun | null;
  cause?: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly run: AgentRun | null;

  constructor(message: string, options: ApiErrorOptions) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.detail = options.detail ?? null;
    this.run = options.run ?? null;
  }
}

function readDetail(detail: unknown): { message: string | null; run: AgentRun | null } {
  if (typeof detail === "string") {
    const trimmed = detail.trim();
    return { message: trimmed ? trimmed : null, run: null };
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item) {
          const msg = (item as { msg?: unknown }).msg;
          return typeof msg === "string" ? msg : null;
        }
        return typeof item === "string" ? item : null;
      })
      .filter((item): item is string => Boolean(item));
    const unique = [...new Set(messages)];
    return {
      message: unique.length ? unique.join(" ") : null,
      run: null,
    };
  }

  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    const message = typeof record.message === "string" ? record.message : null;
    const run =
      record.run && typeof record.run === "object"
        ? (record.run as AgentRun)
        : null;
    return { message, run };
  }

  return { message: null, run: null };
}

function humanize(status: number, raw: string | null): string {
  if (raw) {
    for (const [pattern, text] of MESSAGE_RULES) {
      if (pattern.test(raw)) {
        return text;
      }
    }
  }

  if (status === 0 || status === 500 || status === 503) {
    return FALLBACKS[status] ?? FALLBACKS[500];
  }

  if (raw) {
    return raw;
  }

  return FALLBACKS[status] ?? "Something went wrong. Please try again.";
}

export function fromAxiosError(error: AxiosError): ApiError {
  const status = error.response?.status ?? 0;
  const data: unknown = error.response?.data;
  const detail =
    data && typeof data === "object" && "detail" in data
      ? (data as { detail: unknown }).detail
      : null;

  const { message: raw, run } = readDetail(detail);
  return new ApiError(humanize(status, raw), { status, detail, run });
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) {
    return error;
  }

  const axiosError = error as AxiosError | undefined;
  if (axiosError && typeof axiosError === "object" && "isAxiosError" in axiosError) {
    return fromAxiosError(error as AxiosError);
  }

  return new ApiError(FALLBACKS[500], { status: 500, cause: error });
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return toApiError(error).message;
}
