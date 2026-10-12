import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { discoverJobs } from "../api/agents";
import { listJobs, type JobQuery } from "../api/jobs";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody, CardHeader } from "../components/ui/Card";
import { ErrorAlert } from "../components/ui/ErrorAlert";
import { EmptyState, LoadingState } from "../components/ui/EmptyState";
import { Field, Select, TextInput } from "../components/ui/Field";
import { Icon } from "../components/ui/Icon";
import { PageHeader } from "../components/ui/PageHeader";
import { Spinner } from "../components/ui/Spinner";
import { useAsync } from "../hooks/useAsync";
import { useFetch } from "../hooks/useFetch";
import type { AgentRun, AvailabilityStatus, DiscoveryOutput, DiscoveryRequest, Job } from "../types/api";
import { AVAILABILITY_META, CRAWL_STATUS_META } from "../utils/labels";
import { formatDate } from "../utils/format";

const PAGE_SIZE = 10;

interface Filters {
  location: string;
  experience: string;
  active: string;
  availability: string;
}

const EMPTY_FILTERS: Filters = {
  location: "",
  experience: "",
  active: "",
  availability: "",
};

function buildParams(filters: Filters, offset: number): JobQuery {
  const params: JobQuery = { limit: PAGE_SIZE, offset };
  if (filters.location.trim()) {
    params.location = filters.location.trim();
  }
  if (filters.experience.trim()) {
    params.experience_level = filters.experience.trim();
  }
  if (filters.active) {
    params.is_active = filters.active === "active";
  }
  if (filters.availability) {
    params.availability = filters.availability as AvailabilityStatus;
  }
  return params;
}

function JobRow({ job }: { job: Job }) {
  const crawl = CRAWL_STATUS_META[job.crawl_status];
  const availability = AVAILABILITY_META[job.availability];
  return (
    <Link
      to={`/jobs/${job.id}`}
      className="group flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4 transition-colors last:border-b-0 hover:bg-slate-50"
    >
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-slate-900 group-hover:text-indigo-600">
          {job.title}
        </p>
        <p className="mt-0.5 truncate text-sm text-slate-600">
          {job.company_name ?? "Company not recorded"}
          {job.location ? ` · ${job.location}` : ""}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {job.experience_level ? (
            <Badge tone="neutral">{job.experience_level}</Badge>
          ) : null}
          {job.employment_type ? (
            <Badge tone="muted">{job.employment_type}</Badge>
          ) : null}
          <Badge tone="muted">{job.source}</Badge>
          <Badge tone={availability.tone}>{availability.label}</Badge>
          <Badge tone={crawl.tone}>{crawl.label}</Badge>
          {job.is_active ? null : <Badge tone="danger">Inactive</Badge>}
          <span className="text-xs text-slate-400">
            Found {formatDate(job.first_seen_at)}
          </span>
        </div>
      </div>
      <Icon
        name="chevronRight"
        className="mt-1 h-5 w-5 shrink-0 text-slate-300 transition-colors group-hover:text-indigo-500"
      />
    </Link>
  );
}

