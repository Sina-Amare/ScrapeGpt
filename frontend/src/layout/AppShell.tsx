import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  BrainCog,
  Cookie,
  DatabaseZap,
  HeartPulse,
  List,
  LogOut,
  Menu,
  Moon,
  Plus,
  Settings2,
  Sun,
  X
} from "lucide-react";
import { motion } from "motion/react";
import { ReactNode, useState } from "react";
import { NavLink } from "react-router-dom";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: Activity, end: true },
  { to: "/projects", label: "Projects", icon: List, end: true },
  { to: "/projects/new", label: "New Extraction", icon: BrainCog, end: false },
  { to: "/providers", label: "Providers", icon: Settings2, end: false },
  { to: "/sessions", label: "Sessions", icon: Cookie, end: false },
  { to: "/help", label: "Help", icon: BookOpen, end: false },
  { to: "/scrape/new", label: "Legacy Scrape", icon: Plus, end: false, legacy: true },
  { to: "/health", label: "Health", icon: HeartPulse, end: false }
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
            end={item.end}
            onClick={onNavigate}
            className={({ isActive }) =>
              `relative flex h-10 items-center gap-3 rounded-md px-3 text-sm font-semibold transition ${
                item.legacy && isActive
                  ? "border border-line bg-porcelain text-ink"
                  : isActive
                  ? "bg-teal/[0.1] text-ink"
                  : item.legacy
                  ? "text-muted/80 hover:bg-porcelain hover:text-ink"
                  : "text-muted hover:bg-porcelain hover:text-ink"
              }`
            }
          >
            {({ isActive }) => (
              <>
                {isActive && !item.legacy && (
                  <motion.div
                    layoutId="sidebar-active-indicator"
                    className="absolute left-0 top-1 bottom-1 w-0.5 rounded-full bg-teal"
                    transition={{ type: "spring", stiffness: 500, damping: 40 }}
                  />
                )}
                <Icon className="h-4 w-4" />
                <motion.span
                  className="min-w-0 flex-1 truncate"
                  whileHover={{ x: 2 }}
                  transition={{ type: "spring", stiffness: 400, damping: 30 }}
                >
                  {item.label}
                </motion.span>
                {item.legacy ? (
                  <span className="rounded border border-line px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-muted">
                    old
                  </span>
                ) : null}
              </>
            )}
          </NavLink>
        );
      })}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { displayEmail, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  const { dark, toggle } = useTheme();
  const health = useQuery({
    queryKey: ["health-ready"],
    queryFn: () => api.getHealth("/health/ready"),
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
              ScrapeGPT
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
                  ScrapeGPT
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
        <header className="sticky top-0 z-30 border-b border-line bg-surface">
          <div className="flex h-16 items-center justify-between gap-3 px-4 md:px-8">
            <div className="flex items-center gap-3">
              <button
                type="button"
                className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-porcelain hover:text-ink transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal md:hidden"
                onClick={() => setMobileOpen(true)}
              >
                <Menu className="h-5 w-5" />
              </button>
              <motion.div
                className={`h-2.5 w-2.5 rounded-full ${
                  health.isSuccess ? "bg-success" : "bg-warning"
                }`}
                animate={health.isSuccess
                  ? { scale: [1, 1.25, 1] }
                  : { opacity: [1, 0.45, 1] }
                }
                transition={{ duration: 2.5, repeat: Infinity, ease: "easeInOut" }}
                aria-label={health.isSuccess ? "Backend ready" : "Backend not ready"}
              />
              <span className="text-sm font-semibold text-muted">
                {health.isSuccess ? "Backend ready" : "Backend not ready"}
              </span>
            </div>
            <div className="flex min-w-0 items-center gap-2">
              <span className="hidden max-w-52 truncate text-sm font-semibold text-ink sm:block">
                {displayEmail ?? "Signed in"}
              </span>
              <button
                type="button"
                onClick={toggle}
                className="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted hover:bg-porcelain hover:text-ink transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-teal"
                aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
              >
                <motion.div
                  key={dark ? "moon" : "sun"}
                  initial={{ rotate: -30, scale: 0.7, opacity: 0 }}
                  animate={{ rotate: 0, scale: 1, opacity: 1 }}
                  transition={{ duration: 0.25, ease: "easeOut" }}
                >
                  {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                </motion.div>
              </button>
              <Button variant="secondary" onClick={() => setShowLogoutConfirm(true)}>
                <LogOut className="h-4 w-4" />
                Logout
              </Button>
            </div>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-6 md:px-8">{children}</main>
      </div>

      <ConfirmDialog
        open={showLogoutConfirm}
        title="Log out?"
        message="You'll be returned to the sign-in page. Any unsaved changes on this screen won't be kept."
        confirmLabel="Log out"
        variant="primary"
        onConfirm={() => {
          setShowLogoutConfirm(false);
          logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
}
