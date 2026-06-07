import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BrainCog,
  DatabaseZap,
  HeartPulse,
  List,
  LogOut,
  Menu,
  Plus,
  Settings2,
  X
} from "lucide-react";
import { ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: Activity },
  { to: "/jobs", label: "Jobs", icon: List },
  { to: "/jobs/new", label: "New Analysis", icon: BrainCog },
  { to: "/providers", label: "Providers", icon: Settings2 },
  { to: "/scrape/new", label: "Legacy Scrape", icon: Plus },
  { to: "/health", label: "Health", icon: HeartPulse }
];

const isJsdom = navigator.userAgent.toLowerCase().includes("jsdom");

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="grid gap-1">
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex h-10 items-center gap-3 rounded-md px-3 text-sm font-semibold transition ${
                isActive
                  ? "bg-teal text-white"
                  : "text-muted hover:bg-porcelain hover:text-ink"
              }`
            }
          >
            <Icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { displayEmail, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const health = useQuery({
    queryKey: ["health-live"],
    queryFn: () => api.getHealth("/health/live"),
    refetchInterval: isJsdom ? false : 15000,
    retry: 1
  });

  return (
    <div className="min-h-screen bg-porcelain text-ink">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-line bg-surface px-4 py-5 md:block">
        <div className="mb-8 flex items-center gap-3 px-2">
          <div className="grid h-10 w-10 place-items-center rounded-md bg-teal text-white">
            <DatabaseZap className="h-5 w-5" />
          </div>
          <div>
            <div className="text-sm font-bold uppercase tracking-wide text-ink">
              ScrapGPT
            </div>
            <div className="text-xs font-medium text-muted">BYOK Console</div>
          </div>
        </div>
        <SidebarNav />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 bg-ink/35 md:hidden">
          <aside className="h-full w-72 border-r border-line bg-surface p-4">
            <div className="mb-6 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="grid h-9 w-9 place-items-center rounded-md bg-teal text-white">
                  <DatabaseZap className="h-5 w-5" />
                </div>
                <span className="text-sm font-bold uppercase tracking-wide">
                  ScrapGPT
                </span>
              </div>
              <button
                type="button"
                className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-porcelain hover:text-ink transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal"
                onClick={() => setMobileOpen(false)}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <SidebarNav onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      ) : null}

      <div className="md:pl-64">
        <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
          <div className="flex h-16 items-center justify-between gap-3 px-4 md:px-8">
            <div className="flex items-center gap-3">
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-porcelain hover:text-ink transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal md:hidden"
                onClick={() => setMobileOpen(true)}
              >
                <Menu className="h-5 w-5" />
              </button>
              <div
                className={`h-2.5 w-2.5 rounded-full ${
                  health.isSuccess ? "bg-success" : "bg-warning"
                }`}
                aria-label={health.isSuccess ? "Backend online" : "Backend status unknown"}
              />
              <span className="text-sm font-semibold text-muted">
                {health.isSuccess ? "Backend online" : "Checking backend"}
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-3">
              <span className="hidden max-w-52 truncate text-sm font-semibold text-ink sm:block">
                {displayEmail ?? "Signed in"}
              </span>
              <Button variant="secondary" onClick={logout}>
                <LogOut className="h-4 w-4" />
                Logout
              </Button>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-8">{children}</main>
      </div>
    </div>
  );
}
