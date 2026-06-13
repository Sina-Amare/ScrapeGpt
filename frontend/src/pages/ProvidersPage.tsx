import { useMutation, useMutationState, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Pencil,
  Plus,
  TestTube2,
  Trash2,
  X,
  XCircle
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { FormEvent, useEffect, useState } from "react";
import { toast } from "sonner";
import { Alert } from "../components/ui/Alert";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { Field, Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Select } from "../components/ui/Select";
import { Skeleton } from "../components/ui/Skeleton";
import { Table } from "../components/ui/Table";
import { ApiError, api } from "../lib/api";
import { ProviderConfig, ProviderCreateInput, ProviderTestResponse } from "../types";

const providerOptions = ["openai", "anthropic", "gemini", "openrouter", "mistral", "ollama"];

function emptyProviderForm(): ProviderCreateInput {
  return { name: "", provider: "openai", model: "", api_key: "", is_default: false };
}

function providerError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Provider configuration conflict. Refresh the list and try again.";
  }
  if (error instanceof ApiError && error.status === 401) {
    return "Password confirmation failed.";
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Too many reveal attempts. Wait a moment and try again.";
  }
  return error instanceof Error ? error.message : "Provider operation failed";
}

// ---------------------------------------------------------------------------
// API key input with eye toggle
// ---------------------------------------------------------------------------

