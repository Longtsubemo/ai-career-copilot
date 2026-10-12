import { useState, type FormEvent } from "react";

import type { ApiError } from "../api/errors";
import type { Candidate, CandidateCreateRequest } from "../types/api";
import { ErrorAlert } from "./ui/ErrorAlert";
import { Button } from "./ui/Button";
import { Field, TextArea, TextInput } from "./ui/Field";

function emptyToNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function parseSkills(value: string): string[] | null {
  const skills = value
    .split(",")
    .map((skill) => skill.trim())
    .filter(Boolean);
  return skills.length ? skills : null;
}

export function CandidateForm({
  onSubmit,
  submitting,
  error,
  initial = null,
  submitLabel = "Create profile",
}: {
  onSubmit: (payload: CandidateCreateRequest) => void;
  submitting: boolean;
  error?: ApiError | null;
  initial?: Candidate | null;
  submitLabel?: string;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [headline, setHeadline] = useState(initial?.headline ?? "");
  const [location, setLocation] = useState(initial?.location ?? "");
  const [years, setYears] = useState(
    initial?.years_of_experience !== null && initial?.years_of_experience !== undefined
      ? String(initial.years_of_experience)
      : "",
  );
  const [seniority, setSeniority] = useState(initial?.seniority ?? "");
  const [skills, setSkills] = useState(initial?.skills?.join(", ") ?? "");
  const [summary, setSummary] = useState(initial?.summary ?? "");
  const [localError, setLocalError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setLocalError("Enter your name to create a profile.");
      return;
    }
    const parsedYears = years.trim() === "" ? null : Number(years);
    if (parsedYears !== null && (Number.isNaN(parsedYears) || parsedYears < 0)) {
      setLocalError("Years of experience must be a number of 0 or more.");
      return;
    }
    setLocalError(null);
    onSubmit({
      name: name.trim(),
      headline: emptyToNull(headline),
      location: emptyToNull(location),
      years_of_experience: parsedYears,
      seniority: emptyToNull(seniority),
      skills: parseSkills(skills),
      summary: emptyToNull(summary),
    });
  }

  const message = localError ?? error?.message ?? null;

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {message ? <ErrorAlert message={message} /> : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" required htmlFor="candidate-name">
          <TextInput
            id="candidate-name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Ada Lovelace"
            autoComplete="name"
          />
        </Field>
        <Field label="Headline" htmlFor="candidate-headline">
          <TextInput
            id="candidate-headline"
            value={headline}
            onChange={(event) => setHeadline(event.target.value)}
            placeholder="Backend engineer"
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Location" htmlFor="candidate-location">
          <TextInput
            id="candidate-location"
            value={location}
            onChange={(event) => setLocation(event.target.value)}
            placeholder="Bangalore"
          />
        </Field>
        <Field label="Years of experience" htmlFor="candidate-years">
          <TextInput
            id="candidate-years"
            type="number"
            min={0}
            step={0.5}
            value={years}
            onChange={(event) => setYears(event.target.value)}
            placeholder="5"
          />
        </Field>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Seniority" htmlFor="candidate-seniority">
          <TextInput
            id="candidate-seniority"
            value={seniority}
            onChange={(event) => setSeniority(event.target.value)}
            placeholder="mid / senior"
          />
        </Field>
        <Field
          label="Skills"
          htmlFor="candidate-skills"
          hint="Comma separated — these are matched against job requirements."
        >
          <TextInput
            id="candidate-skills"
            value={skills}
            onChange={(event) => setSkills(event.target.value)}
            placeholder="python, fastapi, postgresql"
          />
        </Field>
      </div>

      <Field label="Summary" htmlFor="candidate-summary">
        <TextArea
          id="candidate-summary"
          rows={4}
          value={summary}
          onChange={(event) => setSummary(event.target.value)}
          placeholder="What you build, what you are looking for."
        />
      </Field>

      <div className="flex justify-end">
        <Button type="submit" loading={submitting}>
          {submitLabel}
        </Button>
      </div>
    </form>
  );
}
