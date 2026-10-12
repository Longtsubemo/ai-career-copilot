import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  declaredResumeContentType,
  DOCX_CONTENT_TYPE,
  fileExtension,
  isSupportedResumeFileName,
  MARKDOWN_CONTENT_TYPE,
  PDF_CONTENT_TYPE,
  RESUME_ACCEPT_ATTRIBUTE,
  SUPPORTED_RESUME_EXTENSIONS,
  SUPPORTED_RESUME_FORMATS_LABEL,
  TEXT_CONTENT_TYPE,
} from "../src/utils/resumeUpload.ts";

describe("declaredResumeContentType", () => {
  it("maps text files to text/plain", () => {
    assert.equal(declaredResumeContentType("resume.txt"), TEXT_CONTENT_TYPE);
    assert.equal(declaredResumeContentType("RESUME.TXT"), TEXT_CONTENT_TYPE);
  });

  it("maps markdown files to text/markdown", () => {
    assert.equal(declaredResumeContentType("resume.md"), MARKDOWN_CONTENT_TYPE);
    assert.equal(
      declaredResumeContentType("resume.markdown"),
      MARKDOWN_CONTENT_TYPE,
    );
  });

  it("maps pdf files to application/pdf", () => {
    assert.equal(declaredResumeContentType("resume.pdf"), PDF_CONTENT_TYPE);
    assert.equal(declaredResumeContentType("Resume.PDF"), PDF_CONTENT_TYPE);
  });

  it("maps docx files to the OOXML word type", () => {
    assert.equal(declaredResumeContentType("resume.docx"), DOCX_CONTENT_TYPE);
  });

  it("defaults to text/plain for unknown extensions", () => {
    assert.equal(declaredResumeContentType("resume.rtf"), TEXT_CONTENT_TYPE);
    assert.equal(declaredResumeContentType("resume"), TEXT_CONTENT_TYPE);
  });
});

describe("isSupportedResumeFileName", () => {
  it("accepts every advertised extension", () => {
    for (const extension of SUPPORTED_RESUME_EXTENSIONS) {
      assert.equal(isSupportedResumeFileName(`resume${extension}`), true);
    }
  });

  it("rejects unsupported and extensionless names", () => {
    assert.equal(isSupportedResumeFileName("resume.rtf"), false);
    assert.equal(isSupportedResumeFileName("resume.exe"), false);
    assert.equal(isSupportedResumeFileName("resume"), false);
  });
});

describe("upload affordances", () => {
  it("lists TXT, Markdown, PDF and DOCX in the accept attribute", () => {
    assert.match(RESUME_ACCEPT_ATTRIBUTE, /\.txt/);
    assert.match(RESUME_ACCEPT_ATTRIBUTE, /\.md/);
    assert.match(RESUME_ACCEPT_ATTRIBUTE, /\.pdf/);
    assert.match(RESUME_ACCEPT_ATTRIBUTE, /\.docx/);
    assert.match(RESUME_ACCEPT_ATTRIBUTE, new RegExp(TEXT_CONTENT_TYPE));
    assert.match(RESUME_ACCEPT_ATTRIBUTE, new RegExp(PDF_CONTENT_TYPE));
  });

  it("names all supported formats for the help text", () => {
    assert.match(SUPPORTED_RESUME_FORMATS_LABEL, /TXT/);
    assert.match(SUPPORTED_RESUME_FORMATS_LABEL, /Markdown/);
    assert.match(SUPPORTED_RESUME_FORMATS_LABEL, /PDF/);
    assert.match(SUPPORTED_RESUME_FORMATS_LABEL, /DOCX/);
  });
});

describe("fileExtension", () => {
  it("returns the lowercased extension including the dot", () => {
    assert.equal(fileExtension("Resume.PDF"), ".pdf");
    assert.equal(fileExtension("my.resume.docx"), ".docx");
  });

  it("returns an empty string when there is no extension", () => {
    assert.equal(fileExtension("resume"), "");
  });
});
