import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, providerLabel, ROLE_LABELS, type ConfigView, type ProvidersView, type RoleSettings } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Button, Card, ErrorNote, SectionTitle, cx, inputClass } from "./ui";

const EFFORTS: [string, string][] = [
  ["low", "Bajo (rápido)"],
  ["medium", "Medio"],
  ["high", "Alto"],
  ["xhigh", "Muy alto"],
  ["max", "Máximo (más lento)"],
];

function useModels(provider: string | undefined) {
  return useQuery({
    queryKey: ["models", provider],
    queryFn: () => api.get<{ models: string[] }>(`/providers/${provider}/models`),
    enabled: !!provider,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

function RoleEditor({
  role,
  view,
  onClose,
}: {
  role: string;
  view: ProvidersView;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const own = view.configured[role] ?? {};
  const resolved = view.roles.find((r) => r.name === role) ?? view.roles[0];
  const [form, setForm] = useState<RoleSettings>({
    provider: own.provider ?? "",
    model: own.model ?? "",
    effort: own.effort ?? "",
    temperature: own.temperature ?? null,
  });
  const provider = form.provider || resolved?.provider;
  const models = useModels(provider);
  const save = useMutation({
    mutationFn: () =>
      api.put<ConfigView>("/config", {
        roles: {
          [role]: {
            provider: form.provider || null,
            model: form.model || null,
            effort: form.effort || null,
            temperature: form.temperature ?? null,
          },
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["setup"] });
      onClose();
    },
  });
  // Al cambiar de proveedor, el modelo anterior ya no aplica.
  useEffect(() => {
    if (form.provider !== (own.provider ?? "")) setForm((f) => ({ ...f, model: "" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.provider]);

  const listId = `models-${role}`;
  return (
    <form
      className="mt-2 grid gap-3 rounded-xl bg-surface-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="grid gap-1 text-sm">
        <span className="text-muted">Proveedor</span>
        <select
          className={inputClass}
          value={form.provider ?? ""}
          onChange={(e) => setForm({ ...form, provider: e.target.value })}
        >
          <option value="">{role === "default" ? "Automático (según tus claves)" : "Igual que «Todos»"}</option>
          {view.providers.map((p) => (
            <option key={p.name} value={p.name}>
              {providerLabel(p.name)}
              {p.key_present === false ? " (falta la clave)" : ""}
            </option>
          ))}
        </select>
      </label>
      <label className="grid gap-1 text-sm">
        <span className="text-muted">Modelo</span>
        <input
          className={inputClass}
          list={listId}
          placeholder={form.provider ? "El recomendado" : resolved?.model}
          value={form.model ?? ""}
          onChange={(e) => setForm({ ...form, model: e.target.value })}
        />
        <datalist id={listId}>
          {models.data?.models.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        {models.isError && <span className="text-xs text-muted">No pude listar los modelos (¿falta la clave?).</span>}
      </label>
      {provider === "anthropic" ? (
        <label className="grid gap-1 text-sm">
          <span className="text-muted">Esfuerzo de razonamiento</span>
          <select className={inputClass} value={form.effort ?? ""} onChange={(e) => setForm({ ...form, effort: e.target.value })}>
            <option value="">Por defecto</option>
            {EFFORTS.map(([e, label]) => (
              <option key={e} value={e}>
                {label}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <label className="grid gap-1 text-sm">
          <span className="text-muted">Temperatura (creatividad, 0–2)</span>
          <input
            className={inputClass}
            inputMode="decimal"
            placeholder={String(resolved?.temperature ?? "")}
            value={form.temperature ?? ""}
            onChange={(e) => {
              const v = e.target.value.replace(",", ".");
              setForm({ ...form, temperature: v === "" ? null : Number(v) });
            }}
          />
        </label>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          Guardar
        </Button>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
      </div>
      <ErrorNote error={save.error} />
    </form>
  );
}

export default function RolesCard() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  const setup = useSetup();
  const [open, setOpen] = useState<string | null>(null);
  const view = providers.data;
  const local = setup.data?.local ?? false;
  const def = view?.configured.default;

  return (
    <Card>
      <SectionTitle>Modelos del panel</SectionTitle>
      <p className="mb-2 text-sm text-muted">
        Cada rol del debate puede usar un proveedor y un modelo distinto. Lo que no cambies usa el de «Todos».
        {!local && ` ${LOCAL_ONLY_NOTE}`}
      </p>
      {view && (
        <ul className="divide-y divide-line text-sm">
          {["default", ...view.roles.map((r) => r.name)].map((role) => {
            const r = role === "default" ? null : view.roles.find((x) => x.name === role);
            const summary =
              role === "default"
                ? def?.provider
                  ? `${providerLabel(def.provider)}${def.model ? ` · ${def.model}` : ""}`
                  : "Automático"
                : `${providerLabel(r!.provider)} · ${r!.model}${r!.effort ? ` · ${r!.effort}` : ""}`;
            return (
              <li key={role} className="py-2">
                <div className="flex items-center gap-2">
                  <span className={cx("font-medium", role === "default" && "text-accent")}>{ROLE_LABELS[role] ?? role}</span>
                  <span className="ml-auto min-w-0 truncate text-muted">{summary}</span>
                  {local && open !== role && (
                    <Button variant="ghost" className="-my-2 shrink-0 px-2" onClick={() => setOpen(role)}>
                      Cambiar
                    </Button>
                  )}
                </div>
                {open === role && <RoleEditor role={role} view={view} onClose={() => setOpen(null)} />}
              </li>
            );
          })}
        </ul>
      )}
      <ErrorNote error={providers.error} />
    </Card>
  );
}
