export interface ProfileEvidence {
  headline: string | null;
  seniority: string | null;
  years_of_experience: number | null;
  skills: string[] | null;
  summary: string | null;
}

export function hasProfileEvidence(candidate: ProfileEvidence): boolean {
  const hasSkills = Boolean(
    candidate.skills?.some((skill) => skill.trim().length > 0),
  );
  return Boolean(
    candidate.summary?.trim() ||
      candidate.headline?.trim() ||
      candidate.seniority?.trim() ||
      hasSkills ||
      candidate.years_of_experience !== null,
  );
}

export function isProfileIncomplete(candidate: ProfileEvidence | null): boolean {
  return candidate === null || !hasProfileEvidence(candidate);
}
