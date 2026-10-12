export const MAX_RESUME_BYTES = 2 * 1024 * 1024;

export const TEXT_CONTENT_TYPE = "text/plain";
export const MARKDOWN_CONTENT_TYPE = "text/markdown";
export const PDF_CONTENT_TYPE = "application/pdf";
export const DOCX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

const MARKDOWN_EXTENSIONS = new Set([".md", ".markdown"]);
const PDF_EXTENSIONS = new Set([".pdf"]);
const DOCX_EXTENSIONS = new Set([".docx"]);

export const SUPPORTED_RESUME_EXTENSIONS = [
  ".txt",
  ".md",
  ".markdown",
  ".pdf",
  ".docx",
] as const;

const SUPPORTED_EXTENSION_SET = new Set<string>(SUPPORTED_RESUME_EXTENSIONS);

export const SUPPORTED_RESUME_FORMATS_LABEL = "TXT, Markdown, PDF or DOCX";

export const RESUME_ACCEPT_ATTRIBUTE = [
  ...SUPPORTED_RESUME_EXTENSIONS,
  TEXT_CONTENT_TYPE,
  MARKDOWN_CONTENT_TYPE,
  PDF_CONTENT_TYPE,
  DOCX_CONTENT_TYPE,
].join(",");

export function fileExtension(name: string): string {
  const lower = name.toLowerCase();
  const dot = lower.lastIndexOf(".");
  return dot >= 0 ? lower.slice(dot) : "";
}

export function declaredResumeContentType(name: string): string {
  const extension = fileExtension(name);
  if (MARKDOWN_EXTENSIONS.has(extension)) {
    return MARKDOWN_CONTENT_TYPE;
  }
  if (PDF_EXTENSIONS.has(extension)) {
    return PDF_CONTENT_TYPE;
  }
  if (DOCX_EXTENSIONS.has(extension)) {
    return DOCX_CONTENT_TYPE;
  }
  return TEXT_CONTENT_TYPE;
}

export function isSupportedResumeFileName(name: string): boolean {
  return SUPPORTED_EXTENSION_SET.has(fileExtension(name));
}
