import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  createApplication,
  listApplications,
  updateApplication,
  type CreatedApplication,
} from "../api/applications";
import { listJobs } from "../api/jobs";
import { AppliedConfirmation } from "../components/job/AppliedConfirmation";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody, CardHeader } from "../components/ui/Card";
import { ErrorAlert, SuccessNote } from "../components/ui/ErrorAlert";
import { EmptyState, LoadingState } from "../components/ui/EmptyState";
import { Field, Select, TextArea } from "../components/ui/Field";
import { Icon } from "../components/ui/Icon";
import { PageHeader } from "../components/ui/PageHeader";
import { useAsync } from "../hooks/useAsync";
import { useFetch } from "../hooks/useFetch";
import {
  APPLICATION_STATUSES,
  type Application,
  type ApplicationStatus,
} from "../types/api";
import { APPLICATION_STATUS_META } from "../utils/labels";
import { formatDateTime } from "../utils/format";
import { requiresAppliedConfirmation } from "../utils/applicationWorkflow";

const PAGE_SIZE = 10;

function ApplicationRow({
  application,
  onUpdated,
}: {
  application: Application;
  onUpdated: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [notes, setNotes] = useState(application.notes ?? "");
  const [confirmingApplied, setConfirmingApplied] = useState(false);

  useEffect(() => {
    setNotes(application.notes ?? "");
  }, [application]);

  useEffect(() => {
    setConfirmingApplied(false);
  }, [application.status, application.id]);

  const statusRun = useAsync(
    (status: ApplicationStatus) =>
      updateApplication(application.id, { status }),
    { onSuccess: () => onUpdated() },
  );
  const notesRun = useAsync(
    (value: string) => updateApplication(application.id, { notes: value }),
    { onSuccess: () => onUpdated() },
  );

  const meta = APPLICATION_STATUS_META[application.status];
  const updating = statusRun.pending || notesRun.pending;
  const error = statusRun.error ?? notesRun.error;
  const notesUnchanged = notes === (application.notes ?? "");
  const currentStatus = application.status;
  const jobLabel = application.job_title_snapshot ?? "this job";

  function handleStatusChange(next: ApplicationStatus) {
    if (requiresAppliedConfirmation(currentStatus, next)) {
      setConfirmingApplied(true);
      return;
    }
    void statusRun.run(next);
  }

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="truncate text-sm font-semibold text-slate-900">
              {application.job_title_snapshot ?? "Job application"}
            </p>
            <Badge tone={meta.tone}>{meta.label}</Badge>
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {application.company_name_snapshot ?? "Company not recorded"} · Created{" "}
            {formatDateTime(application.created_at)}
            {application.applied_at
              ? ` · Applied ${formatDateTime(application.applied_at)}`
              : ""}
          </p>
          {application.notes && !expanded ? (
            <p className="mt-2 line-clamp-2 text-sm text-slate-600">
              {application.notes}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select
            aria-label={`Status for ${application.job_title_snapshot ?? "application"}`}
            className="w-36"
            value={application.status}
            disabled={updating}
            onChange={(event) =>
              handleStatusChange(event.target.value as ApplicationStatus)
            }
          >
            {APPLICATION_STATUSES.map((status) => (
              <option key={status} value={status}>
                {APPLICATION_STATUS_META[status].label}
              </option>
            ))}
          </Select>
          {currentStatus !== "applied" ? (
            <Button
              size="sm"
              variant="secondary"
              disabled={updating}
              onClick={() => setConfirmingApplied(true)}
            >
              Mark applied
            </Button>
          ) : null}
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Close" : "Notes"}
          </Button>
          {application.job_id ? (
            <Link to={`/jobs/${application.job_id}`}>
              <Button size="sm" variant="ghost">
                Open job
              </Button>
            </Link>
          ) : null}
        </div>
      </div>

      {confirmingApplied ? (
        <div className="border-t border-slate-100 px-5 py-4">
          <AppliedConfirmation
            jobLabel={jobLabel}
            pending={updating}
            onConfirm={() => {
              setConfirmingApplied(false);
              void statusRun.run("applied");
            }}
            onCancel={() => setConfirmingApplied(false)}
          />
        </div>
      ) : null}

      {expanded ? (
        <div className="border-t border-slate-100 px-5 py-4">
          <Field label="Notes" htmlFor={`notes-${application.id}`}>
            <TextArea
              id={`notes-${application.id}`}
              rows={3}
              value={notes}
              disabled={updating}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Referral, recruiter contact, interview dates…"
            />
          </Field>
          <div className="mt-2 flex items-center gap-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={updating || notesUnchanged}
              loading={notesRun.pending}
              onClick={() => void notesRun.run(notes)}
            >
              Save notes
            </Button>
            {notesUnchanged ? (
              <span className="text-xs text-slate-500">No unsaved changes</span>
            ) : null}
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="px-5 pb-4">
          <ErrorAlert message={error.message} onDismiss={statusRun.resetError} />
        </div>
      ) : null}
    </Card>
  );
}

function TrackForm({
  onDone,
  onCreated,
}: {
  onDone: () => void;
  onCreated: (created: boolean) => void;
}) {
  const jobs = useFetch(() => listJobs({ limit: 100 }), []);
  const [jobId, setJobId] = useState("");

  const { run: track, pending, error } = useAsync(
    (selected: string) => createApplication({ job_id: selected, status: "draft" }),
    {
      onSuccess: ({ created }: CreatedApplication) => {
        onCreated(created);
        onDone();
      },
    },
  );

  const jobItems = jobs.data?.items ?? [];

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!jobId) {
      return;
    }
    void track(jobId);
  }

  return (
    <Card>
      <CardHeader
        title="Track a job"
        description="Creates a draft application — only when you choose to."
        actions={
          <Button size="sm" variant="ghost" onClick={onDone}>
            Cancel
          </Button>
        }
      />
      <CardBody>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error ? <ErrorAlert message={error.message} /> : null}

          {jobs.loading ? (
            <LoadingState message="Loading jobs…" />
          ) : jobItems.length === 0 ? (
            <p className="text-sm text-slate-500">
              No jobs discovered yet.{" "}
              <Link to="/jobs" className="text-indigo-600 hover:text-indigo-500">
                Search for jobs first
              </Link>
              .
            </p>
          ) : (
            <Field label="Job" htmlFor="track-job">
              <Select
                id="track-job"
                value={jobId}
                onChange={(event) => setJobId(event.target.value)}
              >
                <option value="">Choose a job…</option>
                {jobItems.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.title}
                    {job.company_name ? ` — ${job.company_name}` : ""}
                    {job.location ? ` (${job.location})` : ""}
                  </option>
                ))}
              </Select>
            </Field>
          )}

          <Button type="submit" loading={pending} disabled={!jobId}>
            Create application
          </Button>
        </form>
      </CardBody>
    </Card>
  );
}

