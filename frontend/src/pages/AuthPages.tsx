import { useQuery } from "@tanstack/react-query";
import { Eye, EyeOff, Moon, Sun } from "lucide-react";
import { motion } from "motion/react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/theme";

// Fixed positions/timing for the floating "data node" dots in the auth bg.
const AUTH_NODES = [
  { left: "12%", top: "24%", delay: "0s", dur: "13s" },
  { left: "26%", top: "68%", delay: "2.4s", dur: "16s" },
  { left: "40%", top: "16%", delay: "5s", dur: "12s" },
  { left: "33%", top: "82%", delay: "1.2s", dur: "15s" },
  { left: "53%", top: "58%", delay: "3.6s", dur: "14s" },
  { left: "8%", top: "52%", delay: "6.2s", dur: "17s" },
  { left: "62%", top: "30%", delay: "4.4s", dur: "13s" },
  { left: "18%", top: "88%", delay: "0.8s", dur: "15s" }
];

// ---------------------------------------------------------------------------
// Inputs
// ---------------------------------------------------------------------------

function AuthInput({
  label,
  type = "text",
  value,
  onChange,
  autoComplete,
  required,
  placeholder,
}: {
  label: string;
  type?: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  required?: boolean;
  placeholder?: string;
}) {
  return (
    <label className="grid gap-1.5 text-sm font-medium text-ink">
      {label}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        required={required}
        placeholder={placeholder}
        className="auth-input"
      />
    </label>
  );
}

function AuthPasswordField({
  label,
  value,
  onChange,
  autoComplete,
  required,
  hint,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  autoComplete?: string;
  required?: boolean;
  hint?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <label className="grid gap-1.5 text-sm font-medium text-ink">
      {label}
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          required={required}
          minLength={8}
          className="auth-input pr-11"
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setVisible((v) => !v)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted transition hover:text-ink"
          aria-label={visible ? "Hide password" : "Show password"}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {hint ? <span className="text-xs font-normal text-muted">{hint}</span> : null}
    </label>
  );
}

// ---------------------------------------------------------------------------
// Logo mark
// ---------------------------------------------------------------------------

function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <div
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.28) }}
      className="grid flex-shrink-0 place-items-center bg-primary shadow-glow"
    >
      <svg
        width={size * 0.5}
        height={size * 0.5}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="text-onprimary"
      >
        <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18" />
      </svg>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Shared auth frame
// ---------------------------------------------------------------------------

function AuthFrame({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  const { dark, toggle } = useTheme();

  return (
    <div className="relative min-h-screen overflow-hidden bg-body text-ink">

      {/* ── Background: aurora + data grid + floating data nodes ── */}

      {/* Aurora — slow-drifting blurred color blobs (gradient-mesh feel) */}
      <div className="auth-aurora pointer-events-none" aria-hidden="true">
        <span className="auth-blob auth-blob-1" />
        <span className="auth-blob auth-blob-2" />
        <span className="auth-blob auth-blob-3" />
      </div>

      {/* Data grid — evokes a page being mapped into rows & columns */}
      <div className="auth-grid pointer-events-none" aria-hidden="true" />

      {/* Floating data nodes — points being gathered */}
      <div className="auth-nodes pointer-events-none" aria-hidden="true">
        {AUTH_NODES.map((node, i) => (
          <span
            key={i}
            className="auth-node"
            style={{
              left: node.left,
              top: node.top,
              animationDelay: node.delay,
              animationDuration: node.dur,
            }}
          />
        ))}
      </div>

      {/* ── Theme toggle — top right ── */}
      <div className="absolute right-5 top-5 z-20">
        <button
          onClick={toggle}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface text-muted transition hover:border-teal hover:text-ink"
          aria-label="Toggle theme"
        >
          <motion.div
            key={dark ? "moon" : "sun"}
            initial={{ rotate: -20, scale: 0.7, opacity: 0 }}
            animate={{ rotate: 0, scale: 1, opacity: 1 }}
            transition={{ duration: 0.22, ease: "easeOut" }}
          >
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </motion.div>
        </button>
      </div>

      {/* ── Content ── */}
      <div className="relative z-10 mx-auto grid min-h-screen max-w-5xl items-center gap-12 px-6 py-12 lg:grid-cols-[1fr_400px] lg:gap-16">

        {/* Left: brand — desktop only */}
        <motion.section
          className="hidden lg:flex flex-col gap-12"
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
        >
          <div className="flex items-center gap-2.5">
            <LogoMark size={34} />
            <span className="text-lg font-bold tracking-tight">ScrapeGPT</span>
          </div>

          <div>
            <h1 className="text-[2.6rem] font-black leading-[1.1] tracking-tight text-ink">
              Extract structured<br />data from any site.
            </h1>
            <p className="mt-4 max-w-[280px] text-[0.88rem] leading-relaxed text-muted">
              Connect your LLM, define extraction fields, and pull clean structured data from any page.
            </p>
          </div>

          <ul className="space-y-4 text-sm text-muted">
            {[
              "Bring your own API key — no credits",
              "Self-hosted, your data stays local",
              "Export to JSON, CSV, or XLSX",
            ].map((f) => (
              <li key={f} className="flex items-center gap-3">
                <span className="h-px w-5 flex-shrink-0 bg-teal/50" />
                {f}
              </li>
            ))}
          </ul>
        </motion.section>

        {/* Right: form card */}
        <motion.section
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          className="rounded-2xl border border-line bg-surface p-8 shadow-panel"
        >
          {/* Mobile-only wordmark */}
          <div className="mb-7 flex items-center gap-2.5 lg:hidden">
            <LogoMark size={28} />
            <span className="text-sm font-bold">ScrapeGPT</span>
          </div>

          <div className="mb-7">
            <h2 className="text-xl font-bold text-ink">{title}</h2>
            <p className="mt-1 text-sm text-muted">{subtitle}</p>
          </div>

          {children}
        </motion.section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const authConfig = useQuery({
    queryKey: ["auth-config"],
    queryFn: api.getAuthConfig,
    staleTime: 5 * 60 * 1000,
    retry: false
  });

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthFrame title="Welcome back" subtitle="Sign in to your extraction console.">
      <form className="grid gap-4" onSubmit={onSubmit}>
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <AuthInput
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
          placeholder="you@example.com"
        />

        <div className="grid gap-1.5">
          <AuthPasswordField
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
          {authConfig.data?.password_reset_enabled ? (
            <p className="text-right text-xs text-muted">
              <Link
                to="/forgot-password"
                className="font-medium text-teal transition-colors hover:text-teal-dark"
              >
                Forgot password?
              </Link>
            </p>
          ) : null}
        </div>

        <Button
          type="submit"
          className="mt-1 h-11 w-full rounded-xl text-sm font-semibold"
          disabled={submitting}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <p className="text-center text-sm text-muted">
          New here?{" "}
          <Link
            className="font-semibold text-teal transition-colors hover:text-teal-dark"
            to="/register"
          >
            Create an account
          </Link>
        </p>
      </form>
    </AuthFrame>
  );
}

// ---------------------------------------------------------------------------
// Register
// ---------------------------------------------------------------------------

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password);
      navigate("/dashboard", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthFrame title="Create account" subtitle="Start extracting in minutes.">
      <form className="grid gap-4" onSubmit={onSubmit}>
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <AuthInput
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
          placeholder="you@example.com"
        />

        <AuthPasswordField
          label="Password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          required
          hint="Minimum 8 characters."
        />

        <AuthPasswordField
          label="Confirm password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
          required
        />

        <Button
          type="submit"
          className="mt-1 h-11 w-full rounded-xl text-sm font-semibold"
          disabled={submitting}
        >
          {submitting ? "Creating…" : "Create account"}
        </Button>

        <p className="text-center text-sm text-muted">
          Already have access?{" "}
          <Link
            className="font-semibold text-teal transition-colors hover:text-teal-dark"
            to="/login"
          >
            Sign in
          </Link>
        </p>
      </form>
    </AuthFrame>
  );
}

