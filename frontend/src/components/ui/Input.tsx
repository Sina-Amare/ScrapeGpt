import { InputHTMLAttributes, ReactNode } from "react";

export function Field({
  label,
  hint,
  children
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="grid grid-cols-1 gap-1.5 text-sm font-medium text-ink min-w-0 w-full">
      <span>{label}</span>
      {children}
      {hint ? <span className="text-xs font-normal text-muted">{hint}</span> : null}
    </label>
  );
}

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={`w-full h-10 rounded-lg border border-line bg-porcelain px-3.5 text-sm text-ink outline-none transition placeholder:text-muted/70 hover:border-line focus:border-accent focus:bg-surface focus:ring-2 focus:ring-accent/25 autofill-surface ${className}`}
      {...props}
    />
  );
}
