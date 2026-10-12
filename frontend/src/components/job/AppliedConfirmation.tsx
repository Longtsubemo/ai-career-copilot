import { useState } from "react";

import { Button } from "../ui/Button";

export function AppliedConfirmation({
  jobLabel,
  pending,
  onConfirm,
  onCancel,
}: {
  jobLabel: string;
  pending: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const [confirmed, setConfirmed] = useState(false);

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
      <p className="text-sm font-medium text-amber-900">
        Confirm you applied to {jobLabel}
      </p>
      <p className="mt-1 text-xs text-amber-800">
        This app never submits applications for you. Mark it as applied only after
        you have sent your application on the employer&apos;s site.
      </p>
      <label className="mt-3 flex items-start gap-2 text-sm text-amber-900">
        <input
          type="checkbox"
          className="mt-0.5 h-4 w-4 rounded border-amber-300 text-amber-600 focus:ring-amber-500"
          checked={confirmed}
          disabled={pending}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        I submitted this application on the employer&apos;s site.
      </label>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          loading={pending}
          disabled={!confirmed}
          onClick={onConfirm}
        >
          Mark as applied
        </Button>
        <Button size="sm" variant="secondary" disabled={pending} onClick={onCancel}>
          Not yet
        </Button>
      </div>
    </div>
  );
}
