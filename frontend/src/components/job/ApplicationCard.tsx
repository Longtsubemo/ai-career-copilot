import { useEffect, useState } from "react";

import type { ApiError } from "../../api/errors";
import type { Application, ApplicationStatus } from "../../types/api";
import { APPLICATION_STATUSES } from "../../types/api";
import { APPLICATION_STATUS_META } from "../../utils/labels";
import { formatDateTime } from "../../utils/format";
import { requiresAppliedConfirmation } from "../../utils/applicationWorkflow";
import { AppliedConfirmation } from "./AppliedConfirmation";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card, CardBody, CardHeader } from "../ui/Card";
import { ErrorAlert } from "../ui/ErrorAlert";
import { EmptyState } from "../ui/EmptyState";
import { Select, TextArea } from "../ui/Field";

export function ApplicationCard({
  application,
  creating,
  createError,
  updating,
  updateError,
  onTrack,
  onStatusChange,
  onNotesSave,
}: {
  application: Application | null;
  creating: boolean;
  createError: ApiError | null;
  updating: boolean;
  updateError: ApiError | null;
  onTrack: () => void;
  onStatusChange: (status: ApplicationStatus) => void;
  onNotesSave: (notes: string) => void;
}) {
  const [notes, setNotes] = useState(application?.notes ?? "");
  const [confirmingApplied, setConfirmingApplied] = useState(false);

  useEffect(() => {
    setNotes(application?.notes ?? "");
  }, [application]);

  useEffect(() => {
    setConfirmingApplied(false);
  }, [application?.status, application?.id]);

  if (!application) {
    return (
      <Card id="application">
        <CardHeader
          title="Application"
          description="Track this job only if you decide to apply."
        />
        <CardBody className="space-y-4">
          {createError ? <ErrorAlert message={createError.message} /> : null}
          <EmptyState
            compact
            title="Not tracked yet"
            description="Applications you choose to track will appear here. Nothing is created automatically."
            action={
              <Button onClick={onTrack} loading={creating}>
                Track this application
              </Button>
            }
          />
        </CardBody>
      </Card>
    );
  }

  const meta = APPLICATION_STATUS_META[application.status];
  const notesUnchanged = notes === (application.notes ?? "");
  const jobLabel = application.job_title_snapshot ?? "this job";
  const currentStatus = application.status;

  function handleStatusChange(next: ApplicationStatus) {
    if (requiresAppliedConfirmation(currentStatus, next)) {
      setConfirmingApplied(true);
      return;
    }
    onStatusChange(next);
  }

  return (
    <Card id="application">
      <CardHeader
        title="Application"
        description="Your tracked state for this job — you control every change."
        actions={<Badge tone={meta.tone}>{meta.label}</Badge>}
      />
      <CardBody className="space-y-5">
        {updateError ? <ErrorAlert message={updateError.message} /> : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label
              htmlFor="application-status"
              className="mb-1.5 block text-sm font-medium text-slate-700"
            >
              Status
            </label>
            <Select
              id="application-status"
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
            {updating ? (
              <p className="mt-1.5 text-xs text-slate-500">Saving…</p>
            ) : null}
          </div>

          <dl className="grid grid-cols-2 gap-3 self-end">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Created
              </dt>
              <dd className="mt-1 text-sm text-slate-900">
                {formatDateTime(application.created_at)}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Applied
              </dt>
              <dd className="mt-1 text-sm text-slate-900">
                {application.applied_at ? formatDateTime(application.applied_at) : "—"}
              </dd>
            </div>
          </dl>
        </div>

        {confirmingApplied ? (
          <AppliedConfirmation
            jobLabel={jobLabel}
            pending={updating}
            onConfirm={() => {
              setConfirmingApplied(false);
              onStatusChange("applied");
            }}
            onCancel={() => setConfirmingApplied(false)}
          />
        ) : application.status !== "applied" ? (
          <div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setConfirmingApplied(true)}
              disabled={updating}
            >
              Mark as applied
            </Button>
            <p className="mt-1.5 text-xs text-slate-500">
              Only after you have submitted on the employer&apos;s site. The server
              records the date.
            </p>
          </div>
        ) : (
          <p className="text-xs text-slate-500">
            Marked as applied on{" "}
            {application.applied_at
              ? formatDateTime(application.applied_at)
              : "an unknown date"}
            . This time is set by the server when you move to Applied.
          </p>
        )}

        <div>
          <label
            htmlFor="application-notes"
            className="mb-1.5 block text-sm font-medium text-slate-700"
          >
            Notes
          </label>
          <TextArea
            id="application-notes"
            rows={4}
            value={notes}
            disabled={updating}
            onChange={(event) => setNotes(event.target.value)}
            placeholder="Referral, recruiter contact, interview dates…"
          />
          <div className="mt-2 flex items-center gap-3">
            <Button
              size="sm"
              variant="secondary"
              disabled={updating || notesUnchanged}
              loading={updating && !notesUnchanged}
              onClick={() => onNotesSave(notes)}
            >
              Save notes
            </Button>
            {notesUnchanged ? (
              <span className="text-xs text-slate-500">No unsaved changes</span>
            ) : null}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
