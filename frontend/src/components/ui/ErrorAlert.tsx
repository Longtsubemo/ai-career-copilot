import type { ReactNode } from "react";

export function ErrorAlert({
  message,
  action,
  onDismiss,
}: {
  message: string;
  action?: ReactNode;
  onDismiss?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start gap-3 rounded-lg border border-rose-200 bg-rose-50 px-4 py-3">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        className="mt-0.5 h-5 w-5 shrink-0 text-rose-500"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
        <path
          d="M12 8v5M12 16.5v.5"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        />
      </svg>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-rose-800">{message}</p>
        {action ? <div className="mt-2">{action}</div> : null}
      </div>
      {onDismiss ? (
        <button
          type="button"
          onClick={onDismiss}
          className="text-rose-400 transition-colors hover:text-rose-600"
          aria-label="Dismiss error"
        >
          <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4" aria-hidden="true">
            <path
              d="M6 6l12 12M18 6L6 18"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      ) : null}
    </div>
  );
}

export function SuccessNote({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3">
      <svg
        viewBox="0 0 24 24"
        fill="none"
        className="mt-0.5 h-5 w-5 shrink-0 text-emerald-500"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.8" />
        <path
          d="M8 12.5l2.5 2.5L16 9.5"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      <p className="text-sm text-emerald-800">{message}</p>
    </div>
  );
}
