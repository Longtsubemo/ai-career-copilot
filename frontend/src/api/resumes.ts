import type { Resume, ResumeHistory, ResumeUploadResult } from "../types/api";

import { declaredResumeContentType } from "../utils/resumeUpload";
import { api } from "./client";
import { toApiError } from "./errors";

export async function getMasterResume(): Promise<Resume> {
  const { data } = await api.get<Resume>("/api/resumes/master");
  return data;
}

export async function getCurrentResumeOrNull(): Promise<Resume | null> {
  try {
    return await getMasterResume();
  } catch (error) {
    if (toApiError(error).status === 404) {
      return null;
    }
    throw error;
  }
}

export async function getMasterResumeHistory(): Promise<ResumeHistory> {
  const { data } = await api.get<ResumeHistory>("/api/resumes/master/history");
  return data;
}

export async function uploadMasterResume(file: File): Promise<ResumeUploadResult> {
  const payload = new File([file], file.name, {
    type: declaredResumeContentType(file.name),
  });
  const form = new FormData();
  form.append("file", payload, payload.name);
  const { data } = await api.post<ResumeUploadResult>("/api/resumes/master", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}
