import { ChevronDown } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import {
  Children,
  isValidElement,
  SelectHTMLAttributes,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

type OptionItem = { value: string; label: string; disabled?: boolean };

// Flatten an option's children into plain text. Using String() directly would
// comma-join array children (e.g. `{name} ({provider} / {model})` becomes
// "name, (,provider, / ,model,)"), so concatenate recursively instead.
function nodeToText(node: React.ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeToText).join("");
  if (isValidElement(node)) {
    return nodeToText((node.props as { children?: React.ReactNode }).children);
  }
  return "";
}

function parseOptions(children: React.ReactNode): OptionItem[] {
  const items: OptionItem[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child) || child.type !== "option") return;
    const props = child.props as { value?: string | number; children?: React.ReactNode; disabled?: boolean };
    items.push({
      value: String(props.value ?? ""),
      label: nodeToText(props.children),
      disabled: props.disabled,
    });
  });
  return items;
}

type SelectProps = Pick<
  SelectHTMLAttributes<HTMLSelectElement>,
  "value" | "onChange" | "disabled" | "className" | "title" | "children"
>;

// Fixed-position coordinates for the portaled menu. Either `top` (open below the
// trigger) or `bottom` (flipped above it) is set, never both.
type MenuPos = { left: number; top?: number; bottom?: number; width: number; maxHeight: number };

export function Select({ value = "", onChange, disabled, className = "", children, title }: SelectProps) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<MenuPos | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const options = parseOptions(children);
  const selected = options.find((o) => o.value === String(value));

  // Position the menu against the trigger with fixed coordinates so it escapes
  // any clipping/scrolling ancestor (the page card, the main scroll area), and
  // flip it above the trigger when there isn't room below. maxHeight is capped
  // to the available viewport space so a long list always scrolls internally
  // instead of running off the bottom of the screen.
  function positionMenu() {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    const gap = 4;
    const spaceBelow = window.innerHeight - rect.bottom - margin;
    const spaceAbove = rect.top - margin;
    const placeBelow = spaceBelow >= 160 || spaceBelow >= spaceAbove;
    const maxHeight = Math.max(96, Math.min(240, placeBelow ? spaceBelow : spaceAbove));
    setMenuPos({
      left: rect.left,
      width: rect.width,
      maxHeight,
      ...(placeBelow
        ? { top: rect.bottom + gap }
        : { bottom: window.innerHeight - rect.top + gap }),
    });
  }

  // Position before paint when opening so the menu never flashes in the wrong spot.
  useLayoutEffect(() => {
    if (open) positionMenu();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      const target = e.target as Node;
      // The menu is portaled outside containerRef, so check both.
      const inContainer = containerRef.current?.contains(target);
      const inMenu = menuRef.current?.contains(target);
      if (!inContainer && !inMenu) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function reposition() {
      positionMenu();
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", reposition);
    // Capture scrolls from any ancestor scroll container, not just window.
    window.addEventListener("scroll", reposition, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
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
    <div ref={containerRef} className={`relative min-w-0 ${className}`} title={title}>
      {/* Trigger */}
      <button
        ref={triggerRef}
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
        <span
          className={`min-w-0 flex-1 truncate text-left ${!selected?.label ? "text-muted" : ""}`}
          title={selected?.label || undefined}
        >
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

      {/* Dropdown — portaled to <body> with fixed positioning so it can't be
          clipped by a card/scroll container and can't run off the viewport.
          Rendering outside the Field's <label> also stops option clicks from
          re-activating the trigger and reopening the menu. */}
      {createPortal(
        <AnimatePresence>
          {open && menuPos && (
            <motion.ul
              ref={menuRef}
              role="listbox"
              initial={{ opacity: 0, y: -6, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -6, scale: 0.98 }}
              transition={{ duration: 0.14, ease: [0.16, 1, 0.3, 1] }}
              style={{
                position: "fixed",
                left: menuPos.left,
                width: menuPos.width,
                maxHeight: menuPos.maxHeight,
                ...(menuPos.top != null ? { top: menuPos.top } : { bottom: menuPos.bottom }),
              }}
              className="z-[60] overflow-y-auto overscroll-contain rounded-md border border-line bg-surface py-1 shadow-lg shadow-ink/10 outline-none"
            >
              {options.map((opt) => {
                const isSelected = opt.value === String(value);
                return (
                  <li
                    key={opt.value}
                    role="option"
                    aria-selected={isSelected}
                    aria-disabled={opt.disabled}
                    title={opt.label}
                    // Select on mousedown (not click) and preventDefault so the
                    // choice registers immediately, instead of needing a second
                    // click after the trigger blurs.
                    onMouseDown={(event) => {
                      event.preventDefault();
                      if (!opt.disabled) pick(opt.value);
                    }}
                    className={[
                      "block cursor-pointer truncate px-3 py-2 text-sm transition select-none",
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
        </AnimatePresence>,
        document.body
      )}
    </div>
  );
}
