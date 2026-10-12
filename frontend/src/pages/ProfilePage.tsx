import { useState } from "react";

import { CandidateForm } from "../components/CandidateForm";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody, CardHeader } from "../components/ui/Card";
import { PageHeader } from "../components/ui/PageHeader";
import { useAsync } from "../hooks/useAsync";
import { useCandidate } from "../hooks/useCandidate";
import type { CandidateCreateRequest } from "../types/api";
import { formatDate } from "../utils/format";
import { isProfileIncomplete } from "../utils/profile";

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </dt>
      <dd className="mt-1 text-sm text-slate-900">{value}</dd>
    </div>
  );
}

export function ProfilePage() {
  const { candidate, updateProfile } = useCandidate();
  const [editing, setEditing] = useState(false);
  const { run, pending, error } = useAsync(
    (payload: CandidateCreateRequest) => updateProfile(payload),
    { onSuccess: () => setEditing(false) },
  );

  if (!candidate) {
    return null;
  }

  const incomplete = isProfileIncomplete(candidate);

  return (
    <div>
      <PageHeader
        title="Profile"
        description="The candidate the copilot works for."
        actions={
          editing ? null : (
            <Button variant="secondary" onClick={() => setEditing(true)}>
              Edit profile
            </Button>
          )
        }
      />

      {incomplete ? (
        <div className="mb-6 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Add a headline, summary, skills or years of experience so matching has
          candidate evidence to work with.
        </div>
      ) : null}

      {editing ? (
        <Card>
          <CardHeader
            title="Edit profile"
            description="Saved changes are used for matching and cover letters."
          />
          <CardBody>
            <CandidateForm
              initial={candidate}
              submitLabel="Save changes"
              onSubmit={(payload) => {
                void run(payload);
              }}
              submitting={pending}
              error={error}
            />
            <div className="mt-4 flex justify-end">
              <Button
                type="button"
                variant="ghost"
                onClick={() => setEditing(false)}
              >
                Cancel
              </Button>
            </div>
          </CardBody>
        </Card>
      ) : (
        <Card>
          <CardHeader
            title={candidate.name}
            description={candidate.headline ?? "No headline yet"}
          />
          <CardBody>
            <dl className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              <Detail label="Location" value={candidate.location ?? "—"} />
              <Detail
                label="Years of experience"
                value={
                  candidate.years_of_experience !== null
                    ? String(candidate.years_of_experience)
                    : "—"
                }
              />
              <Detail label="Seniority" value={candidate.seniority ?? "—"} />
              <Detail
                label="Profile created"
                value={formatDate(candidate.created_at)}
              />
              <Detail label="Last updated" value={formatDate(candidate.updated_at)} />
              <Detail label="Candidate id" value={candidate.id} />
            </dl>

            <div className="mt-6">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Skills
              </p>
              {candidate.skills && candidate.skills.length ? (
                <div className="mt-2 flex flex-wrap gap-2">
                  {candidate.skills.map((skill) => (
                    <Badge key={skill} tone="info">
                      {skill}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm text-slate-500">
                  No skills listed yet. Matching uses your skills, summary and resume.
                </p>
              )}
            </div>

            <div className="mt-6">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                Summary
              </p>
              <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">
                {candidate.summary ?? "No summary yet."}
              </p>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}
