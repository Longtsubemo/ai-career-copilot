export type UUID = string;

export interface User {
  id: UUID;
  email: string;
  created_at: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  name?: string | null;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface Candidate {
  id: UUID;
  name: string;
  headline: string | null;
  location: string | null;
  years_of_experience: number | null;
  seniority: string | null;
  skills: string[] | null;
  summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface CandidateCreateRequest {
  name: string;
  headline?: string | null;
  location?: string | null;
  years_of_experience?: number | null;
  seniority?: string | null;
  skills?: string[] | null;
  summary?: string | null;
}

export interface CandidateUpdateRequest {
  name?: string;
  headline?: string | null;
  location?: string | null;
  years_of_experience?: number | null;
  seniority?: string | null;
  skills?: string[] | null;
  summary?: string | null;
}

export type DocumentKind = "master_resume" | "tailored_resume" | "cover_letter";

export interface Resume {
  id: UUID;
  candidate_id: UUID;
  kind: DocumentKind;
  content: string;
  source_document_id: UUID | null;
  model: string | null;
  prompt_version: string | null;
  created_at: string;
}

export interface ResumeUploadResult {
  candidate_id: UUID;
  document_id: UUID;
  kind: DocumentKind;
  filename: string | null;
  created_at: string;
}

export interface ResumeHistory {
  items: Resume[];
  count: number;
}

export type CrawlStatus = "pending" | "scraped" | "failed";

export type AvailabilityStatus = "unknown" | "available" | "stale" | "closed";

export type VerificationOutcome =
  | "verified_available"
  | "verified_closed"
  | "still_unknown"
  | "check_failed";

export interface Job {
  id: UUID;
  company_id: UUID | null;
  company_name: string | null;
  title: string;
  description: string | null;
  location: string | null;
  employment_type: string | null;
  experience_level: string | null;
  job_url: string;
  canonical_url: string;
  source: string;
  date_posted: string | null;
  is_active: boolean;
  crawl_status: CrawlStatus;
  availability: AvailabilityStatus;
  last_verified_at: string | null;
  closed_at: string | null;
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
}

export interface JobListPage {
  items: Job[];
  total: number;
  limit: number;
  offset: number;
}

export interface JobVerificationResult {
  job: Job;
  outcome: VerificationOutcome;
  message: string;
  checked_at: string;
}

export type RunStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface AgentRun<TOutput = Record<string, unknown>> {
  id: UUID;
  agent_name: string;
  status: RunStatus;
  attempt_count: number;
  max_attempts: number;
  job_id: UUID | null;
  candidate_id: UUID | null;
  output: TOutput | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DiscoveryRequest {
  role: string;
  location: string;
  experience: string;
}

export interface DiscoveryIssue {
  url: string;
  stage: string;
  category: string;
  status: number | null;
  message: string;
}

export interface DiscoveryOutput {
  query: Record<string, string>;
  queries: string[];
  searched: number;
  candidates_found: number;
  jobs_created: number;
  jobs_updated: number;
  jobs_skipped: number;
  errors: string[];
  job_ids: string[];
  duplicates_skipped: number;
  scrape_attempts: number;
  extractions_succeeded: number;
  /** Missing from discovery outputs persisted before this counter was added. */
  page_validation_failures?: number;
  listings_skipped: number;
  invalid_results: number;
  malformed_results: number;
  scrape_failures: number;
  issues: DiscoveryIssue[];
}

export interface JobAnalysis {
  summary: string;
  confidence: number;
  required_skills: string[];
  preferred_skills: string[];
  responsibilities: string[];
  qualifications: string[];
  experience_years_min: number | null;
  experience_years_max: number | null;
  seniority: string | null;
  remote_type: string | null;
  domain_keywords: string[];
  technologies: string[];
  education_requirements: string[];
  certifications: string[];
  ambiguities: string[];
}

export interface JobAnalysisOutput {
  job_id: UUID;
  prompt_version: string;
  model: string | null;
  analysis: JobAnalysis;
}

export type MatchVerdict =
  | "strong_match"
  | "good_match"
  | "partial_match"
  | "weak_match"
  | "poor_match";

export type MatchSignal = "matched" | "mismatched" | "unclear";

export interface MatchResult {
  score: number;
  verdict: MatchVerdict;
  matched_skills: string[];
  missing_required_skills: string[];
  missing_preferred_skills: string[];
  strengths: string[];
  gaps: string[];
  experience_match: MatchSignal;
  education_match: MatchSignal;
  location_match: MatchSignal;
  seniority_match: MatchSignal;
  rationale: string;
  confidence: number;
}

export interface MatchEnvelope {
  job_id: UUID;
  candidate_id: UUID;
  resume_document_id: UUID;
  job_analysis_id: UUID;
  prompt_version: string;
  model: string | null;
  match: MatchResult;
}

export interface CoverLetterResult {
  content: string;
  confidence: number;
}

export interface CoverLetterEnvelope {
  job_id: UUID;
  candidate_id: UUID;
  master_resume_document_id: UUID;
  match_id: UUID;
  document_id: UUID;
  prompt_version: string;
  model: string | null;
  cover_letter: CoverLetterResult;
}

export type ApplicationStatus =
  | "draft"
  | "applied"
  | "interview"
  | "offer"
  | "rejected"
  | "withdrawn";

export const APPLICATION_STATUSES: ApplicationStatus[] = [
  "draft",
  "applied",
  "interview",
  "offer",
  "rejected",
  "withdrawn",
];

export interface Application {
  id: UUID;
  job_id: UUID | null;
  status: ApplicationStatus;
  applied_at: string | null;
  job_title_snapshot: string | null;
  company_name_snapshot: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationListPage {
  items: Application[];
  total: number;
  limit: number;
  offset: number;
}

export interface ApplicationCreateRequest {
  job_id?: UUID;
  status?: ApplicationStatus;
  notes?: string | null;
}

export interface ApplicationUpdateRequest {
  status?: ApplicationStatus;
  notes?: string | null;
}
