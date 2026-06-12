import { Eye, EyeOff, Moon, Sun } from "lucide-react";
import { motion } from "motion/react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";
import { useTheme } from "../lib/useTheme";

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
    <label className="grid gap-1.5 text-sm font-medium text-white/50">
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
    <label className="grid gap-1.5 text-sm font-medium text-white/50">
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
          className="absolute right-3 top-1/2 -translate-y-1/2 text-white/25 transition hover:text-white/60"
          aria-label={visible ? "Hide password" : "Show password"}
        >
          {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
        </button>
      </div>
      {hint ? <span className="text-xs font-normal text-white/28">{hint}</span> : null}
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
      className="grid flex-shrink-0 place-items-center bg-teal"
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
        className="text-white"
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
    <div className="relative min-h-screen overflow-hidden text-white" style={{ background: "#0E0E12" }}>

      {/* ── Background animations ── */}

      {/* Glow 1 — upper-left */}
      <div
        className="auth-glow pointer-events-none absolute"
        style={{
          top: "-10%", left: "-5%",
          width: 600, height: 500,
          borderRadius: "50%",
          background: "radial-gradient(ellipse, rgba(34,114,255,0.07) 0%, transparent 70%)",
        }}
      />
      {/* Glow 2 — lower-right */}
      <div
        className="auth-glow-alt pointer-events-none absolute"
        style={{
          bottom: "-15%", right: "-8%",
          width: 500, height: 440,
          borderRadius: "50%",
          background: "radial-gradient(ellipse, rgba(34,114,255,0.05) 0%, transparent 70%)",
        }}
      />

      {/* Scan line — slides top→bottom */}
      <div className="auth-scanline pointer-events-none absolute inset-x-0 top-0" />

      {/* ── Theme toggle — top right ── */}
      <div className="absolute right-5 top-5 z-20">
        <button
          onClick={toggle}
          className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-white/10 bg-white/[0.05] text-white/40 backdrop-blur-sm transition hover:border-white/20 hover:bg-white/[0.09] hover:text-white/70"
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
            <h1 className="text-[2.6rem] font-black leading-[1.1] tracking-tight">
              Extract structured<br />data from any site.
            </h1>
            <p className="mt-4 max-w-[280px] text-[0.88rem] leading-relaxed text-white/35">
              Connect your LLM, define extraction fields, and pull clean structured data from any page.
            </p>
          </div>

          <ul className="space-y-4 text-sm text-white/38">
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
          className="rounded-2xl border p-8"
          style={{ background: "#18181E", borderColor: "#282832" }}
        >
          {/* Mobile-only wordmark */}
          <div className="mb-7 flex items-center gap-2.5 lg:hidden">
            <LogoMark size={28} />
            <span className="text-sm font-bold">ScrapeGPT</span>
          </div>

          <div className="mb-7">
            <h2 className="text-xl font-bold text-white">{title}</h2>
            <p className="mt-1 text-sm text-white/35">{subtitle}</p>
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
          <p className="text-right text-xs text-white/20">
            Forgot password?{" "}
            <span className="cursor-default font-medium text-white/28">Not yet available</span>
          </p>
        </div>

        <Button
          type="submit"
          className="mt-1 h-11 w-full rounded-xl text-sm font-semibold"
          disabled={submitting}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </Button>

        <p className="text-center text-sm text-white/28">
          New here?{" "}
          <Link
            className="font-semibold text-teal-subtle transition-colors hover:text-white/80"
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

        <p className="text-center text-sm text-white/28">
          Already have access?{" "}
          <Link
            className="font-semibold text-teal-subtle transition-colors hover:text-white/80"
            to="/login"
          >
            Sign in
          </Link>
        </p>
      </form>
    </AuthFrame>
  );
}
