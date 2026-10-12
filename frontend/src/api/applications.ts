import type {
  Application,
  ApplicationCreateRequest,
  ApplicationListPage,
  ApplicationUpdateRequest,
} from "../types/api";

import { api } from "./client";

export interface ApplicationQuery {
  status?: string;
  limit?: number;
  offset?: number;
}

export async function listApplications(
  params: ApplicationQuery = {},
): Promise<ApplicationListPage> {
  const { data } = await api.get<ApplicationListPage>("/api/applications", {
    params,
  });
  return data;
}

export async function getApplication(applicationId: string): Promise<Application> {
  const { data } = await api.get<Application>(`/api/applications/${applicationId}`);
  return data;
}

export interface CreatedApplication {
  application: Application;
  created: boolean;
}

export async function createApplication(
  payload: ApplicationCreateRequest,
): Promise<CreatedApplication> {
  const response = await api.post<Application>("/api/applications", payload);
  return { application: response.data, created: response.status === 201 };
}

export async function updateApplication(
  applicationId: string,
  payload: ApplicationUpdateRequest,
): Promise<Application> {
  const { data } = await api.patch<Application>(
    `/api/applications/${applicationId}`,
    payload,
  );
  return data;
}
