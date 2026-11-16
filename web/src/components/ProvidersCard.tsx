import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, PROVIDER_INFO, providerLabel, type Check, type ProviderEntry, type ProvidersView, type SetupStatus } from "../lib/api";
import { Badge, Button, Card, ErrorNote, SectionTitle, cx, inputClass } from "./ui";

export const LOCAL_ONLY_NOTE = "Solo se puede cambiar desde el PC donde corre Oraqlo.";

export function useSetup() {
  return useQuery({ queryKey: ["setup"], queryFn: () => api.get<SetupStatus>("/setup") });
}

/** Guarda la clave de un proveedor (y la prueba). Se reutiliza en la bienvenida. */
export function useKeyActions(onDone?: (check: Check) => void) {
  const qc = useQueryClient();
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["providers"] });
    qc.invalidateQueries({ queryKey: ["setup"] });
  };
  const save = useMutation({
    mutationFn: async ({ provider, value }: { provider: ProviderEntry; value: string }) => {
      await api.put<ProvidersView>(`/keys/${provider.api_key_env}`, { value });
      return api.post<Check>(`/providers/${provider.name}/test`);
    },
    onSuccess: (check) => {
      refresh();
      onDone?.(check);
    },
  });
  const remove = useMutation({
    mutationFn: (provider: ProviderEntry) => api.del(`/keys/${provider.api_key_env}`),
    onSuccess: refresh,
  });
  const test = useMutation({ mutationFn: (name: string) => api.post<Check>(`/providers/${name}/test`) });
  return { save, remove, test };
}

function KeyRow({ provider, used, local }: { provider: ProviderEntry; used: boolean; local: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [result, setResult] = useState<Check | null>(null);
  const { save, remove, test } = useKeyActions((check) => {
    setResult(check);
    if (check.ok) {
      setEditing(false);
      setValue("");
    }
  });
  const info = PROVIDER_INFO[provider.name];
  const needsKey = provider.api_key_env !== null;
  const shown = test.data ?? result;

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{providerLabel(provider.name)}</span>
        {used && <Badge tone="accent">en uso</Badge>}
        <span className="ml-auto">
          {!needsKey ? (
            <Badge>sin clave</Badge>
          ) : provider.key_present ? (
            <Badge tone="good">clave {provider.key_hint}</Badge>
          ) : (
            <Badge tone={used ? "bad" : "muted"}>falta la clave</Badge>
          )}
        </span>
      </div>
      {info?.note && <p className="mt-0.5 text-xs text-muted">{info.note}</p>}

      {editing ? (
        <form
          className="mt-2 flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) save.mutate({ provider, value });
          }}
        >
          <input
            className={cx(inputClass, "min-w-0 flex-1")}
            type="password"
            autoComplete="off"
            placeholder="Pega aquí tu clave"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label={`Clave de ${providerLabel(provider.name)}`}
            autoFocus
          />
          <Button type="submit" disabled={!value.trim() || save.isPending}>
            {save.isPending ? "Probando…" : "Guardar"}
          </Button>
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
            Cancelar
          </Button>
          {info?.keyUrl && (
            <a className="w-full text-xs text-accent underline" href={info.keyUrl} target="_blank" rel="noreferrer">
              ¿No tienes clave? Consíguela aquí
            </a>
          )}
        </form>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          {needsKey && (
            <Button
              variant="secondary"
              disabled={!local}
              title={local ? undefined : LOCAL_ONLY_NOTE}
              onClick={() => {
                setResult(null);
                setEditing(true);
              }}
            >
              {provider.key_present ? "Cambiar clave" : "Añadir clave"}
            </Button>
          )}
          {(provider.key_present || !needsKey) && (
            <Button variant="secondary" disabled={test.isPending} onClick={() => test.mutate(provider.name)}>
              {test.isPending ? "Probando…" : "Probar"}
            </Button>
          )}
          {needsKey && provider.key_present && local && (
            <Button variant="ghost" disabled={remove.isPending} onClick={() => remove.mutate(provider)}>
              Quitar
            </Button>
          )}
        </div>
      )}
      {shown && (
        <p className={cx("mt-2 text-sm", shown.ok ? "text-good" : "text-bad")}>
          {shown.ok ? "✓ " : "✗ "}
          {shown.detail} {shown.fix}
        </p>
      )}
      <ErrorNote error={save.error ?? remove.error ?? test.error} />
    </li>
  );
}

export default function ProvidersCard() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  const setup = useSetup();
  const [all, setAll] = useState(false);
  const used = new Set(providers.data?.roles.map((r) => r.provider));
  const local = setup.data?.local ?? false;
  const list = (providers.data?.providers ?? [])
    .filter((p) => all || used.has(p.name) || p.key_present || ["anthropic", "openai", "gemini"].includes(p.name))
    .sort((a, b) => Number(used.has(b.name)) - Number(used.has(a.name)));

  return (
    <Card>
      <SectionTitle
        aside={
          <Button variant="ghost" className="min-h-0 py-1" onClick={() => setAll(!all)}>
            {all ? "Ver menos" : "Ver todos"}
          </Button>
        }
      >
        Proveedores y claves
      </SectionTitle>
      <p className="text-sm text-muted">
        Las claves se guardan solo en este PC y nunca se vuelven a mostrar completas.
        {!local && ` ${LOCAL_ONLY_NOTE}`}
      </p>
      <ul className="divide-y divide-line text-sm">
        {list.map((p) => (
          <KeyRow key={p.name} provider={p} used={used.has(p.name)} local={local} />
        ))}
      </ul>
      <ErrorNote error={providers.error} />
    </Card>
  );
}
