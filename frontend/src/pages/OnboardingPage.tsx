import { useNavigate } from "react-router-dom";

import { CandidateForm } from "../components/CandidateForm";
import { Button } from "../components/ui/Button";
import { Card, CardBody } from "../components/ui/Card";
import { Icon } from "../components/ui/Icon";
import { useAsync } from "../hooks/useAsync";
import { useCandidate } from "../hooks/useCandidate";
import type { Candidate, CandidateCreateRequest } from "../types/api";

export function OnboardingPage({
  candidate,
  onSkip,
}: {
  candidate: Candidate | null;
  onSkip: () => void;
}) {
  const { createProfile, updateProfile } = useCandidate();
  const navigate = useNavigate();
  const editing = candidate !== null;
  const { run, pending, error } = useAsync(
    (payload: CandidateCreateRequest) =>
      candidate ? updateProfile(payload) : createProfile(payload),
    { onSuccess: () => navigate("/", { replace: true }) },
  );

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-2xl">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold text-white">
            C
          </span>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">
              Career Copilot
            </h1>
            <p className="text-sm text-slate-500">
              Discover jobs, analyze them, match your resume, track applications.
            </p>
          </div>
        </div>

        <Card>
          <CardBody>
            <div className="mb-5">
              <h2 className="text-base font-semibold text-slate-900">
                {editing ? "Complete your profile" : "Create your profile"}
              </h2>
              <p className="mt-1 text-sm text-slate-500">
                Matching and cover letters read this profile as candidate evidence.
                Add at least a headline, summary, skills or years of experience for
                better results.
              </p>
            </div>

            <CandidateForm
              initial={candidate}
              submitLabel={editing ? "Save profile" : "Create profile"}
              onSubmit={(payload) => {
                void run(payload);
              }}
              submitting={pending}
              error={error}
            />

            <div className="mt-4 flex justify-end">
              <Button type="button" variant="ghost" onClick={onSkip}>
                Skip for now
              </Button>
            </div>
          </CardBody>
        </Card>

        <p className="mt-4 flex items-center gap-2 text-xs text-slate-500">
          <Icon name="sparkle" className="h-4 w-4 text-indigo-400" />
          Your resume and profile stay in your own backend database.
        </p>
      </div>
    </div>
  );
}