function DiscoverySummary({ output }: { output: DiscoveryOutput }) {
  const stats = [
    { label: "Results checked", value: output.candidates_found },
    { label: "New jobs", value: output.jobs_created },
    { label: "Refreshed", value: output.jobs_updated },
    { label: "Filtered out", value: output.jobs_skipped },
    { label: "Not a vacancy", value: output.listings_skipped },
    { label: "Duplicates", value: output.duplicates_skipped },
  ];
  const unreadable =
    output.scrape_failures + output.invalid_results + output.malformed_results;

  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4">
      <p className="text-sm font-medium text-emerald-800">
        Search complete — {output.searched} result
        {output.searched === 1 ? "" : "s"} checked.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {stats.map((stat) => (
          <div key={stat.label} className="rounded-md bg-white/70 px-3 py-2">
            <p className="text-lg font-semibold text-slate-900">{stat.value}</p>
            <p className="text-xs text-slate-500">{stat.label}</p>
          </div>
        ))}
      </div>
      {unreadable > 0 ? (
        <p className="mt-3 text-xs text-emerald-900">
          {unreadable} listing{unreadable === 1 ? "" : "s"} could not be read or
          parsed.
        </p>
      ) : null}
      {output.errors.length ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs font-medium text-emerald-800">
            {output.errors.length} issue{output.errors.length === 1 ? "" : "s"}{" "}
            reported
          </summary>
          <ul className="mt-2 list-inside list-disc space-y-1 text-xs text-emerald-900">
            {output.errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export function JobsPage() {
  const [role, setRole] = useState("");
  const [searchLocation, setSearchLocation] = useState("");
  const [searchExperience, setSearchExperience] = useState("");
  const [summary, setSummary] = useState<DiscoveryOutput | null>(null);

  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [draftFilters, setDraftFilters] = useState<Filters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);

  const jobs = useFetch(() => listJobs(buildParams(filters, offset)), [
    filters,
    offset,
  ]);

  const { run: search, pending: searching, error: searchError } = useAsync(
    (request: DiscoveryRequest) => discoverJobs(request),
    {
      onSuccess: (run: AgentRun<DiscoveryOutput>) => {
        if (run.output) {
          setSummary(run.output);
        }
        void jobs.reload(true);
      },
    },
  );

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    setSummary(null);
    void search({
      role: role.trim(),
      location: searchLocation.trim(),
      experience: searchExperience.trim(),
    });
  }

  function applyFilters(event: FormEvent) {
    event.preventDefault();
    setOffset(0);
    setFilters({ ...draftFilters });
  }

  function clearFilters() {
    setDraftFilters(EMPTY_FILTERS);
    setOffset(0);
    setFilters(EMPTY_FILTERS);
  }

  const total = jobs.data?.total ?? 0;
  const items = jobs.data?.items ?? [];
  const firstIndex = total === 0 ? 0 : offset + 1;
  const lastIndex = Math.min(offset + PAGE_SIZE, total);
  const hasFilters =
    filters.location !== "" ||
    filters.experience !== "" ||
    filters.active !== "" ||
    filters.availability !== "";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Jobs"
        description="Search the web for roles, then open a job to analyze it."
      />

      <Card>
        <CardHeader
          title="Search for jobs"
          description="One search covers a role, a location and an experience level."
        />
        <CardBody>
          <form onSubmit={handleSearch} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-3">
              <Field label="Role" required htmlFor="discovery-role">
                <TextInput
                  id="discovery-role"
                  value={role}
                  onChange={(event) => setRole(event.target.value)}
                  placeholder="Backend engineer"
                  disabled={searching}
                />
              </Field>
              <Field label="Location" required htmlFor="discovery-location">
                <TextInput
                  id="discovery-location"
                  value={searchLocation}
                  onChange={(event) => setSearchLocation(event.target.value)}
                  placeholder="Bangalore"
                  disabled={searching}
                />
              </Field>
              <Field label="Experience" required htmlFor="discovery-experience">
                <TextInput
                  id="discovery-experience"
                  value={searchExperience}
                  onChange={(event) => setSearchExperience(event.target.value)}
                  placeholder="1-3 years"
                  disabled={searching}
                />
              </Field>
            </div>

            {searchError ? <ErrorAlert message={searchError.message} /> : null}
            {summary ? <DiscoverySummary output={summary} /> : null}

            <div className="flex flex-wrap items-center gap-3">
              <Button
                type="submit"
                loading={searching}
                disabled={!role.trim() || !searchLocation.trim() || !searchExperience.trim()}
              >
                {searching ? "Searching…" : "Search jobs"}
              </Button>
              {searching ? (
                <span className="flex items-center gap-2 text-sm text-slate-500">
                  <Spinner className="h-4 w-4 text-indigo-500" />
                  Searching and reading listings — this can take a minute.
                </span>
              ) : (
                <span className="text-xs text-slate-500">
                  Results are added to the list below.
                </span>
              )}
            </div>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Discovered jobs"
          description={
            jobs.loading
              ? "Loading jobs…"
              : `${total} job${total === 1 ? "" : "s"} found`
          }
          actions={
            <Button
              size="sm"
              variant="secondary"
              onClick={() => void jobs.reload()}
              loading={jobs.loading && !jobs.data}
            >
              Refresh
            </Button>
          }
        />

        <CardBody className="border-b border-slate-100">
          <form
            onSubmit={applyFilters}
            className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5"
          >
            <Field label="Location" htmlFor="filter-location">
              <TextInput
                id="filter-location"
                value={draftFilters.location}
                onChange={(event) =>
                  setDraftFilters({ ...draftFilters, location: event.target.value })
                }
                placeholder="Contains, e.g. Bangalore"
              />
            </Field>
            <Field label="Experience" htmlFor="filter-experience">
              <TextInput
                id="filter-experience"
                value={draftFilters.experience}
                onChange={(event) =>
                  setDraftFilters({
                    ...draftFilters,
                    experience: event.target.value,
                  })
                }
                placeholder="Exact, e.g. 1-3 years"
              />
            </Field>
            <Field label="State" htmlFor="filter-active">
              <Select
                id="filter-active"
                value={draftFilters.active}
                onChange={(event) =>
                  setDraftFilters({ ...draftFilters, active: event.target.value })
                }
              >
                <option value="">Any</option>
                <option value="active">Active only</option>
                <option value="inactive">Inactive only</option>
              </Select>
            </Field>
            <Field label="Availability" htmlFor="filter-availability">
              <Select
                id="filter-availability"
                value={draftFilters.availability}
                onChange={(event) =>
                  setDraftFilters({
                    ...draftFilters,
                    availability: event.target.value,
                  })
                }
              >
                <option value="">Any</option>
                <option value="available">
                  {AVAILABILITY_META.available.label}
                </option>
                <option value="stale">{AVAILABILITY_META.stale.label}</option>
                <option value="closed">{AVAILABILITY_META.closed.label}</option>
                <option value="unknown">{AVAILABILITY_META.unknown.label}</option>
              </Select>
            </Field>
            <div className="flex items-end gap-2">
              <Button type="submit" variant="secondary" className="flex-1">
                Apply filters
              </Button>
              {hasFilters ? (
                <Button type="button" variant="ghost" onClick={clearFilters}>
                  Clear
                </Button>
              ) : null}
            </div>
          </form>
        </CardBody>

        {jobs.loading ? (
          <LoadingState message="Loading jobs…" />
        ) : jobs.error ? (
          <div className="px-5 py-6 sm:px-6">
            <ErrorAlert
              message={jobs.error.message}
              action={
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void jobs.reload()}
                >
                  Retry
                </Button>
              }
            />
          </div>
        ) : items.length === 0 ? (
          <div className="px-5 py-6 sm:px-6">
            <EmptyState
              compact
              title={hasFilters ? "No jobs match these filters" : "No jobs discovered yet"}
              description={
                hasFilters
                  ? "Try a different location or experience level."
                  : "Search for your first set of jobs — results appear here."
              }
              action={
                hasFilters ? (
                  <Button variant="secondary" onClick={clearFilters}>
                    Clear filters
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div>
            {items.map((job) => (
              <JobRow key={job.id} job={job} />
            ))}
          </div>
        )}

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