export function ApplicationsPage() {
  const [statusFilter, setStatusFilter] = useState("");
  const [offset, setOffset] = useState(0);
  const [tracking, setTracking] = useState(false);
  const [created, setCreated] = useState<string | null>(null);

  const applications = useFetch(
    () =>
      listApplications({
        status: statusFilter || undefined,
        limit: PAGE_SIZE,
        offset,
      }),
    [statusFilter, offset],
  );

  const total = applications.data?.total ?? 0;
  const items = applications.data?.items ?? [];
  const firstIndex = total === 0 ? 0 : offset + 1;
  const lastIndex = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Applications"
        description="Your record of every application you track. You apply on the employer's site — this app never submits for you."
        actions={
          <Button onClick={() => setTracking(true)}>
            <Icon name="plus" className="h-4 w-4" />
            Track a job
          </Button>
        }
      />

      {created ? <SuccessNote message={created} /> : null}

      {tracking ? (
        <TrackForm
          onDone={() => setTracking(false)}
          onCreated={(wasCreated) => {
            setCreated(
              wasCreated
                ? "Draft application created. It is only marked applied when you confirm you submitted it."
                : "You already track this job — opening the existing record.",
            );
            void applications.reload();
          }}
        />
      ) : null}

      <Card>
        <CardHeader
          title="Tracked applications"
          description={
            applications.loading
              ? "Loading applications…"
              : `${total} application${total === 1 ? "" : "s"}`
          }
          actions={
            <Select
              aria-label="Filter by status"
              className="w-40"
              value={statusFilter}
              onChange={(event) => {
                setOffset(0);
                setStatusFilter(event.target.value);
              }}
            >
              <option value="">All statuses</option>
              {APPLICATION_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {APPLICATION_STATUS_META[status].label}
                </option>
              ))}
            </Select>
          }
        />

        <CardBody className="space-y-4">
          {applications.loading ? (
            <LoadingState message="Loading applications…" />
          ) : applications.error ? (
            <ErrorAlert
              message={applications.error.message}
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void applications.reload()}
                >
                  Retry
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <EmptyState
              compact
              title={
                statusFilter ? "No applications with this status" : "No applications yet"
              }
              description={
                statusFilter
                  ? "Change the status filter to see the rest."
                  : "Applications you choose to track will appear here."
              }
              action={
                statusFilter ? (
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setOffset(0);
                      setStatusFilter("");
                    }}
                  >
                    Show all statuses
                  </Button>
                ) : (
                  <Button onClick={() => setTracking(true)}>Track a job</Button>
                )
              }
            />
          ) : (
            <div className="space-y-4">
              {items.map((application) => (
                <ApplicationRow
                  key={application.id}
                  application={application}
                  onUpdated={() => void applications.reload(true)}
                />
              ))}
            </div>
          )}
        </CardBody>

        {total > PAGE_SIZE ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 sm:px-6">
            <p className="text-xs text-slate-500">
              Showing {firstIndex}–{lastIndex} of {total}
            </p>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                <Icon name="chevronLeft" className="h-4 w-4" />
                Previous
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
                <Icon name="chevronRight" className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ) : null}
      </Card>
    </div>
  );
}
