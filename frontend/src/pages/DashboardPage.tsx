import { Link } from "react-router-dom";

import { listApplications } from "../api/applications";
import { listJobs } from "../api/jobs";
import { getCurrentResumeOrNull } from "../api/resumes";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody, CardHeader } from "../components/ui/Card";
import { ErrorAlert } from "../components/ui/ErrorAlert";
import { LoadingState } from "../components/ui/EmptyState";
import { Icon } from "../components/ui/Icon";
import { PageHeader } from "../components/ui/PageHeader";
import { useCandidate } from "../hooks/useCandidate";
import { useFetch } from "../hooks/useFetch";
import { APPLICATION_STATUSES } from "../types/api";
import { formatDate } from "../utils/format";
import { APPLICATION_STATUS_META } from "../utils/labels";
import { isProfileIncomplete } from "../utils/profile";

function StatCard({
  label,
  value,
  hint,
  to,
}: {
  label: string;
  value: string;
  hint: string;
  to: string;
}) {
  return (
    <Link
      to={to}
      className="group rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition-colors hover:border-indigo-300"
    >
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
        {value}
      </p>
      <p className="mt-1 text-xs text-slate-500 group-hover:text-indigo-600">{hint}</p>
    </Link>
  );
}

export function DashboardPage() {
  const { candidate } = useCandidate();
  const jobs = useFetch(() => listJobs({ limit: 5 }), []);
  const applications = useFetch(() => listApplications({ limit: 100 }), []);
  const resume = useFetch(getCurrentResumeOrNull, []);

  const recentJobs = jobs.data?.items ?? [];
  const jobTotal = jobs.data?.total ?? 0;
  const applicationPage = applications.data;
  const applicationTotal = applicationPage?.total ?? 0;

  const counts = new Map<string, number>();
  for (const item of applicationPage?.items ?? []) {
    counts.set(item.status, (counts.get(item.status) ?? 0) + 1);
  }
  const activeCounts = APPLICATION_STATUSES.filter(
    (status) => (counts.get(status) ?? 0) > 0,
  );

  const error = jobs.error ?? applications.error ?? resume.error;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Your job search at a glance."
        actions={
          <Link to="/jobs">
            <Button>
              <Icon name="search" className="h-4 w-4" />
              Search jobs
            </Button>
          </Link>
        }
      />

      {error ? (
        <ErrorAlert
          message={error.message}
          action={
            <Button
              size="sm"
              variant="secondary"
              onClick={() => {
                void jobs.reload();
                void applications.reload();
                void resume.reload();
              }}
            >
              Retry
            </Button>
          }
        />
      ) : null}

      {resume.data === null && !resume.loading ? (
        <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-5 py-4 sm:flex sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-indigo-900">
              Upload your resume to start matching jobs.
            </p>
            <p className="mt-0.5 text-sm text-indigo-700">
              Matching and cover letters are grounded in your master resume.
            </p>
          </div>
          <Link to="/resume" className="mt-3 block sm:mt-0">
            <Button>Upload resume</Button>
          </Link>
        </div>
      ) : null}

      {isProfileIncomplete(candidate) ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 sm:flex sm:items-center sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-amber-900">
              Your profile is missing matching evidence.
            </p>
            <p className="mt-0.5 text-sm text-amber-800">
              Add a headline, summary, skills or years of experience for better
              matches.
            </p>
          </div>
          <Link to="/profile" className="mt-3 block sm:mt-0">
            <Button variant="secondary">Complete profile</Button>
          </Link>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          label="Jobs discovered"
          value={jobs.loading ? "…" : String(jobTotal)}
          hint="Open a job to analyze it"
          to="/jobs"
        />
        <StatCard
          label="Applications"
          value={applications.loading ? "…" : String(applicationTotal)}
          hint="Track the ones you choose"
          to="/applications"
        />
        <StatCard
          label="Master resume"
          value={resume.loading ? "…" : resume.data ? "Ready" : "Missing"}
          hint={resume.data ? "Used for every match" : "Text or Markdown, up to 2 MB"}
          to="/resume"
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Recent jobs"
            description="The newest listings discovered by your searches."
            actions={
              <Link
                to="/jobs"
                className="text-sm font-medium text-indigo-600 hover:text-indigo-500"
              >
                View all
              </Link>
            }
          />
          <CardBody>
            {jobs.loading ? (
              <LoadingState />
            ) : recentJobs.length === 0 ? (
              <p className="text-sm text-slate-500">
                Search for your first set of jobs — they show up here.
              </p>
            ) : (
              <ul className="divide-y divide-slate-100">
                {recentJobs.map((job) => (
                  <li key={job.id}>
                    <Link
                      to={`/jobs/${job.id}`}
                      className="group flex items-center justify-between gap-3 py-3 first:pt-0 last:pb-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-slate-900 group-hover:text-indigo-600">
                          {job.title}
                        </p>
                        <p className="truncate text-xs text-slate-500">
                          {job.company_name ?? "Company not recorded"}
                          {job.location ? ` · ${job.location}` : ""}
                        </p>
                      </div>
                      <span className="shrink-0 text-xs text-slate-400">
                        {formatDate(job.first_seen_at)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Applications"
            description="Where your tracked applications stand."
            actions={
              <Link
                to="/applications"
                className="text-sm font-medium text-indigo-600 hover:text-indigo-500"
              >
                View all
              </Link>
            }
          />
          <CardBody>
            {applications.loading ? (
              <LoadingState />
            ) : applicationTotal === 0 ? (
              <p className="text-sm text-slate-500">
                Applications you choose to track will appear here.
              </p>
            ) : activeCounts.length === 0 ? (
              <p className="text-sm text-slate-500">No applications yet.</p>
            ) : (
              <ul className="space-y-3">
                {activeCounts.map((status) => {
                  const count = counts.get(status) ?? 0;
                  const meta = APPLICATION_STATUS_META[status];
                  const share = Math.round(
                    (count / (applicationPage?.items.length || 1)) * 100,
                  );
                  return (
                    <li key={status}>
                      <div className="flex items-center justify-between gap-3">
                        <Badge tone={meta.tone}>{meta.label}</Badge>
                        <span className="text-sm font-medium text-slate-700">
                          {count}
                        </span>
                      </div>
                      <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                        <div
                          className="h-full rounded-full bg-indigo-500"
                          style={{ width: `${share}%` }}
                        />
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
            {applicationPage && applicationTotal > applicationPage.items.length ? (
              <p className="mt-3 text-xs text-slate-500">
                Based on the {applicationPage.items.length} most recent applications.
              </p>
            ) : null}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
