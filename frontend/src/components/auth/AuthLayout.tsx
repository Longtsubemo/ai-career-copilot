import type { ReactNode } from "react";

import { Card, CardBody } from "../ui/Card";
import { Icon } from "../ui/Icon";

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-lg font-bold text-white">
            C
          </span>
          <div>
            <h1 className="text-lg font-semibold text-slate-900">Career Copilot</h1>
            <p className="text-sm text-slate-500">
              Discover jobs, analyze them, match your resume, track applications.
            </p>
          </div>
        </div>

        <Card>
          <CardBody>
            <div className="mb-5">
              <h2 className="text-base font-semibold text-slate-900">{title}</h2>
              {subtitle ? (
                <p className="mt-1 text-sm text-slate-500">{subtitle}</p>
              ) : null}
            </div>
            {children}
          </CardBody>
        </Card>

        {footer ? (
          <div className="mt-4 text-center text-sm text-slate-500">{footer}</div>
        ) : null}

        <p className="mt-4 flex items-center justify-center gap-2 text-xs text-slate-400">
          <Icon name="sparkle" className="h-4 w-4 text-indigo-400" />
          Your data stays in your own backend database.
        </p>
      </div>
    </div>
  );
}
