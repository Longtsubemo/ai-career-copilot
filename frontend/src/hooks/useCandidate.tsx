import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  createCandidate,
  getCurrentCandidate,
  updateCurrentCandidate,
} from "../api/candidates";
import { ApiError, toApiError } from "../api/errors";
import type {
  Candidate,
  CandidateCreateRequest,
  CandidateUpdateRequest,
} from "../types/api";

interface CandidateContextValue {
  candidate: Candidate | null;
  loading: boolean;
  error: ApiError | null;
  refresh: () => Promise<void>;
  createProfile: (payload: CandidateCreateRequest) => Promise<Candidate>;
  updateProfile: (payload: CandidateUpdateRequest) => Promise<Candidate>;
}

const CandidateContext = createContext<CandidateContextValue | null>(null);

export function CandidateProvider({ children }: { children: ReactNode }) {
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (showLoading: boolean) => {
    if (showLoading) {
      setLoading(true);
    }
    setError(null);
    try {
      const result = await getCurrentCandidate();
      setCandidate(result);
    } catch (err) {
      const apiError = toApiError(err);
      if (apiError.status === 404) {
        setCandidate(null);
      } else {
        setError(apiError);
      }
    } finally {
      if (showLoading) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load(true);
  }, [load]);

  const refresh = useCallback(async () => {
    await load(false);
  }, [load]);

  const createProfile = useCallback(async (payload: CandidateCreateRequest) => {
    const created = await createCandidate(payload);
    setCandidate(created);
    setError(null);
    return created;
  }, []);

  const updateProfile = useCallback(async (payload: CandidateUpdateRequest) => {
    const updated = await updateCurrentCandidate(payload);
    setCandidate(updated);
    setError(null);
    return updated;
  }, []);

  const value = useMemo(
    () => ({ candidate, loading, error, refresh, createProfile, updateProfile }),
    [candidate, loading, error, refresh, createProfile, updateProfile],
  );

  return (
    <CandidateContext.Provider value={value}>{children}</CandidateContext.Provider>
  );
}

export function useCandidate(): CandidateContextValue {
  const value = useContext(CandidateContext);
  if (!value) {
    throw new Error("useCandidate must be used inside a CandidateProvider");
  }
  return value;
}
