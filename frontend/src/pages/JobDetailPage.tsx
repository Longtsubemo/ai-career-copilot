import { useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  analyzeJob,
  generateCoverLetter,
  matchJob,
} from "../api/agents";
import { getJob, verifyJob } from "../api/jobs";
import {
  createApplication,
  listApplications,
  updateApplication,
  type CreatedApplication,
} from "../api/applications";
import { AnalysisCard } from "../components/job/AnalysisCard";
import { ApplicationCard } from "../components/job/ApplicationCard";
import { ApplicationWorkflowGuide } from "../components/job/ApplicationWorkflowGuide";
import { CoverLetterCard } from "../components/job/CoverLetterCard";
import { MatchCard } from "../components/job/MatchCard";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody } from "../components/ui/Card";
import { ErrorAlert } from "../components/ui/ErrorAlert";
import { EmptyState, LoadingState } from "../components/ui/EmptyState";
import { Icon } from "../components/ui/Icon";
import { useAsync } from "../hooks/useAsync";
import { useFetch } from "../hooks/useFetch";
import type {
  AgentRun,
  Application,
  ApplicationStatus,
  CoverLetterEnvelope,
  Job,
  JobAnalysis,
  JobAnalysisOutput,
  JobVerificationResult,
  MatchEnvelope,
} from "../types/api";
import {
  AVAILABILITY_META,
  CRAWL_STATUS_META,
  VERIFICATION_OUTCOME_META,
} from "../utils/labels";
import { formatDate, formatDateTime } from "../utils/format";

