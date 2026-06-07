import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import { ProtectedRoute, PublicRoute } from "./layout/RouteGuards";
import { AuthProvider, useAuth } from "./lib/auth";
import { LoginPage, RegisterPage } from "./pages/AuthPages";
import { DashboardPage } from "./pages/DashboardPage";
import { HealthPage } from "./pages/HealthPage";
import { JobDetailPage } from "./pages/JobDetailPage";
import { JobsPage } from "./pages/JobsPage";
import { NewJobPage } from "./pages/NewJobPage";
import { NewScrapePage } from "./pages/NewScrapePage";
import { ProvidersPage } from "./pages/ProvidersPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      refetchOnWindowFocus: false
    }
  }
});

function ProtectedShell() {
  return (
    <AppShell>
      <Routes>
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/new" element={<NewJobPage />} />
        <Route path="jobs/:id" element={<JobDetailPage />} />
        <Route path="providers" element={<ProvidersPage />} />
        <Route path="scrape/new" element={<NewScrapePage />} />
        <Route path="health" element={<HealthPage />} />
        <Route path="*" element={<FallbackRedirect />} />
      </Routes>
    </AppShell>
  );
}

function FallbackRedirect() {
  const { authenticated } = useAuth();
  return <Navigate to={authenticated ? "/dashboard" : "/login"} replace />;
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <Routes>
          <Route element={<PublicRoute />}>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
          </Route>
          <Route element={<ProtectedRoute />}>
            <Route path="/*" element={<ProtectedShell />} />
          </Route>
        </Routes>
      </AuthProvider>
    </QueryClientProvider>
  );
}
