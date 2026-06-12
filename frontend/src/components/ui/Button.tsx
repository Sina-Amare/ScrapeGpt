import { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "bg-teal text-white hover:bg-teal-dark focus-visible:ring-teal",
  secondary:
    "border border-line bg-surface text-ink hover:border-teal hover:text-teal focus-visible:ring-teal",
  ghost: "text-muted hover:bg-porcelain hover:text-ink focus-visible:ring-teal",
  danger: "bg-danger text-white hover:bg-red-800 focus-visible:ring-danger"
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
