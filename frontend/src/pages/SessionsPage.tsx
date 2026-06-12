import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { FormEvent, useState } from "react";
import { toast } from "sonner";
import { Alert } from "../components/ui/Alert";
import { Button } from "../components/ui/Button";
import { ConfirmDialog } from "../components/ui/ConfirmDialog";
import { Dialog } from "../components/ui/Dialog";
import { Field, Input } from "../components/ui/Input";
import { PageHeader } from "../components/ui/PageHeader";
import { Skeleton } from "../components/ui/Skeleton";
import { Table } from "../components/ui/Table";
import { api } from "../lib/api";
import { BrowserSession } from "../types";

function emptyForm() {
  return { name: "", domain: "", cookies_raw: "", user_agent: "", expires_at: "" };
}

function SessionRow({
  session,
  onDelete,
  isPending,
}: {
  session: BrowserSession;
  onDelete: () => void;
  isPending?: boolean;
}) {
  return (
    <tr>
      <td className="py-2 pr-4 font-medium">{session.name}</td>
      <td className="py-2 pr-4 font-mono text-sm">{session.domain}</td>
      <td className="py-2 pr-4 text-sm text-gray-500">
        {session.expires_at
          ? new Date(session.expires_at).toLocaleDateString()
          : "No expiry"}
      </td>
      <td className="py-2 pr-4">
        <span
          className={`text-xs font-semibold ${
            session.is_active ? "text-green-600" : "text-gray-400"
          }`}
        >
          {session.is_active ? "Active" : "Inactive"}
        </span>
      </td>
      <td className="py-2 text-right">
        <Button variant="ghost" onClick={onDelete} disabled={isPending}>
          <Trash2 className="h-4 w-4 text-red-500" />
        </Button>
      </td>
    </tr>
  );
}

export function SessionsPage() {
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<BrowserSession | null>(null);

  const { data: sessions, isLoading } = useQuery<BrowserSession[]>({
    queryKey: ["sessions"],
    queryFn: () => api.listSessions(),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.createSession({
        name: form.name,
        domain: form.domain,
        cookies_raw: form.cookies_raw,
        user_agent: form.user_agent || null,
        expires_at: form.expires_at || null,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setShowAdd(false);
      setForm(emptyForm());
      setFormError(null);
      toast.success("Session saved");
    },
    onError: (err) => {
      setFormError(err instanceof Error ? err.message : "Failed to save session");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.deleteSession(id),
    onSuccess: () => {
      setDeleteTarget(null);
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      toast.success("Session deleted");
    },
    onError: (err) => {
      setDeleteTarget(null);
      toast.error(err instanceof Error ? err.message : "Failed to delete session");
    },
  });

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!form.name.trim() || !form.domain.trim() || !form.cookies_raw.trim()) {
      setFormError("Name, domain, and cookies are required.");
      return;
    }
    createMutation.mutate();
  }

  return (
    <div className="container mx-auto max-w-4xl px-4 py-8">
      <PageHeader title="Browser Sessions">
        <Button onClick={() => setShowAdd(true)}>
          <Plus className="mr-1 h-4 w-4" />
          Add session
        </Button>
      </PageHeader>
      <p className="mb-6 text-sm text-muted">
        Store your browser cookies so ScrapeGPT can access sites you have legitimate access to.
      </p>

      {isLoading && <Skeleton className="mt-6 h-24 w-full" />}

      {!isLoading && (!sessions || sessions.length === 0) && (
        <div className="mt-8 rounded-lg border border-dashed border-gray-300 p-8 text-center text-gray-500">
          <p className="text-sm">No sessions yet.</p>
          <p className="mt-1 text-xs">
            Export cookies from your browser (e.g. with{" "}
            <span className="font-medium">Cookie-Editor</span>) and paste them
            here to unlock sites that require a logged-in session.
          </p>
        </div>
      )}

      {sessions && sessions.length > 0 && (
        <Table headings={["Name", "Domain", "Expires", "Status", ""]}>
          {sessions.map((s) => (
            <SessionRow
              key={s.id}
              session={s}
              onDelete={() => setDeleteTarget(s)}
              isPending={deleteMutation.isPending && deleteTarget?.id === s.id}
            />
          ))}
        </Table>
      )}

      {showAdd && <Dialog onClose={() => setShowAdd(false)} title="Add browser session">
        <form onSubmit={handleSubmit} className="space-y-4">
          <Field label="Name" hint="A label for this session, e.g. 'OATD account'">
            <Input
              value={form.name}
              disabled={createMutation.isPending}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="My OATD session"
            />
          </Field>
          <Field
            label="Domain"
            hint="Bare hostname — cookies will only be injected for this domain."
          >
            <Input
              value={form.domain}
              disabled={createMutation.isPending}
              onChange={(e) => setForm((f) => ({ ...f, domain: e.target.value }))}
              placeholder="oatd.org"
            />
          </Field>
          <Field
            label="Cookies"
            hint='Paste a JSON cookie array from Cookie-Editor, or "name=value; name2=value2" from DevTools.'
          >
            <textarea
              className="w-full rounded-md border border-gray-300 bg-white px-3 py-2 font-mono text-xs focus:border-blue-500 focus:outline-none disabled:opacity-60"
              rows={5}
              value={form.cookies_raw}
              disabled={createMutation.isPending}
              onChange={(e) => setForm((f) => ({ ...f, cookies_raw: e.target.value }))}
              placeholder={'[{"name":"cf_clearance","value":"abc...","domain":".oatd.org","path":"/"}]'}
            />
          </Field>
          <Field label="User-Agent" hint="Optional — leave blank to use the global default.">
            <Input
              value={form.user_agent}
              disabled={createMutation.isPending}
              onChange={(e) => setForm((f) => ({ ...f, user_agent: e.target.value }))}
              placeholder="Leave blank to use default"
            />
          </Field>
          <Field label="Expires at" hint="Optional — ISO date/time after which this session is ignored.">
            <Input
              type="datetime-local"
              value={form.expires_at}
              disabled={createMutation.isPending}
              onChange={(e) => setForm((f) => ({ ...f, expires_at: e.target.value }))}
            />
          </Field>

          {formError && <Alert tone="danger">{formError}</Alert>}

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" disabled={createMutation.isPending} onClick={() => setShowAdd(false)}>
              Cancel
            </Button>
            <Button type="submit" loading={createMutation.isPending}>
              Save session
            </Button>
          </div>
        </form>
      </Dialog>}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete session"
        message={`Delete the session "${deleteTarget?.name ?? ""}"? This cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
        isPending={deleteMutation.isPending}
      />
    </div>
  );
}
