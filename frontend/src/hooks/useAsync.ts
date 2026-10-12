import { useCallback, useRef, useState } from "react";

import { ApiError, toApiError } from "../api/errors";

export interface UseAsyncOptions<TResult> {
  onSuccess?: (result: TResult) => void;
}

export interface UseAsyncResult<TArgs extends unknown[], TResult> {
  run: (...args: TArgs) => Promise<TResult | null>;
  pending: boolean;
  error: ApiError | null;
  data: TResult | null;
  resetError: () => void;
}

export function useAsync<TArgs extends unknown[], TResult>(
  callback: (...args: TArgs) => Promise<TResult>,
  options: UseAsyncOptions<TResult> = {},
): UseAsyncResult<TArgs, TResult> {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [data, setData] = useState<TResult | null>(null);

  const busyRef = useRef(false);
  const callbackRef = useRef(callback);
  callbackRef.current = callback;
  const successRef = useRef(options.onSuccess);
  successRef.current = options.onSuccess;

  const run = useCallback(async (...args: TArgs): Promise<TResult | null> => {
    if (busyRef.current) {
      return null;
    }
    busyRef.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await callbackRef.current(...args);
      setData(result);
      successRef.current?.(result);
      return result;
    } catch (err) {
      setError(toApiError(err));
      return null;
    } finally {
      busyRef.current = false;
      setPending(false);
    }
  }, []);

  const resetError = useCallback(() => setError(null), []);

  return { run, pending, error, data, resetError };
}
