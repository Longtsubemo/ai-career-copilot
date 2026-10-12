import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";

import {
  getCurrentResumeOrNull,
  getMasterResumeHistory,
  uploadMasterResume,
} from "../api/resumes";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Card, CardBody, CardHeader } from "../components/ui/Card";
import { ErrorAlert, SuccessNote } from "../components/ui/ErrorAlert";
import { EmptyState, LoadingState } from "../components/ui/EmptyState";
import { Field, TextInput } from "../components/ui/Field";
import { Icon } from "../components/ui/Icon";
import { PageHeader } from "../components/ui/PageHeader";
import { useAsync } from "../hooks/useAsync";
import { useFetch } from "../hooks/useFetch";
import type { ResumeUploadResult } from "../types/api";
import { formatDateTime, formatFileSize } from "../utils/format";
import {
  isSupportedResumeFileName,
  MAX_RESUME_BYTES,
  RESUME_ACCEPT_ATTRIBUTE,
  SUPPORTED_RESUME_FORMATS_LABEL,
} from "../utils/resumeUpload";

export function ResumePage() {
  const resume = useFetch(getCurrentResumeOrNull, []);
  const history = useFetch(getMasterResumeHistory, []);

  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const { run: upload, pending, error, resetError } = useAsync(
    async (chosen: File) => uploadMasterResume(chosen),
    {
      onSuccess: (result: ResumeUploadResult) => {
        setFile(null);
        if (inputRef.current) {
          inputRef.current.value = "";
        }
        setSuccess(
          `Resume uploaded — new version saved ${formatDateTime(result.created_at)}.`,
        );
        void resume.reload(true);
        void history.reload(true);
      },
    },
  );

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const chosen = event.target.files?.[0] ?? null;
    setFile(chosen);
    setLocalError(null);
    setSuccess(null);
    resetError();
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setSuccess(null);
    if (!file) {
      setLocalError("Choose a resume file first.");
      return;
    }
    if (!isSupportedResumeFileName(file.name)) {
      setLocalError(
        `That file type is not supported. Upload ${SUPPORTED_RESUME_FORMATS_LABEL}.`,
      );
      return;
    }
    if (file.size > MAX_RESUME_BYTES) {
      setLocalError(
        `That file is ${formatFileSize(file.size)}. The maximum resume size is 2 MB.`,
      );
      return;
    }
    if (file.size === 0) {
      setLocalError("That file is empty. Choose a resume with actual text.");
      return;
    }
    setLocalError(null);
    void upload(file);
  }

  const message = localError ?? error?.message ?? null;
  const versions = history.data?.items ?? [];

  return (
    <div>
      <PageHeader
        title="Resume"
        description="Your master resume — the evidence matching and cover letters use."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {resume.loading ? (
            <Card>
              <LoadingState message="Loading your resume…" />
            </Card>
          ) : resume.error ? (
            <ErrorAlert
              message={resume.error.message}
              action={
                <Button size="sm" variant="secondary" onClick={() => void resume.reload()}>
                  Retry
                </Button>
              }
            />
          ) : resume.data ? (
            <Card>
              <CardHeader
                title="Current master resume"
                description={`Uploaded ${formatDateTime(resume.data.created_at)} · ${resume.data.content.length.toLocaleString()} characters`}
                actions={<Badge tone="success">Active</Badge>}
              />
              <CardBody>
                <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-4 text-sm text-slate-700">
                  {resume.data.content}
                </pre>
              </CardBody>
            </Card>
          ) : (
            <EmptyState
              title="No resume uploaded yet"
              description={`Upload your resume to start matching jobs. ${SUPPORTED_RESUME_FORMATS_LABEL}, up to 2 MB.`}
              action={
                <Button onClick={() => inputRef.current?.click()}>
                  <Icon name="upload" className="h-4 w-4" />
                  Choose a resume
                </Button>
              }
            />
          )}

          <Card>
            <CardHeader
              title="Version history"
              description="Every upload is kept — the previous version is never overwritten."
            />
            <CardBody>
              {history.loading ? (
                <LoadingState />
              ) : versions.length === 0 ? (
                <p className="text-sm text-slate-500">
                  No versions yet. Your first upload starts the history.
                </p>
              ) : (
                <ul className="divide-y divide-slate-100">
                  {versions.map((version, index) => (
                    <li key={version.id} className="py-3 first:pt-0 last:pb-0">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium text-slate-900">
                            {formatDateTime(version.created_at)}
                          </span>
                          {index === 0 ? <Badge tone="success">Current</Badge> : null}
                          <span className="text-xs text-slate-500">
                            {version.content.length.toLocaleString()} characters
                          </span>
                        </div>
                        <details className="text-xs text-slate-500">
                          <summary className="cursor-pointer select-none text-indigo-600 hover:text-indigo-500">
                            View version
                          </summary>
                          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 text-slate-700">
                            {version.content}
                          </pre>
                        </details>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </CardBody>
          </Card>
        </div>

        <div className="lg:col-span-1">
          <Card>
            <CardHeader
              title="Upload a new version"
              description="Appends a new version and keeps the old one."
            />
            <CardBody className="space-y-4">
              {message ? <ErrorAlert message={message} /> : null}
              {success ? <SuccessNote message={success} /> : null}

              <form onSubmit={handleSubmit} className="space-y-4">
                <Field
                  label="Resume file"
                  hint={`Plain text, Markdown, PDF or DOCX (.txt, .md, .pdf, .docx), up to 2 MB.`}
                >
                  <TextInput
                    ref={inputRef}
                    type="file"
                    accept={RESUME_ACCEPT_ATTRIBUTE}
                    onChange={handleFileChange}
                  />
                </Field>

                {file ? (
                  <p className="text-xs text-slate-500">
                    {file.name} · {formatFileSize(file.size)}
                  </p>
                ) : null}

                <Button type="submit" loading={pending} className="w-full">
                  Upload resume
                </Button>
              </form>

              <p className="text-xs text-slate-500">
                Your resume never leaves this backend — nothing is sent to the browser
                from external services.{" "}
                <Link to="/profile" className="text-indigo-600 hover:text-indigo-500">
                  View profile
                </Link>
              </p>
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  );
}
