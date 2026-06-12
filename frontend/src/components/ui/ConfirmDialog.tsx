import { ReactNode } from "react";
import { Button } from "./Button";
import { Dialog } from "./Dialog";

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  variant = "danger",
  onConfirm,
  onCancel,
  isPending = false
}: {
  open: boolean;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
  isPending?: boolean;
}) {
  if (!open) return null;

  return (
    <Dialog title={title} onClose={onCancel}>
      <div className="space-y-5">
        <p className="text-sm text-ink">{message}</p>
        <div className="flex justify-end gap-3">
          <Button variant="secondary" onClick={onCancel} disabled={isPending}>
            Cancel
          </Button>
          <Button variant={variant} onClick={onConfirm} loading={isPending}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
