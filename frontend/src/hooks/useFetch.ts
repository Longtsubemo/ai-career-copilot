import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, toApiError } from "../api/errors";

export interface UseFetchResult<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
  reload: (silent?: boolean) => Promise<void>;
}

export function useFetch<T>(
  fetcher: () => Promise<T>,
  deps: unknown[] = [],
): UseFetchResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const aliveRef = useRef(true);

  const reload = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const result = await fetcherRef.current();
      if (aliveRef.current) {
        setData(result);
      }
    } catch (err) {
      if (aliveRef.current) {
        setError(toApiError(err));
      }
    } finally {
      if (aliveRef.current && !silent) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    void reload();
    return () => {
      aliveRef.current = false;
    };
  }, deps);

  return { data, loading, error, reload };
}
