import { ArrowLeft } from "lucide-react";
import { ReactNode } from "react";
import { Button } from "./Button";

type PrimaryAction = {
  label: string;
  onClick: () => void;
  loading?: boolean;
  disabled?: boolean;
  icon?: ReactNode;
};

type BackAction = {
  label?: string;
  onClick: () => void;
  disabled?: boolean;
};

/**
 * Sticky footer for the wizard: an optional Back action + gate hint on the left,
 * the primary "next" action on the right. The primary action is the one place a
 * user advances the flow; gates disable it and surface a hint.
 */
export function NextStepBar({
  primary,
  back,
  hint,
}: {
  primary?: PrimaryAction;
  back?: BackAction;
  hint?: string | null;
}) {
  if (!primary && !back) return null;
  return (
    <div className="sticky bottom-0 z-20 -mx-4 mt-2 border-t border-line bg-surface/95 px-4 py-3 backdrop-blur md:-mx-8 md:px-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          {back ? (
            <Button variant="secondary" onClick={back.onClick} disabled={back.disabled}>
              <ArrowLeft className="h-4 w-4" />
              {back.label ?? "Back"}
            </Button>
          ) : (
            <span />
          )}
          {hint ? <span className="text-sm text-muted">{hint}</span> : null}
        </div>
        {primary ? (
          <Button
            onClick={primary.onClick}
            loading={primary.loading}
            disabled={primary.disabled}
          >
            {primary.icon}
            {primary.label}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
