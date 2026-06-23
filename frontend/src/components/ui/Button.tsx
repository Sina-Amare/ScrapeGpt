import { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variantClasses: Record<ButtonVariant, string> = {
  primary:
    "btn-primary-shimmer bg-primary text-onprimary shadow-sm hover:bg-[var(--c-primary-hover)] focus-visible:ring-accent",
  secondary:
    "border border-line bg-surface2 text-ink hover:border-accent/60 hover:text-accent focus-visible:ring-accent",
  ghost: "text-muted hover:bg-porcelain hover:text-ink focus-visible:ring-accent",
  danger:
    "border border-danger/30 bg-danger/10 text-danger hover:bg-danger/20 hover:border-danger/50 focus-visible:ring-danger"
};

export function Button({
  children,
  className = "",
  variant = "primary",
  type = "button",
  loading,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  variant?: ButtonVariant;
  loading?: boolean;
}) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={`inline-flex h-10 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-55 ${variantClasses[variant]} ${className}`}
      {...props}
    >
      {loading && <Spinner />}
      {children}
    </button>
  );
}