// ---------------------------------------------------------------------------
// Forgot password (request code -> confirm code + new password -> done)
// ---------------------------------------------------------------------------

function BackToLogin() {
  return (
    <p className="text-center text-sm text-muted">
      Remembered it?{" "}
      <Link
        className="font-semibold text-teal transition-colors hover:text-teal-dark"
        to="/login"
      >
        Back to sign in
      </Link>
    </p>
  );
}

export function ForgotPasswordPage() {
  const [step, setStep] = useState<"request" | "confirm" | "done">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onRequest(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const response = await api.requestPasswordReset(email);
      setNotice(response.message);
      setStep("confirm");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not send a reset code.");
    } finally {
      setSubmitting(false);
    }
  }

  async function onConfirm(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await api.confirmPasswordReset({ email, code, new_password: password });
      setStep("done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset your password.");
    } finally {
      setSubmitting(false);
    }
  }

  const subtitle =
    step === "request"
      ? "Enter your account email and we'll send a reset code."
      : step === "confirm"
        ? "Enter the code we emailed and choose a new password."
        : "Your password has been updated.";

  return (
    <AuthFrame title="Reset password" subtitle={subtitle}>
      {step === "request" ? (
        <form className="grid gap-4" onSubmit={onRequest}>
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <AuthInput
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            required
            placeholder="you@example.com"
          />
          <Button
            type="submit"
            className="mt-1 h-11 w-full rounded-xl text-sm font-semibold"
            disabled={submitting}
          >
            {submitting ? "Sending…" : "Send reset code"}
          </Button>
          <BackToLogin />
        </form>
      ) : step === "confirm" ? (
        <form className="grid gap-4" onSubmit={onConfirm}>
          {notice ? <Alert tone="info">{notice}</Alert> : null}
          {error ? <Alert tone="danger">{error}</Alert> : null}
          <AuthInput
            label="Reset code"
            value={code}
            onChange={setCode}
            autoComplete="one-time-code"
            required
            placeholder="6-digit code"
          />
          <AuthPasswordField
            label="New password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            required
            hint="Minimum 8 characters."
          />
          <Button
            type="submit"
            className="mt-1 h-11 w-full rounded-xl text-sm font-semibold"
            disabled={submitting}
          >
            {submitting ? "Resetting…" : "Reset password"}
          </Button>
          <button
            type="button"
            onClick={() => {
              setStep("request");
              setError(null);
              setNotice(null);
            }}
            className="text-center text-xs text-muted transition hover:text-ink"
          >
            Didn't get a code? Start over
          </button>
          <BackToLogin />
        </form>
      ) : (
        <div className="grid gap-4">
          <Alert tone="success">
            Your password has been reset. You can now sign in with your new password.
          </Alert>
          <Link
            to="/login"
            className="inline-flex h-11 w-full items-center justify-center rounded-xl bg-primary text-sm font-semibold text-onprimary transition hover:bg-[var(--c-primary-hover)]"
          >
            Back to sign in
          </Link>
        </div>
      )}
    </AuthFrame>
  );
}
