import { Link } from "react-router-dom";

import type { ApiError } from "../../api/errors";
import type { MatchEnvelope, MatchResult, MatchSignal } from "../../types/api";
import { formatPercent, formatScore } from "../../utils/format";
import { mentionsAnalysis, mentionsResume } from "../../utils/errorHints";
import {
  MATCH_SIGNAL_META,
  MATCH_VERDICT_META,
  scoreTone,
  type Tone,
} from "../../utils/labels";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Card, CardBody, CardHeader } from "../ui/Card";
import { ErrorAlert } from "../ui/ErrorAlert";
import { EmptyState, LoadingState } from "../ui/EmptyState";
import { BulletList, Chips, SectionTitle } from "./Lists";

const SCORE_COLORS: Record<Tone, string> = {
  success: "text-emerald-600",
  warning: "text-amber-600",
  danger: "text-rose-600",
  info: "text-blue-600",
  neutral: "text-slate-700",
  muted: "text-slate-500",
};

const BAR_COLORS: Record<Tone, string> = {
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  info: "bg-blue-500",
  neutral: "bg-slate-500",
  muted: "bg-slate-400",
};

function SignalBox({ label, signal }: { label: string; signal: MatchSignal }) {
  const meta = MATCH_SIGNAL_META[signal];
  return (
    <div className="rounded-lg bg-slate-50 px-3 py-2">
      <p className="text-xs capitalize text-slate-500">{label}</p>
      <p
        className={`mt-0.5 flex items-center gap-1.5 text-sm font-medium ${
          meta.tone === "success"
            ? "text-emerald-700"
            : meta.tone === "danger"
              ? "text-rose-700"
              : "text-slate-500"
        }`}
      >
        <span aria-hidden="true">{meta.symbol}</span>
        {meta.label}
      </p>
    </div>
  );
}

function ScoreBlock({ match }: { match: MatchResult }) {
  const tone = scoreTone(match.score);
  const verdict = MATCH_VERDICT_META[match.verdict];

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-baseline gap-2">
          <span className={`text-5xl font-bold tracking-tight ${SCORE_COLORS[tone]}`}>
            {formatScore(match.score)}
          </span>
          <span className="text-sm font-medium text-slate-400">/ 100</span>
        </div>
        <div className="flex flex-col items-start gap-1">
          <Badge tone={verdict.tone}>{verdict.label}</Badge>
          <span className="text-xs text-slate-500">
            Model confidence {formatPercent(match.confidence)}
          </span>
        </div>
      </div>

      <div className="mt-4 h-2 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className={`h-full rounded-full ${BAR_COLORS[tone]}`}
          style={{ width: `${Math.max(0, Math.min(100, match.score))}%` }}
        />
      </div>
    </div>
  );
}

export function MatchCard({
  envelope,
  pending,
  error,
  onMatch,
  onAnalyze,
}: {
  envelope: MatchEnvelope | null;
  pending: boolean;
  error: ApiError | null;
  onMatch: () => void;
  onAnalyze: () => void;
}) {
  const match = envelope?.match ?? null;

  return (
    <Card id="match">
      <CardHeader
        title="Resume match"
        description="How your resume fits this job, scored by the copilot."
      />
      <CardBody>
        {pending ? (
          <LoadingState message="Matching this job against your resume…" />
        ) : error ? (
          <ErrorAlert
            message={error.message}
            action={
              mentionsAnalysis(error) ? (
                <Button size="sm" variant="secondary" onClick={onAnalyze}>
                  Analyze this job first
                </Button>
              ) : mentionsResume(error) ? (
                <Link
                  to="/resume"
                  className="inline-flex items-center rounded-lg bg-white px-3 py-1.5 text-xs font-medium text-rose-700 ring-1 ring-inset ring-rose-200 hover:bg-rose-50"
                >
                  Upload your resume
                </Link>
              ) : (
                <Button size="sm" variant="secondary" onClick={onMatch}>
                  Retry
                </Button>
              )
            }
          />
        ) : match ? (
          <div className="space-y-6">
            <ScoreBlock match={match} />

            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <SignalBox label="Experience" signal={match.experience_match} />
              <SignalBox label="Education" signal={match.education_match} />
              <SignalBox label="Location" signal={match.location_match} />
              <SignalBox label="Seniority" signal={match.seniority_match} />
            </div>

            <div>
              <SectionTitle>Matched skills</SectionTitle>
              <div className="mt-2">
                <Chips items={match.matched_skills} tone="success" emptyText="None matched" />
              </div>
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <SectionTitle>Missing required skills</SectionTitle>
                <div className="mt-2">
                  <Chips
                    items={match.missing_required_skills}
                    tone="danger"
                    emptyText="Nothing required is missing"
                  />
                </div>
              </div>
              <div>
                <SectionTitle>Missing preferred skills</SectionTitle>
                <div className="mt-2">
                  <Chips
                    items={match.missing_preferred_skills}
                    tone="warning"
                    emptyText="Nothing preferred is missing"
                  />
                </div>
              </div>
            </div>

            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <SectionTitle>Strengths</SectionTitle>
                <div className="mt-2">
                  <BulletList items={match.strengths} emptyText="None noted" />
                </div>
              </div>
              <div>
                <SectionTitle>Gaps</SectionTitle>
                <div className="mt-2">
                  <BulletList items={match.gaps} emptyText="None noted" />
                </div>
              </div>
            </div>

            <div>
              <SectionTitle>Rationale</SectionTitle>
              <p className="mt-2 rounded-lg bg-slate-50 p-4 text-sm leading-6 text-slate-700">
                {match.rationale}
              </p>
            </div>
          </div>
        ) : (
          <EmptyState
            compact
            title="Match this job against your resume"
            description="The score compares your current master resume with this job's analysis. Your backend result is authoritative."
            action={
              <Button onClick={onMatch} loading={pending}>
                Match me
              </Button>
            }
          />
        )}
      </CardBody>
    </Card>
  );
}
