import { useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { Spinner } from "./components/ui/Spinner";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { CandidateProvider, useCandidate } from "./hooks/useCandidate";
import { AppLayout } from "./layouts/AppLayout";
import { ApplicationsPage } from "./pages/ApplicationsPage";
import { DashboardPage } from "./pages/DashboardPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { LoginPage } from "./pages/LoginPage";
import { NotFoundPage } from "./pages/NotFoundPage";
import { OnboardingPage } from "./pages/OnboardingPage";
import { ProfilePage } from "./pages/ProfilePage";
import { RegisterPage } from "./pages/RegisterPage";
import { ResumePage } from "./pages/ResumePage";
import { authRedirect } from "./utils/authRouting";
import { isProfileIncomplete } from "./utils/profile";

function LoadingScreen() {
  return (
    <div className="flex min-h-screen items-center justify-center">
      <Spinner className="h-8 w-8 text-indigo-500" />
    </div>
  );
}

function ErrorScreen({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-4 px-4 text-center">
      <p className="text-sm text-slate-700">{message}</p>
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

function UnauthenticatedRoutes() {
  return (
    <Routes>
      <Route path="/register" element={<RegisterPage />} />
      <Route path="*" element={<LoginPage />} />
    </Routes>
  );
}

function AuthenticatedShell() {
  const { candidate, loading, error, refresh } = useCandidate();
  const [skippedOnboarding, setSkippedOnboarding] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  if (loading) {
    return <LoadingScreen />;
  }

  if (error) {
    return <ErrorScreen message={error.message} onRetry={() => void refresh()} />;
  }

  const incomplete = isProfileIncomplete(candidate);

  if (incomplete && !skippedOnboarding && location.pathname !== "/onboarding") {
    return <Navigate to="/onboarding" replace />;
  }

  if (location.pathname === "/onboarding") {
    return (
      <OnboardingPage
        candidate={candidate}
        onSkip={() => {
          setSkippedOnboarding(true);
          navigate("/", { replace: true });
        }}
      />
    );
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

function AuthenticatedApp() {
  return (
    <CandidateProvider>
      <AuthenticatedShell />
    </CandidateProvider>
  );
}

function Shell() {
  const { status, error, refresh } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <LoadingScreen />;
  }

  if (status === "unauthenticated" && error) {
    return <ErrorScreen message={error.message} onRetry={() => void refresh()} />;
  }

  const redirect = authRedirect(status, location.pathname);
  if (redirect) {
    return <Navigate to={redirect} replace />;
  }

  if (status === "authenticated") {
    return <AuthenticatedApp />;
  }

  return <UnauthenticatedRoutes />;
}

export default function App() {
  return (
    <AuthProvider>
      <Shell />
    </AuthProvider>
  );
}
