import { Route, Routes } from "react-router-dom";

import { Spinner } from "./components/ui/Spinner";
import { CandidateProvider, useCandidate } from "./hooks/useCandidate";
import { AppLayout } from "./layouts/AppLayout";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ProfilePage } from "./pages/ProfilePage";
import { ResumePage } from "./pages/ResumePage";

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner className="h-8 w-8 text-indigo-500" />
    </div>
  );
}

function ErrorScreen({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="max-w-xl text-sm text-slate-700">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
      >
        Retry
      </button>
    </div>
  );
}

function ApplicationRoutes() {
  const { loading, error, refresh } = useCandidate();

  if (loading) return <LoadingScreen />;
  if (error) {
    return <ErrorScreen message={error.message} onRetry={() => void refresh()} />;
  }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<DashboardPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="applications" element={<ApplicationsPage />} />
        <Route path="resume" element={<ResumePage />} />
        <Route path="profile" element={<ProfilePage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <CandidateProvider>
      <ApplicationRoutes />
    </CandidateProvider>
  );
}