function ApiKeyField({
  value,
  onChange,
  required,
  placeholder
}: {
  value: string;
  onChange: (v: string) => void;
  required: boolean;
  placeholder?: string;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="relative">
      <input
        type={visible ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="new-password"
        required={required}
        placeholder={placeholder}
        className="h-10 w-full rounded-xl border border-line bg-white px-3.5 pr-11 font-mono text-sm text-ink outline-none transition placeholder:text-muted focus:border-teal focus:ring-2 focus:ring-teal/15"
      />
      <button
        type="button"
        tabIndex={-1}
        onClick={() => setVisible((v) => !v)}
        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted transition hover:text-ink"
        aria-label={visible ? "Hide key" : "Show key"}
      >
        {visible ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capability flag row
// ---------------------------------------------------------------------------

function CapFlag({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      {ok ? (
        <CheckCircle2 className="h-4 w-4 shrink-0 text-green-500" />
      ) : (
        <XCircle className="h-4 w-4 shrink-0 text-red-400" />
      )}
      <span className={ok ? "text-ink" : "text-muted"}>{label}</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Capability panel (shown after test) — animated sequential reveal
// ---------------------------------------------------------------------------

function AnimatedCapabilityPanel({
  providerName,
  result,
  onClose
}: {
  providerName: string;
  result: ProviderTestResponse;
  onClose: () => void;
}) {
  const [revealed, setRevealed] = useState(0);
  const flags = result.capability_flags as Record<string, unknown>;

  useEffect(() => {
    const timers = [
      setTimeout(() => setRevealed(1), 0),
      setTimeout(() => setRevealed(2), 600),
      setTimeout(() => setRevealed(3), 1100),
    ];
    return () => timers.forEach(clearTimeout);
  }, []);

  const capFlags = [
    { label: "Connectivity", ok: !!flags.connectivity },
    { label: "JSON output validated", ok: !!flags.validated_json },
    { label: `Native JSON mode${flags.native_json ? "" : " (prompt-based fallback)"}`, ok: !!flags.native_json },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="rounded-xl border border-line bg-surface p-5 shadow-panel"
    >
      <div className="mb-4 flex items-start justify-between gap-2">
        <div>
          <p className="text-xs font-bold uppercase tracking-widest text-muted">Test result</p>
          <h3 className="mt-0.5 text-sm font-bold text-ink">{providerName}</h3>
        </div>
        <button
          onClick={onClose}
          className="text-muted transition hover:text-ink"
          aria-label="Dismiss"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="space-y-3">
        {capFlags.map((flag, i) => (
          <div key={flag.label} className="min-h-[1.5rem]">
            <AnimatePresence mode="wait">
              {i < revealed ? (
                <motion.div
                  key="revealed"
                  initial={{ opacity: 0, x: -12, height: 0 }}
                  animate={{ opacity: 1, x: 0, height: "auto" }}
                  transition={{ duration: 0.3 }}
                >
                  <CapFlag label={flag.label} ok={flag.ok} />
                </motion.div>
              ) : (
                <motion.div
                  key="skeleton"
                  initial={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="flex items-center gap-2"
                >
                  <div className="h-4 w-4 animate-pulse rounded-full bg-line" />
                  <div className="h-3 w-32 animate-pulse rounded bg-line" />
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>

      <AnimatePresence>
        {revealed >= 3 && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.15 }}
            className={`mt-4 rounded-lg p-3 text-xs font-medium ${
              result.ok
                ? "border border-green-100 bg-green-50 text-green-700"
                : "border border-red-100 bg-red-50 text-red-700"
            }`}
          >
            {result.ok ? (
              "Provider is ready for use in the extraction pipeline."
            ) : (
              <>
                <span className="font-semibold">{(flags.error_type as string) ?? "Error"}: </span>
                {result.error}
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

// ---------------------------------------------------------------------------
// Reveal key dialog
// ---------------------------------------------------------------------------

function RevealKeyDialog({
  providerName,
  apiKey,
  onClose
}: {
  providerName: string;
  apiKey: string;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);

  async function copyKey() {
    await navigator.clipboard.writeText(apiKey);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <Dialog title={`API key — ${providerName}`} onClose={onClose}>
      <p className="mb-4 text-sm text-muted">
        This key is decrypted on demand and never stored in plaintext. Copy it
        and store it in a secure vault if needed.
      </p>
      <div className="flex items-center gap-2">
        <input
          type="text"
          readOnly
          value={apiKey}
          autoComplete="off"
          className="h-10 min-w-0 flex-1 rounded-xl border border-line bg-porcelain px-3.5 font-mono text-sm text-ink outline-none"
        />
        <Button
          variant="secondary"
          className="h-10 shrink-0 gap-1.5 px-3"
          onClick={() => void copyKey()}
        >
          {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </Dialog>
  );
}

function ConfirmRevealDialog({
  providerName,
  onCancel,
  onSubmit,
  submitting
}: {
  providerName: string;
  onCancel: () => void;
  onSubmit: (password: string) => void;
  submitting: boolean;
}) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const passwordInput = event.currentTarget.elements.namedItem("password");
    const password =
      passwordInput && "value" in passwordInput
        ? String(passwordInput.value)
        : "";
    onSubmit(password);
  }

  return (
    <Dialog title={`Reveal key — ${providerName}`} onClose={onCancel}>
      <form className="grid gap-4" onSubmit={submit} autoComplete="off">
        <Field
          label="Account password"
          hint="Password confirmation is required before decrypting a stored provider key."
        >
          <Input
            type="password"
            name="password"
            autoComplete="off"
            required
          />
        </Field>
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
          <Button type="submit" disabled={submitting}>
            {submitting ? "Checking..." : "Reveal key"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function ConfirmDeleteDialog({
  providerName,
  onCancel,
  onConfirm,
  submitting
}: {
  providerName: string;
  onCancel: () => void;
  onConfirm: () => void;
  submitting: boolean;
}) {
  return (
    <Dialog title="Delete provider" onClose={onCancel}>
      <div className="grid gap-4">
        <p className="text-sm text-muted">
          Are you sure you want to delete the provider config <strong className="text-ink">"{providerName}"</strong>?
          This action is permanent and cannot be undone. Any tasks referencing this provider will fall back to other configured options.
        </p>
        <div className="flex justify-end gap-2 mt-2">
          <Button variant="secondary" onClick={onCancel} disabled={submitting}>
            Cancel
          </Button>
          <Button variant="danger" onClick={onConfirm} disabled={submitting}>
            {submitting ? "Deleting..." : "Delete provider"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Provider form
// ---------------------------------------------------------------------------

function ProviderForm({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
  submitting,
  requireApiKey
}: {
  initial: ProviderCreateInput;
  submitLabel: string;
  onSubmit: (input: ProviderCreateInput) => void;
  onCancel: () => void;
  submitting: boolean;
  requireApiKey: boolean;
}) {
  const [form, setForm] = useState(initial);

  function update<K extends keyof ProviderCreateInput>(key: K, value: ProviderCreateInput[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(form);
  }

  return (
    <form className="grid gap-4" onSubmit={submit} autoComplete="off">
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="Display name">
          <Input
            value={form.name}
            onChange={(event) => update("name", event.target.value)}
            autoComplete="off"
            required
          />
        </Field>
        <Field label="Provider">
          <Select
            value={form.provider}
            onChange={(event) => update("provider", event.target.value)}
          >
            {providerOptions.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <Field label="Model">
        <Input
          value={form.model}
          placeholder="gpt-4o-mini, claude-3-5-sonnet, gemini/gemini-1.5-pro"
          onChange={(event) => update("model", event.target.value)}
          autoComplete="off"
          required
        />
      </Field>
      <Field
        label="API key"
        hint={
          requireApiKey
            ? "Encrypted at rest with AES-128 Fernet. Use the eye icon to verify before saving."
            : "Leave blank to keep existing key. Use the Reveal button on the row to view it."
        }
      >
        <ApiKeyField
          value={form.api_key}
          onChange={(v) => update("api_key", v)}
          required={requireApiKey}
          placeholder={requireApiKey ? "" : "Leave blank to keep existing key"}
        />
      </Field>
      <label className="flex items-center gap-2 text-sm font-semibold text-ink">
        <input
          type="checkbox"
          checked={Boolean(form.is_default)}
          onChange={(event) => update("is_default", event.target.checked)}
          className="h-4 w-4 rounded border-line text-teal focus:ring-teal"
        />
        Set as default provider
      </label>
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" disabled={submitting}>
          {submitting ? "Saving..." : submitLabel}
        </Button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function ProvidersPage() {
  const queryClient = useQueryClient();
  const [dialog, setDialog] = useState<"create" | "edit" | null>(null);
  const [selected, setSelected] = useState<ProviderConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Which providers are currently being tested, read from the mutation cache so
  // it survives navigating away and back (local state would reset on remount and
  // wrongly re-enable the Test button while the request is still running).
  const pendingTestIds = useMutationState({
    filters: { mutationKey: ["provider-test"], status: "pending" },
    select: (mutation) => mutation.state.variables as number
  });
  const [lastTest, setLastTest] = useState<{
    name: string;
    result: ProviderTestResponse;
  } | null>(null);
  const [revealPrompt, setRevealPrompt] = useState<{
    id: number;
    name: string;
  } | null>(null);
  const [revealDialog, setRevealDialog] = useState<{
    id: number;
    name: string;
    key: string;
  } | null>(null);
  const [deletePrompt, setDeletePrompt] = useState<{
    id: number;
    name: string;
  } | null>(null);

  const providers = useQuery({
    queryKey: ["providers"],
    queryFn: api.listProviders
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["providers"] });

  const create = useMutation({
    mutationFn: api.createProvider,
    onSuccess: () => {
      setDialog(null);
      setError(null);
      void invalidate();
    },
    onError: (err) => {
      setError(providerError(err));
      void invalidate();
    }
  });

  const update = useMutation({
    mutationFn: ({ id, input }: { id: number; input: ProviderCreateInput }) =>
      api.updateProvider(id, {
        name: input.name,
        provider: input.provider,
        model: input.model,
        api_key: input.api_key || undefined,
        is_default: input.is_default
      }),
    onSuccess: () => {
      setDialog(null);
      setSelected(null);
      setError(null);
      void invalidate();
    },
    onError: (err) => {
      setError(providerError(err));
      void invalidate();
    }
  });

  const remove = useMutation({
    mutationFn: api.deleteProvider,
    onSuccess: (_data, id) => {
      setRevealPrompt((current) => (current?.id === id ? null : current));
      setRevealDialog((current) => (current?.id === id ? null : current));
      setDeletePrompt((current) => (current?.id === id ? null : current));
      void invalidate();
    },
    onError: (err) => setError(providerError(err))
  });

  const test = useMutation({
    // Stable key so the in-progress state is discoverable from the mutation
    // cache (via useMutationState) regardless of which page is mounted.
    mutationKey: ["provider-test"],
    mutationFn: (id: number) => api.testProvider(id),
    // meta.notify runs from the global MutationCache, so completion is reported
    // even if the user navigated away from this page while the test ran.
    meta: {
      notify: (data: unknown) => {
        const result = data as ProviderTestResponse;
        const name =
          providers.data?.find((p) => p.id === result.provider_config_id)?.name ?? "Provider";
        if (result.ok) {
          toast.success(`${name} — provider test passed`);
        } else {
          toast.error(`${name} — test failed: ${result.error ?? "open Providers for details"}`);
        }
        void invalidate();
      },
      notifyError: (err: unknown) => {
        toast.error(err instanceof Error ? err.message : "Provider test failed");
        void invalidate();
      }
    },
    onSuccess: (result, id) => {
      // In-page detail panel — only relevant while still on this page.
      const name = providers.data?.find((p) => p.id === id)?.name ?? `Provider #${id}`;
      setLastTest({ name, result });
    },
    onError: (err) => setError(providerError(err)),
  });

  const reveal = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      api.revealProviderKey(id, { password }),
    onSuccess: (data, variables) => {
      const name =
        providers.data?.find((p) => p.id === variables.id)?.name ?? `Provider #${variables.id}`;
      setRevealPrompt(null);
      setRevealDialog({ id: variables.id, name, key: data.api_key });
    },
    onError: (err) => setError(providerError(err))
  });

  return (
    <>
      <PageHeader title="Providers" eyebrow="BYOK settings">
        <Button onClick={() => setDialog("create")}>
          <Plus className="h-4 w-4" />
          Add provider
        </Button>
      </PageHeader>

      <div className="grid gap-3">
        {error ? <Alert tone="danger">{error}</Alert> : null}
      </div>

      {/* Capability panel — shown after a test run */}
      {lastTest ? (
        <div className="mt-3">
          <AnimatedCapabilityPanel
            providerName={lastTest.name}
            result={lastTest.result}
            onClose={() => setLastTest(null)}
          />
        </div>
      ) : null}

      <section className="mt-5">
        {providers.isLoading ? (
          <div className="grid gap-3">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : providers.data?.length ? (
          <Table headings={["Name", "Provider", "Model", "Status", "Actions"]}>
            {providers.data.map((provider) => (
              <tr key={provider.id}>
                <td className="px-4 py-4 font-semibold text-ink">{provider.name}</td>
                <td className="px-4 py-4 text-muted">{provider.provider}</td>
                <td className="px-4 py-4 text-muted">{provider.model}</td>
                <td className="px-4 py-4">
                  <div className="flex flex-wrap gap-2">
                    {provider.is_default ? <Badge tone="accent">Default</Badge> : null}
                    {provider.capability_flags?.validated_json ? (
                      <Badge tone="success">JSON tested</Badge>
                    ) : (
                      <Badge>Untested</Badge>
                    )}
                  </div>
                </td>
                <td className="px-4 py-4">
                  {/* Actions: wrap on mobile, single no-wrap row on desktop. The
                      Test button keeps a fixed width so the "Testing…" busy state
                      never shifts or wraps the sibling buttons. */}
                  <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-nowrap sm:items-center sm:justify-end">
                    <Button
                      variant="secondary"
                      className="h-9 px-3 sm:min-w-[7rem] sm:justify-center"
                      onClick={() => test.mutate(provider.id)}
                      loading={pendingTestIds.includes(provider.id)}
                      disabled={pendingTestIds.length > 0}
                    >
                      <TestTube2 className="h-4 w-4" />
                      {pendingTestIds.includes(provider.id) ? "Testing…" : "Test"}
                    </Button>
                    <Button
                      variant="secondary"
                      className="h-9 px-3"
                      onClick={() => setRevealPrompt({ id: provider.id, name: provider.name })}
                      disabled={reveal.isPending}
                    >
                      <Eye className="h-4 w-4" />
                      Reveal
                    </Button>
                    <Button
                      variant="secondary"
                      className="h-9 px-3"
                      onClick={() => {
                        setSelected(provider);
                        setDialog("edit");
                      }}
                    >
                      <Pencil className="h-4 w-4" />
                      Edit
                    </Button>
                    <Button
                      variant="danger"
                      className="h-9 px-3"
                      disabled={remove.isPending}
                      onClick={() => setDeletePrompt({ id: provider.id, name: provider.name })}
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </Table>
        ) : (
          <div className="card-hover rounded-md border border-line bg-surface p-10 text-center shadow-panel">
            <KeyRound className="mx-auto h-10 w-10 text-teal" />
            <h2 className="mt-4 text-lg font-bold text-ink">No providers configured</h2>
            <p className="mx-auto mt-2 max-w-md text-sm text-muted">
              Add your first provider to test encrypted BYOK storage and prepare
              for the analysis pipeline.
            </p>
            <Button className="mt-5" onClick={() => setDialog("create")}>
              <Plus className="h-4 w-4" />
              Add provider
            </Button>
          </div>
        )}
      </section>

      {/* Create dialog */}
      {dialog === "create" ? (
        <Dialog title="Add provider" onClose={() => setDialog(null)}>
          <ProviderForm
            initial={emptyProviderForm()}
            submitLabel="Save provider"
            submitting={create.isPending}
            requireApiKey
            onCancel={() => setDialog(null)}
            onSubmit={(input) => create.mutate(input)}
          />
        </Dialog>
      ) : null}

      {/* Edit dialog */}
      {dialog === "edit" && selected ? (
        <Dialog title="Edit provider" onClose={() => setDialog(null)}>
          <ProviderForm
            initial={{
              name: selected.name,
              provider: selected.provider,
              model: selected.model,
              api_key: "",
              is_default: selected.is_default
            }}
            submitLabel="Update provider"
            submitting={update.isPending}
            requireApiKey={false}
            onCancel={() => setDialog(null)}
            onSubmit={(input) => update.mutate({ id: selected.id, input })}
          />
        </Dialog>
      ) : null}

      {revealPrompt ? (
        <ConfirmRevealDialog
          providerName={revealPrompt.name}
          submitting={reveal.isPending}
          onCancel={() => setRevealPrompt(null)}
          onSubmit={(password) => reveal.mutate({ id: revealPrompt.id, password })}
        />
      ) : null}

      {/* Reveal key dialog */}
      {revealDialog ? (
        <RevealKeyDialog
          providerName={revealDialog.name}
          apiKey={revealDialog.key}
          onClose={() => setRevealDialog(null)}
        />
      ) : null}

      {deletePrompt ? (
        <ConfirmDeleteDialog
          providerName={deletePrompt.name}
          submitting={remove.isPending}
          onCancel={() => setDeletePrompt(null)}
          onConfirm={() => remove.mutate(deletePrompt.id)}
        />
      ) : null}
    </>
  );
}
