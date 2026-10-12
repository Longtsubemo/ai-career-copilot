import type {
  ApplicationStatus,
  AvailabilityStatus,
  CrawlStatus,
  MatchSignal,
  MatchVerdict,
  VerificationOutcome,
} from "../types/api";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "muted";

export const APPLICATION_STATUS_META: Record<
  ApplicationStatus,
  { label: string; tone: Tone }
> = {
  draft: { label: "Draft", tone: "neutral" },
  applied: { label: "Applied", tone: "info" },
  interview: { label: "Interview", tone: "warning" },
  offer: { label: "Offer", tone: "success" },
  rejected: { label: "Rejected", tone: "danger" },
  withdrawn: { label: "Withdrawn", tone: "muted" },
};

export const MATCH_VERDICT_META: Record<MatchVerdict, { label: string; tone: Tone }> =
  {
    strong_match: { label: "Strong match", tone: "success" },
    good_match: { label: "Good match", tone: "success" },
    partial_match: { label: "Partial match", tone: "warning" },
    weak_match: { label: "Weak match", tone: "danger" },
    poor_match: { label: "Poor match", tone: "danger" },
  };

export const MATCH_SIGNAL_META: Record<
  MatchSignal,
  { label: string; symbol: string; tone: Tone }
> = {
  matched: { label: "Matched", symbol: "✓", tone: "success" },
  mismatched: { label: "Not matched", symbol: "✕", tone: "danger" },
  unclear: { label: "Unclear", symbol: "?", tone: "muted" },
};

export const CRAWL_STATUS_META: Record<CrawlStatus, { label: string; tone: Tone }> =
  {
    scraped: { label: "Scraped", tone: "success" },
    pending: { label: "Pending", tone: "warning" },
    failed: { label: "Crawl failed", tone: "danger" },
  };

export const AVAILABILITY_META: Record<
  AvailabilityStatus,
  { label: string; tone: Tone }
> = {
  unknown: { label: "Unverified", tone: "muted" },
  available: { label: "Available", tone: "success" },
  stale: { label: "Not recently verified", tone: "warning" },
  closed: { label: "Closed", tone: "danger" },
};

export const VERIFICATION_OUTCOME_META: Record<
  VerificationOutcome,
  { label: string; tone: Tone }
> = {
  verified_available: { label: "Confirmed available", tone: "success" },
  verified_closed: { label: "Confirmed closed", tone: "danger" },
  still_unknown: { label: "Could not tell", tone: "muted" },
  check_failed: { label: "Check failed", tone: "warning" },
};

export function scoreTone(score: number): Tone {
  if (score >= 75) {
    return "success";
  }
  if (score >= 50) {
    return "warning";
  }
  return "danger";
}

export function titleCase(value: string): string {
  return value
    .split(/[_\s]+/)
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : part))
    .join(" ");
}
