import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes, useParams } from "react-router-dom";
import { Toaster } from "sonner";
import { AppShell } from "./layout/AppShell";
import { ProtectedRoute, PublicRoute } from "./layout/RouteGuards";
import { AuthProvider, useAuth } from "./lib/auth";
import { isCurrentSessionMutation } from "./lib/session";
import { ThemeProvider } from "./lib/theme";
import { ForgotPasswordPage, LoginPage, RegisterPage } from "./pages/AuthPages";
import { DashboardPage } from "./pages/DashboardPage";
import { HealthPage } from "./pages/HealthPage";
import { HelpPage } from "./pages/HelpPage";
import { NewProjectPage } from "./pages/NewProjectPage";
import { NewScrapePage } from "./pages/NewScrapePage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProvidersPage } from "./pages/ProvidersPage";
import { SessionsPage } from "./pages/SessionsPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      refetchOnWindowFocus: false
    }
  },
  // Mutation completion handlers live at the client level, so they fire even
  // when the page that started the mutation has unmounted (e.g. you navigated
  // away while a provider test was running). A mutation opts in by setting
  // `meta.notify` / `meta.notifyError` — typically a toast + cache invalidation.
  // We drop notifications for a mutation that was submitted before the last
  // auth-session boundary, so a test that resolves after logout (or under a
  // different user) can't surface the previous session's result.
  mutationCache: new MutationCache({
    onSuccess: (data, variables, _context, mutation) => {
      if (!isCurrentSessionMutation(mutation.state.submittedAt)) return;
      (mutation.options.meta?.notify as
        | ((d: unknown, v: unknown) => void)
        | undefined)?.(data, variables);
    },
    onError: (error, variables, _context, mutation) => {
      if (!isCurrentSessionMutation(mutation.state.submittedAt)) return;
      (mutation.options.meta?.notifyError as
        | ((e: unknown, v: unknown) => void)
        | undefined)?.(error, variables);
    }
  })
});

function ProtectedShell() {
  return (
    <AppShell>
      <Routes>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/new" element={<NewProjectPage />} />
        <Route path="projects/:id" element={<ProjectDetailPage />} />
        <Route path="new" element={<Navigate to="/projects/new" replace />} />
        <Route path="jobs" element={<Navigate to="/projects" replace />} />
        <Route path="jobs/new" element={<Navigate to="/projects/new" replace />} />
        <Route path="jobs/:id" element={<LegacyJobRedirect />} />
        <Route path="providers" element={<ProvidersPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        <Route path="scrape/new" element={<NewScrapePage />} />
        <Route path="health" element={<HealthPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="*" element={<FallbackRedirect />} />
      </Routes>
    </AppShell>
  );
}

function LegacyJobRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={id ? `/projects/${id}` : "/projects"} replace />;
}

function FallbackRedirect() {
  const { authenticated } = useAuth();
  return <Navigate to={authenticated ? "/dashboard" : "/login"} replace />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AuthProvider>
          <Toaster position="bottom-right" richColors />
          <Routes>
            <Route element={<PublicRoute />}>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />
              <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            </Route>
            <Route element={<ProtectedRoute />}>
              <Route path="/*" element={<ProtectedShell />} />
            </Route>
          </Routes>
        </AuthProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
