import { Eye, EyeOff } from "lucide-react";
import { motion } from "motion/react";
import { FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";

// ---------------------------------------------------------------------------
// Shared primitives
// ---------------------------------------------------------------------------

function AuthFrame({
  title,
  subtitle,
  children
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <div className="relative min-h-screen overflow-hidden bg-porcelain px-4 py-10 text-ink">
      {/* Floating orb blobs */}
      <div className="pointer-events-none absolute -top-16 -left-16 h-64 w-64 rounded-full bg-teal/[0.08] blur-3xl animate-float-slow" />
      <div className="pointer-events-none absolute top-40 -right-8 h-48 w-48 rounded-full bg-teal/[0.06] blur-2xl animate-float-medium" />

      <div className="mx-auto grid min-h-[calc(100vh-5rem)] max-w-6xl items-center gap-8 lg:grid-cols-[1fr_440px]">
        {/* Hero column — desktop only */}
        <section className="hidden lg:block">
          <motion.div
            initial={{ opacity: 0, x: -16 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.05, duration: 0.4 }}
            className="mb-8 inline-flex rounded-full border border-teal/30 bg-teal-soft px-3 py-1 text-xs font-bold uppercase tracking-widest text-teal"
          >
            Open-source extraction console
          </motion.div>
          <motion.h1
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1, duration: 0.4 }}
            className="max-w-2xl text-5xl font-black leading-tight text-ink"
          >
            Control AI-assisted scraping from one clean workspace.
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.15, duration: 0.4 }}
            className="mt-5 max-w-xl text-base leading-7 text-muted"
          >
            Connect your provider keys, start URL jobs, and inspect backend
            status without touching Swagger for every operation.
          </motion.p>
          <div className="mt-8 grid max-w-sm grid-cols-3 gap-3">
            {["BYOK", "No credits", "Self-hosted"].map((item, i) => (
              <motion.div
                key={item}
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 + i * 0.08, duration: 0.35 }}
                className="rounded-xl border border-line bg-white p-4 shadow-sm"
              >
                <div className="h-1.5 w-6 rounded-full bg-teal/60 mb-2" />
                <div className="text-sm font-bold text-ink">{item}</div>
              </motion.div>
            ))}
          </div>
        </section>

        {/* Form card */}
        <motion.section
          initial={{ opacity: 0, y: 24, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
          className="rounded-2xl border border-line bg-surface p-8 shadow-panel"
        >
          <div className="mb-7">
            <div className="mb-5 inline-flex h-10 w-10 items-center justify-center rounded-xl bg-teal">
              <svg
                className="h-5 w-5 text-white"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M9 3H5a2 2 0 0 0-2 2v4m6-6h10a2 2 0 0 1 2 2v4M9 3v18m0 0h10a2 2 0 0 0 2-2V9M9 21H5a2 2 0 0 1-2-2V9m0 0h18" />
              </svg>
            </div>
            <h2 className="text-2xl font-black text-ink">{title}</h2>
            <p className="mt-1 text-sm text-muted">{subtitle}</p>
          </div>
          {children}
        </motion.section>
      </div>
    </div>
  );
}

function TextField({
  label,
  type = "text",
  value,
  onChange,
  autoComplete,
  required,
  placeholder
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
    <label className="grid gap-1.5 text-sm font-semibold text-ink">
      {label}
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={autoComplete}
        required={required}
        placeholder={placeholder}
        className="h-11 w-full rounded-xl border border-line bg-white px-3.5 text-sm text-ink outline-none transition placeholder:text-muted focus:border-teal focus:ring-2 focus:ring-teal/15"
      />
    </label>
  );
}

function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  required,
  hint
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
    <label className="grid gap-1.5 text-sm font-semibold text-ink">
      {label}
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          required={required}
          minLength={8}
          className="h-11 w-full rounded-xl border border-line bg-white px-3.5 pr-11 text-sm text-ink outline-none transition placeholder:text-muted focus:border-teal focus:ring-2 focus:ring-teal/15"
        />
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setVisible((v) => !v)}
          className="absolute right-3 top-1/2 -translate-y-1/2 text-muted transition hover:text-ink"
          aria-label={visible ? "Hide password" : "Show password"}
        >
          {visible ? (
            <EyeOff className="h-4 w-4" />
          ) : (
            <Eye className="h-4 w-4" />
          )}
        </button>
      </div>
      {hint ? <span className="text-xs font-normal text-muted">{hint}</span> : null}
    </label>
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
    <AuthFrame
      title="Welcome back"
      subtitle="Sign in to manage providers and scrape tasks."
    >
      <form className="grid gap-4" onSubmit={onSubmit}>
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.35 }}
        >
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            required
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.22, duration: 0.35 }}
          className="grid gap-1.5"
        >
          <PasswordField
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="current-password"
            required
          />
          <div className="text-right">
            <span className="text-xs text-muted">
              Forgot your password?{" "}
              <span className="font-semibold text-teal cursor-default opacity-60">
                Recovery not yet available
              </span>
            </span>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.29, duration: 0.35 }}
          whileTap={{ scale: 0.97 }}
        >
          <Button type="submit" className="mt-1 h-11 w-full rounded-xl text-base" disabled={submitting}>
            {submitting ? "Signing in..." : "Sign in"}
          </Button>
        </motion.div>

        <p className="text-center text-sm text-muted">
          New here?{" "}
          <Link className="font-semibold text-teal hover:text-teal-dark" to="/register">
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
    <AuthFrame
      title="Create account"
      subtitle="Start with auth, providers, and task monitoring."
    >
      <form className="grid gap-4" onSubmit={onSubmit}>
        {error ? <Alert tone="danger">{error}</Alert> : null}

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.35 }}
        >
          <TextField
            label="Email"
            type="email"
            value={email}
            onChange={setEmail}
            autoComplete="email"
            required
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.22, duration: 0.35 }}
        >
          <PasswordField
            label="Password"
            value={password}
            onChange={setPassword}
            autoComplete="new-password"
            required
            hint="Minimum 8 characters."
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.29, duration: 0.35 }}
        >
          <PasswordField
            label="Confirm password"
            value={confirm}
            onChange={setConfirm}
            autoComplete="new-password"
            required
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.36, duration: 0.35 }}
          whileTap={{ scale: 0.97 }}
        >
          <Button type="submit" className="mt-1 h-11 w-full rounded-xl text-base" disabled={submitting}>
            {submitting ? "Creating..." : "Create account"}
          </Button>
        </motion.div>

        <p className="text-center text-sm text-muted">
          Already have access?{" "}
          <Link className="font-semibold text-teal hover:text-teal-dark" to="/login">
            Sign in
          </Link>
        </p>
      </form>
    </AuthFrame>
  );
}
