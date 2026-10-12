import type { ApiError } from "../../api/errors";
import type { JobAnalysis } from "../../types/api";
import { formatPercent } from "../../utils/format";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card, CardBody, CardHeader } from "../ui/Card";
import { ErrorAlert } from "../ui/ErrorAlert";
import { EmptyState, LoadingState } from "../ui/EmptyState";
import { BulletList, Chips, SectionTitle } from "./Lists";

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs text-slate-500">{label}</p>
      <p className="mt-0.5 text-sm font-medium text-slate-900">{value}</p>
    </div>
  );
}

export function AnalysisCard({
  analysis,
  pending,
  error,
  onAnalyze,
}: {
  analysis: JobAnalysis | null;
  pending: boolean;
  error: ApiError | null;
  onAnalyze: () => void;
}) {
  const experience =
    analysis && analysis.experience_years_min !== null
      ? analysis.experience_years_max !== null
        ? `${analysis.experience_years_min}–${analysis.experience_years_max} years`
        : `${analysis.experience_years_min}+ years`
      : "Not specified";

  return (
    <Card id="analysis">
      <CardHeader
        title="Job analysis"
        description="Requirements and expectations read from the posting."
        actions={
          analysis ? (
            <Badge tone="info">Confidence {formatPercent(analysis.confidence)}</Badge>
          ) : null
        }
      />
      <CardBody>
        {pending ? (
          <LoadingState message="Analyzing this job…" />
        ) : error ? (
          <ErrorAlert
            message={error.message}
            action={
              <Button size="sm" variant="secondary" onClick={onAnalyze}>
                Retry
              </Button>
            }
          />
        ) : analysis ? (
          <div className="space-y-6">
            <div>
              <SectionTitle>Summary</SectionTitle>
              <p className="mt-2 text-sm leading-6 text-slate-700">
                {analysis.summary}
              </p>
            </div>

            <div>
              <SectionTitle>Required skills</SectionTitle>
              <div className="mt-2">
                <Chips items={analysis.required_skills} tone="info" emptyText="None listed" />
              </div>
            </div>

            <div>
              <SectionTitle>Preferred skills</SectionTitle>
              <div className="mt-2">
                <Chips
                  items={analysis.preferred_skills}
                  tone="muted"
                  emptyText="None listed"
                />
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <Fact label="Experience" value={experience} />
              <Fact label="Seniority" value={analysis.seniority ?? "Not specified"} />
              <Fact label="Work style" value={analysis.remote_type ?? "Not specified"} />
              <Fact
                label="Education"
                value={
                  analysis.education_requirements.length
                    ? analysis.education_requirements.join(", ")
                    : "Not specified"
                }
              />
            </div>

            <div>
              <SectionTitle>Responsibilities</SectionTitle>
              <div className="mt-2">
                <BulletList items={analysis.responsibilities} />
              </div>
            </div>

            <div>
              <SectionTitle>Qualifications</SectionTitle>
              <div className="mt-2">
                <BulletList items={analysis.qualifications} />
              </div>
            </div>

            <div>
              <SectionTitle>Technologies</SectionTitle>
              <div className="mt-2">
                <Chips items={analysis.technologies} tone="neutral" />
              </div>
            </div>

            {analysis.domain_keywords.length ? (
              <div>
                <SectionTitle>Domain keywords</SectionTitle>
                <div className="mt-2">
                  <Chips items={analysis.domain_keywords} tone="muted" />
                </div>
              </div>
            ) : null}

            {analysis.certifications.length ? (
              <div>
                <SectionTitle>Certifications</SectionTitle>
                <div className="mt-2">
                  <BulletList items={analysis.certifications} />
                </div>
              </div>
            ) : null}

            {analysis.ambiguities.length ? (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3">
                <p className="text-xs font-semibold uppercase tracking-wide text-amber-800">
                  Ambiguities in this posting
                </p>
                <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-amber-900">
                  {analysis.ambiguities.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer select-none text-indigo-600 hover:text-indigo-500">
                Show raw analysis JSON
              </summary>
              <pre className="mt-2 overflow-auto rounded-lg bg-slate-900 p-3 text-slate-100">
                {JSON.stringify(analysis, null, 2)}
              </pre>
            </details>
          </div>
        ) : (
          <EmptyState
            compact
            title="Analyze this job to understand its requirements"
            description="The analysis extracts required skills, responsibilities, qualifications and experience — it is stored once and reused."
            action={
              <Button onClick={onAnalyze} loading={pending}>
                Analyze job
              </Button>
            }
          />
        )}
      </CardBody>
    </Card>
  );
}
