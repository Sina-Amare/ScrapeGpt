import { Eye, EyeOff } from "lucide-react";
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
    <div className="min-h-screen bg-porcelain px-4 py-10 text-ink">
      <div className="mx-auto grid min-h-[calc(100vh-5rem)] max-w-6xl items-center gap-8 lg:grid-cols-[1fr_440px]">
        {/* Hero column — desktop only */}
        <section className="hidden lg:block">
          <div className="mb-8 inline-flex rounded-full border border-teal/30 bg-teal-soft px-3 py-1 text-xs font-bold uppercase tracking-widest text-teal">
            Open-source extraction console
          </div>
          <h1 className="max-w-2xl text-5xl font-black leading-tight text-ink">
            Control AI-assisted scraping from one clean workspace.
          </h1>
          <p className="mt-5 max-w-xl text-base leading-7 text-muted">
            Connect your provider keys, start URL jobs, and inspect backend
            status without touching Swagger for every operation.
          </p>
          <div className="mt-8 grid max-w-sm grid-cols-3 gap-3">
            {["BYOK", "No credits", "Self-hosted"].map((item) => (
              <div
                key={item}
                className="rounded-xl border border-line bg-white p-4 shadow-sm"
              >
                <div className="h-1.5 w-6 rounded-full bg-teal/60 mb-2" />
                <div className="text-sm font-bold text-ink">{item}</div>
              </div>
            ))}
          </div>
        </section>

        {/* Form card */}
        <section className="rounded-2xl border border-line bg-surface p-8 shadow-panel">
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
        </section>
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

        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
        />

        <div className="grid gap-1.5">
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
        </div>

        <Button type="submit" className="mt-1 h-11 rounded-xl text-base" disabled={submitting}>
          {submitting ? "Signing in..." : "Sign in"}
        </Button>

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

        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={setEmail}
          autoComplete="email"
          required
        />

        <PasswordField
          label="Password"
          value={password}
          onChange={setPassword}
          autoComplete="new-password"
          required
          hint="Minimum 8 characters."
        />

        <PasswordField
          label="Confirm password"
          value={confirm}
          onChange={setConfirm}
          autoComplete="new-password"
          required
        />

        <Button type="submit" className="mt-1 h-11 rounded-xl text-base" disabled={submitting}>
          {submitting ? "Creating..." : "Create account"}
        </Button>

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