function Description({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 700;

  return (
    <div>
      <div
        className={`whitespace-pre-wrap text-sm leading-6 text-slate-700 ${
          long && !expanded ? "max-h-64 overflow-hidden" : ""
        }`}
      >
        {text}
      </div>
      {long ? (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-3 text-sm font-medium text-indigo-600 hover:text-indigo-500"
        >
          {expanded ? "Show less" : "Show full description"}
        </button>
      ) : null}
    </div>
  );
}

export function JobDetailPage() {
  const { jobId = "" } = useParams();
  const job = useFetch(() => getJob(jobId), [jobId]);
  const applications = useFetch(() => listApplications({ limit: 100 }), []);

  const [analysis, setAnalysis] = useState<JobAnalysis | null>(null);
  const [match, setMatch] = useState<MatchEnvelope | null>(null);
  const [letter, setLetter] = useState<CoverLetterEnvelope | null>(null);
  const [tracked, setTracked] = useState<Application | null>(null);

  const analyzeRun = useAsync(() => analyzeJob(jobId), {
    onSuccess: (run: AgentRun<JobAnalysisOutput>) => {
      if (run.output) {
        setAnalysis(run.output.analysis);
      }
    },
  });
  const matchRun = useAsync(() => matchJob(jobId), {
    onSuccess: (run: AgentRun<MatchEnvelope>) => {
      if (run.output) {
        setMatch(run.output);
      }
    },
  });
  const letterRun = useAsync(() => generateCoverLetter(jobId), {
    onSuccess: (run: AgentRun<CoverLetterEnvelope>) => {
      if (run.output) {
        setLetter(run.output);
      }
    },
  });
  const trackRun = useAsync(
    () => createApplication({ job_id: jobId, status: "draft" }),
    {
      onSuccess: ({ application: created }: CreatedApplication) => {
        setTracked(created);
        void applications.reload(true);
      },
    },
  );
  const statusRun = useAsync(
    (applicationId: string, status: ApplicationStatus) =>
      updateApplication(applicationId, { status }),
    { onSuccess: (application: Application) => setTracked(application) },
  );
  const notesRun = useAsync(
    (applicationId: string, notes: string) =>
      updateApplication(applicationId, { notes }),
    { onSuccess: (application: Application) => setTracked(application) },
  );
  const verifyRun = useAsync(() => verifyJob(jobId), {
    onSuccess: (_result: JobVerificationResult) => {
      void job.reload(true);
    },
  });

  const listedApplication =
    applications.data?.items.find((item) => item.job_id === jobId) ?? null;
  const application = tracked ?? listedApplication;

  const busy =
    analyzeRun.pending ||
    matchRun.pending ||
    letterRun.pending ||
    trackRun.pending ||
    statusRun.pending ||
    notesRun.pending ||
    verifyRun.pending;

  if (job.loading) {
    return <LoadingState message="Loading job…" />;
  }

  if (job.error) {
    if (job.error.status === 404) {
      return (
        <EmptyState
          title="Job not found"
          description="This job does not exist any more. It may have been removed."
          action={
            <Link to="/jobs">
              <Button variant="secondary">Back to jobs</Button>
            </Link>
          }
        />
      );
    }
    return (
      <ErrorAlert
        message={job.error.message}
        action={
          <Button size="sm" variant="secondary" onClick={() => void job.reload()}>
            Retry
          </Button>
        }
      />
    );
  }

  if (!job.data) {
    return <LoadingState message="Loading job…" />;
  }

  const posting: Job = job.data;
  const crawl = CRAWL_STATUS_META[posting.crawl_status];
  const availability = AVAILABILITY_META[posting.availability];

  return (
    <div className="space-y-6">
      <div>
        <Link
          to="/jobs"
          className="inline-flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700"
        >
          <Icon name="chevronLeft" className="h-4 w-4" />
          Back to jobs
        </Link>

        <div className="mt-3 flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
              {posting.title}
            </h1>
            <p className="mt-1 text-sm text-slate-600">
              {posting.company_name ?? "Company not recorded"}
              {posting.location ? ` · ${posting.location}` : ""}
              {posting.employment_type ? ` · ${posting.employment_type}` : ""}
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {posting.experience_level ? (
                <Badge tone="neutral">{posting.experience_level}</Badge>
              ) : null}
              <Badge tone="muted">{posting.source}</Badge>
              <Badge tone={availability.tone}>{availability.label}</Badge>
              <Badge tone={crawl.tone}>{crawl.label}</Badge>
              {posting.is_active ? (
                <Badge tone="success">Active</Badge>
              ) : (
                <Badge tone="danger">Inactive</Badge>
              )}
              <span className="text-xs text-slate-400">
                Discovered {formatDate(posting.first_seen_at)}
              </span>
            </div>
          </div>

          <a
            href={posting.job_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            View original posting
            <Icon name="external" className="h-4 w-4" />
          </a>
        </div>
      </div>

      <Card>
        <CardBody>
          {posting.description ? (
            <Description text={posting.description} />
          ) : (
            <p className="text-sm text-slate-500">
              No description was captured for this posting, so there is nothing to
              analyze yet.
            </p>
          )}
          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-500">
            First seen {formatDateTime(posting.first_seen_at)} · Last seen{" "}
            {formatDateTime(posting.last_seen_at)}
            {posting.last_verified_at
              ? ` · Last verified ${formatDateTime(posting.last_verified_at)}`
              : ""}
            {posting.closed_at
              ? ` · Closed ${formatDateTime(posting.closed_at)}`
              : ""}
          </p>
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-slate-900">
                Availability check
              </p>
              <p className="mt-1 text-xs text-slate-500">
                The stored posting is re-fetched and judged again. This is a
                point-in-time check — it does not run on a schedule.
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <Badge tone={availability.tone}>{availability.label}</Badge>
                <span className="text-xs text-slate-400">
                  {posting.last_verified_at
                    ? `Last checked ${formatDateTime(posting.last_verified_at)}`
                    : "Never manually checked"}
                </span>
              </div>
              {verifyRun.error ? (
                <div className="mt-2 max-w-prose">
                  <ErrorAlert message={verifyRun.error.message} />
                </div>
              ) : null}
            </div>
            <Button
              variant="secondary"
              onClick={() => void verifyRun.run()}
              loading={verifyRun.pending}
              disabled={busy}
            >
              Re-verify now
            </Button>
          </div>
          {verifyRun.data ? (
            <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 px-3 py-2">
              <Badge
                tone={VERIFICATION_OUTCOME_META[verifyRun.data.outcome].tone}
              >
                {VERIFICATION_OUTCOME_META[verifyRun.data.outcome].label}
              </Badge>
              <p className="mt-1.5 text-xs leading-5 text-slate-600">
                {verifyRun.data.message}
              </p>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardBody>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              onClick={() => void analyzeRun.run()}
              loading={analyzeRun.pending}
              disabled={busy}
            >
              Analyze job
            </Button>
            <Button
              onClick={() => void matchRun.run()}
              loading={matchRun.pending}
              disabled={busy}
            >
              Match me
            </Button>
            <Button
              onClick={() => void letterRun.run()}
              loading={letterRun.pending}
              disabled={busy}
            >
              Generate cover letter
            </Button>
            {application ? (
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() =>
                  document
                    .getElementById("application")
                    ?.scrollIntoView({ behavior: "smooth", block: "start" })
                }
              >
                Application tracked
              </Button>
            ) : (
              <Button
                variant="secondary"
                onClick={() => void trackRun.run()}
                loading={trackRun.pending}
                disabled={busy}
              >
                Track application
              </Button>
            )}
          </div>
          <p className="mt-3 text-xs text-slate-500">
            Everything runs through your FastAPI backend — matching and letters are
            never generated in the browser.
          </p>
        </CardBody>
      </Card>

      <AnalysisCard
        analysis={analysis}
        pending={analyzeRun.pending}
        error={analyzeRun.error}
        onAnalyze={() => void analyzeRun.run()}
      />

      <MatchCard
        envelope={match}
        pending={matchRun.pending}
        error={matchRun.error}
        onMatch={() => void matchRun.run()}
        onAnalyze={() => void analyzeRun.run()}
      />

      <CoverLetterCard
        envelope={letter}
        pending={letterRun.pending}
        error={letterRun.error}
        onGenerate={() => void letterRun.run()}
        onMatch={() => void matchRun.run()}
      />

      <ApplicationWorkflowGuide
        jobUrl={posting.job_url}
        applied={application?.applied_at != null}
      />

      <ApplicationCard
        application={application}
        creating={trackRun.pending}
        createError={trackRun.error}
        updating={statusRun.pending || notesRun.pending}
        updateError={statusRun.error ?? notesRun.error}
        onTrack={() => void trackRun.run()}
        onStatusChange={(status) => {
          if (application) {
            void statusRun.run(application.id, status);
          }
        }}
        onNotesSave={(notes) => {
          if (application) {
            void notesRun.run(application.id, notes);
          }
        }}
      />
    </div>
  );
}
