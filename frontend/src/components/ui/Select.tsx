import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { Children, isValidElement, SelectHTMLAttributes, useEffect, useRef, useState } from "react";

type OptionItem = { value: string; label: string; disabled?: boolean };

function parseOptions(children: React.ReactNode): OptionItem[] {
  const items: OptionItem[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child) || child.type !== "option") return;
    const props = child.props as { value?: string | number; children?: React.ReactNode; disabled?: boolean };
    items.push({
      value: String(props.value ?? ""),
      label: String(props.children ?? ""),
      disabled: props.disabled,
    });
  });
  return items;
}

type SelectProps = Pick<
  SelectHTMLAttributes<HTMLSelectElement>,
  "value" | "onChange" | "disabled" | "className" | "title" | "children"
>;

export function Select({ value = "", onChange, disabled, className = "", children, title }: SelectProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const options = parseOptions(children);
  const selected = options.find((o) => o.value === String(value));

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function pick(optValue: string) {
    if (onChange) {
      onChange({ target: { value: optValue } } as React.ChangeEvent<HTMLSelectElement>);
    }
    setOpen(false);
  }

  function onTriggerKey(e: React.KeyboardEvent) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen((v) => !v); }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      const idx = options.findIndex((o) => o.value === String(value));
      const next = options.find((o, i) => i > idx && !o.disabled);
      if (next) pick(next.value);
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      const idx = options.findIndex((o) => o.value === String(value));
      const prev = [...options].reverse().find((o, i) => options.length - 1 - i < idx && !o.disabled);
      if (prev) pick(prev.value);
    }
  }

  return (
    <div ref={containerRef} className={`relative ${className}`} title={title}>
      {/* Trigger */}
      <button
        type="button"
        role="combobox"
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={onTriggerKey}
        className={[
          "flex w-full h-10 items-center justify-between rounded-md border bg-surface px-3 text-sm text-ink",
          "outline-none transition",
          open
            ? "border-teal ring-2 ring-teal/15"
            : "border-line hover:border-teal/50",
          disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        ].join(" ")}
      >
        <span className={`truncate ${!selected?.label ? "text-muted" : ""}`}>
          {selected?.label ?? <span className="text-muted">—</span>}
        </span>
        <motion.span
          animate={{ rotate: open ? 180 : 0 }}
          transition={{ duration: 0.18, ease: "easeInOut" }}
          className="ml-2 flex-shrink-0"
        >
          <ChevronDown className="h-4 w-4 text-muted" />
        </motion.span>
      </button>

      {/* Dropdown */}
      <AnimatePresence>
        {open && (
          <motion.ul
            role="listbox"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
            className="absolute left-0 right-0 top-full z-50 mt-1 max-h-60 overflow-y-auto overscroll-contain rounded-md border border-line bg-surface py-1 shadow-lg shadow-ink/10 outline-none"
          >
            {options.map((opt) => {
              const isSelected = opt.value === String(value);
              return (
                <li
                  key={opt.value}
                  role="option"
                  aria-selected={isSelected}
                  aria-disabled={opt.disabled}
                  onClick={() => !opt.disabled && pick(opt.value)}
                  className={[
                    "flex cursor-pointer items-center px-3 py-2 text-sm transition select-none",
                    opt.disabled
                      ? "cursor-not-allowed text-muted/40"
                      : isSelected
                      ? "bg-teal text-white"
                      : "text-ink hover:bg-teal/[0.08]",
                  ].join(" ")}
                >
                  {opt.label}
                </li>
              );
            })}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}
