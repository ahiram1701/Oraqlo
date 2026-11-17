import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
  api,
  providerLabel,
  ROLE_LABELS,
  type CatalogModel,
  type ConfigView,
  type ModelList,
  type ProvidersView,
  type RoleSettings,
} from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, SectionTitle, cx, inputClass, useFeedback } from "./ui";

const EFFORTS: [string, string][] = [
  ["low", "Bajo (rápido)"],
  ["medium", "Medio"],
  ["high", "Alto"],
  ["xhigh", "Muy alto"],
  ["max", "Máximo (más lento)"],
];

const OTHER = "__otro__";

function useModels(provider: string | undefined) {
  return useQuery({
    queryKey: ["models", provider],
    queryFn: () => api.get<ModelList>(`/providers/${provider}/models`),
    enabled: !!provider,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

/**
 * Proveedor y modelo de un rol con los cambios del formulario aún sin guardar. Es la
 * misma regla que `config.role()` en el servidor: lo propio, si no lo de «Todos»
 * (su modelo solo si el proveedor coincide), si no el recomendado del proveedor.
 * Antes se usaba el rol ya resuelto, que podía traer un modelo propio anterior.
 */
export function resolveRole(view: ProvidersView, role: string, form: RoleSettings): { provider: string; model: string } {
  const isDefault = role === "default";
  const base = isDefault ? form : (view.configured.default ?? {});
  const own = isDefault ? {} : form;
  const baseProvider = base.provider || view.auto?.provider || "";
  const provider = own.provider || baseProvider;
  const inherited = provider === baseProvider ? base.model : null;
  const model = own.model || inherited || view.default_models?.[provider] || view.auto?.model || "";
  return { provider, model };
}

/** El catálogo del proveedor; sin él (proveedores que solo listan nombres), los nombres. */
export function catalogOf(data: ModelList | undefined): CatalogModel[] {
  return data?.catalog ?? (data?.models ?? []).map((name) => ({ name, cloud: false, installed: true }));
}

/** Los proveedores que entienden el «esfuerzo de razonamiento». */
export function supportsEffort(kind: string | undefined): boolean {
  return kind === "anthropic" || kind === "ollama";
}

function sizeLabel(bytes?: number | null) {
  if (!bytes) return "";
  const gb = bytes / 1e9;
  return gb >= 1 ? ` · ${gb >= 100 ? Math.round(gb) : gb.toFixed(1)} GB` : "";
}

function ModelPicker({
  catalog,
  value,
  placeholder,
  onChange,
}: {
  catalog: CatalogModel[];
  value: string;
  placeholder: string;
  onChange: (v: string) => void;
}) {
  const known = catalog.some((m) => m.name === value);
  const [typing, setTyping] = useState(!!value && !known && catalog.length > 0);
  const installed = catalog.filter((m) => m.installed);
  const remote = catalog.filter((m) => !m.installed);

  if (!catalog.length || typing) {
    return (
      <>
        <input
          className={inputClass}
          aria-label="Modelo"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
        {catalog.length > 0 && (
          <span className="text-xs text-muted">
            {value && !known && "Ese nombre no está en el catálogo: compruébalo con «Probar». "}
            <button type="button" className="underline" onClick={() => setTyping(false)}>
              Elegir de la lista
            </button>
          </span>
        )}
      </>
    );
  }
  return (
    <select
      className={inputClass}
      aria-label="Modelo"
      value={value}
      onChange={(e) => (e.target.value === OTHER ? (setTyping(true), onChange("")) : onChange(e.target.value))}
    >
      <option value="">{placeholder}</option>
      {installed.length > 0 && (
        <optgroup label={remote.length ? "Descargados en este PC" : "Disponibles"}>
          {installed.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.cloud ? " (nube)" : ""}
              {sizeLabel(m.size)}
            </option>
          ))}
        </optgroup>
      )}
      {remote.length > 0 && (
        <optgroup label="En la nube de Ollama">
          {remote.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </optgroup>
      )}
      <option value={OTHER}>Otro (escribir el nombre)…</option>
    </select>
  );
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
  const { confirm } = useFeedback();
  const own = view.configured[role] ?? {};
  const resolved = view.roles.find((r) => r.name === role) ?? view.roles[0];
  const [form, setForm] = useState<RoleSettings>({
    provider: own.provider ?? "",
    model: own.model ?? "",
    effort: own.effort ?? "",
    temperature: own.temperature ?? null,
  });
  const provider = form.provider || resolved?.provider;
  const kind = view.providers.find((p) => p.name === provider)?.kind;
  const models = useModels(provider);
  const catalog = catalogOf(models.data);
  // El modelo que se usará de verdad si se deja vacío.
  // Lo que usará de verdad, con los cambios aún sin guardar (igual que el servidor).
  const target = resolveRole(view, role, form);
  const effective = target.model;

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["providers"] });
    qc.invalidateQueries({ queryKey: ["setup"] });
  };
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
      refresh();
      onClose();
    },
  });
  // «Todos» → aplicar a todos: los roles dejan de tener ajustes propios y heredan.
  const applyAll = useMutation({
    mutationFn: async () => {
      const reset = Object.fromEntries(
        view.roles.map((r) => [r.name, { provider: null, model: null, effort: null, temperature: null }]),
      );
      await api.put<ConfigView>("/config", {
        roles: {
          ...reset,
          default: { provider: form.provider || null, model: form.model || null, effort: form.effort || null, temperature: form.temperature ?? null },
        },
      });
    },
    onSuccess: () => {
      refresh();
      onClose();
    },
  });
  const test = useMutation({
    mutationFn: () => api.post<{ ok: boolean; detail: string }>(`/providers/${target.provider}/test`, { model: effective }),
  });
  // Al cambiar de proveedor, el modelo anterior ya no aplica.
  useEffect(() => {
    if (form.provider !== (own.provider ?? "")) setForm((f) => ({ ...f, model: "" }));
    test.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.provider]);

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
          <option value="">{role === "default" ? "Automático (según tus claves)" : `Igual que «Todos» (${providerLabel(resolved?.provider ?? "")})`}</option>
          {view.providers.map((p) => (
            <option key={p.name} value={p.name}>
              {providerLabel(p.name)}
              {p.key_present === false ? " (falta la clave)" : ""}
            </option>
          ))}
        </select>
      </label>
      <div className="grid gap-1 text-sm">
        <span className="text-muted">Modelo</span>
        <ModelPicker
          key={provider}
          catalog={catalog}
          value={form.model ?? ""}
          placeholder={`Sin cambiar: ${resolveRole(view, role, { ...form, model: "" }).model || "—"}`}
          onChange={(model) => {
            setForm({ ...form, model });
            test.reset();
          }}
        />
        {models.isError && <span className="text-xs text-muted">No pude listar los modelos (¿falta la clave o está cerrado Ollama?).</span>}
      </div>
      {supportsEffort(kind) ? (
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
          {kind === "ollama" && <span className="text-xs text-muted">Solo en modelos que razonan (gpt-oss, deepseek, qwen3…).</span>}
        </label>
      ) : null}
      {kind !== "anthropic" && (
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
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={save.isPending}>
          Guardar
        </Button>
        <Button type="button" variant="secondary" disabled={!effective || test.isPending} onClick={() => test.mutate()}>
          {test.isPending ? "Probando…" : "Probar"}
        </Button>
        {role === "default" && view.roles.some((r) => Object.keys(view.configured[r.name] ?? {}).length > 0) && (
          <Button
            type="button"
            variant="secondary"
            disabled={applyAll.isPending}
            onClick={async () => {
              const ok = await confirm({
                title: "¿Usar esto en todos los roles?",
                body: "Los roles que tenían su propio modelo volverán a usar el de «Todos».",
                confirm: "Aplicar a todos",
              });
              if (ok) applyAll.mutate();
            }}
          >
            Aplicar a todos los roles
          </Button>
        )}
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancelar
        </Button>
      </div>
      {test.data && (
        <p className={cx("text-sm", test.data.ok ? "text-good" : "text-bad")}>
          {test.data.ok ? "✓ " : "✗ "}
          {effective} · {test.data.detail}
        </p>
      )}
      <ErrorNote error={save.error ?? applyAll.error ?? test.error} />
    </form>
  );
}

