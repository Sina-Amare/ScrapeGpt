import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import { Select } from "./Select";

// Shared provider picker (mirrors the one in NewProjectPage). Lets the user
// choose which configured AI provider to use — e.g. when retrying a failed
// analysis with a different provider/model.
export function ProviderSelect({
  value,
  onChange,
  className
}: {
  value: number | null;
  onChange: (id: number | null) => void;
  className?: string;
}) {
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.listProviders });

  return (
    <Select
      value={value == null ? "" : String(value)}
      onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}
      className={className}
    >
      <option value="">Default provider</option>
      {(providers.data ?? []).map((provider) => (
        <option key={provider.id} value={String(provider.id)}>
          {provider.name} ({provider.provider} / {provider.model})
        </option>
      ))}
    </Select>
  );
}