/** ¿Tiene el rol ajustes propios (o hereda todo de «Todos»)? */
export function hasOwnSettings(settings: RoleSettings | undefined): boolean {
  return !!settings && Object.values(settings).some((v) => v !== null && v !== undefined && v !== "");
}

export default function RolesCard() {
  const qc = useQueryClient();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  const setup = useSetup();
  const [open, setOpen] = useState<string | null>(null);
  const view = providers.data;
  const local = setup.data?.local ?? false;
  const def = view?.configured.default;
  const reset = useMutation({
    mutationFn: (role: string) =>
      api.put<ConfigView>("/config", { roles: { [role]: { provider: null, model: null, effort: null, temperature: null } } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["providers"] }),
  });

  return (
    <Card>
      <SectionTitle>Modelos del panel</SectionTitle>
      <p className="mb-2 text-sm text-muted">
        Cada rol del debate puede usar un proveedor y un modelo distinto. Lo que no cambies usa el de «Todos» (si un rol
        cambia de proveedor, usa el modelo recomendado de ese proveedor).
        {!local && ` ${LOCAL_ONLY_NOTE}`}
      </p>
      {view && (
        <ul className="divide-y divide-line text-sm">
          {["default", ...view.roles.map((r) => r.name)].map((role) => {
            const r = role === "default" ? null : view.roles.find((x) => x.name === role);
            const mine = role !== "default" && hasOwnSettings(view.configured[role]);
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
                  {mine && <Badge tone="accent">propio</Badge>}
                  <span className="ml-auto min-w-0 truncate text-muted" title={summary}>
                    {summary}
                  </span>
                  {local && open !== role && (
                    <>
                      {mine && (
                        <Button
                          variant="ghost"
                          className="-my-2 shrink-0 px-2"
                          title="Volver a usar el de «Todos»"
                          disabled={reset.isPending}
                          onClick={() => reset.mutate(role)}
                        >
                          Restablecer
                        </Button>
                      )}
                      <Button variant="ghost" className="-my-2 shrink-0 px-2" onClick={() => setOpen(role)}>
                        Cambiar
                      </Button>
                    </>
                  )}
                </div>
                {open === role && <RoleEditor role={role} view={view} onClose={() => setOpen(null)} />}
              </li>
            );
          })}
        </ul>
      )}
      <ErrorNote error={providers.error ?? reset.error} />
    </Card>
  );
}
