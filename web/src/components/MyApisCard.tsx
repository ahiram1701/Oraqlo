import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Check, type CustomProvider } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, Notice, SectionTitle, Skeleton, cx, inputClass, useFeedback } from "./ui";

const TYPES: { value: CustomProvider["type"]; label: string; hint: string }[] = [
  { value: "openai", label: "Compatible con OpenAI", hint: "La mayoría: vLLM, LM Studio, Together, Fireworks, un servidor propio…" },
  { value: "ollama", label: "Ollama", hint: "Un Ollama en otro equipo de tu red." },
  { value: "anthropic", label: "Anthropic", hint: "Otra cuenta de Claude con su propia clave." },
];

type Draft = { name: string; type: CustomProvider["type"]; base_url: string; key: string };
const EMPTY: Draft = { name: "", type: "openai", base_url: "", key: "" };

/** APIs propias: se crean, prueban y quitan aquí, sin tocar ningún archivo. */
export default function MyApisCard() {
  const qc = useQueryClient();
  const setup = useSetup();
  const local = !!setup.data?.local;
  const { confirm } = useFeedback();
  const list = useQuery({ queryKey: ["custom-providers"], queryFn: () => api.get<CustomProvider[]>("/providers/custom") });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [editingExisting, setEditingExisting] = useState(false);
  const [check, setCheck] = useState<Check | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["custom-providers"] });
    qc.invalidateQueries({ queryKey: ["providers"] });
  };
  const save = useMutation({
    mutationFn: (d: Draft) =>
      api.put<{ providers: CustomProvider[]; check: Check }>(`/providers/custom/${d.name.trim()}`, {
        type: d.type,
        base_url: d.base_url.trim() || null,
        // Al editar, un campo vacío conserva la clave; al crear, vacío = sin clave.
        key: d.key.trim() ? d.key.trim() : editingExisting ? null : "",
      }),
    onSuccess: (out) => {
      setCheck(out.check);
      refresh();
      if (out.check.ok) setDraft(null);
    },
  });
  const remove = useMutation({ mutationFn: (name: string) => api.del(`/providers/custom/${name}`), onSuccess: refresh });
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...(d ?? EMPTY), ...patch }));

  return (
    <Card id="mis-apis">
      <SectionTitle>Mis APIs</SectionTitle>
      <p className="mb-3 text-sm text-muted">
        ¿Usas un servidor propio u otro proveedor? Añádelo aquí con su dirección y su clave. Después lo puedes elegir en «Modelos del
        panel».
      </p>
      {list.isLoading ? (
        <Skeleton className="h-12" />
      ) : (
        <ErrorNote error={list.error} onRetry={() => list.refetch()} />
      )}
      {list.data && list.data.length > 0 && (
        <ul className="mb-3 divide-y divide-line">
          {list.data.map((p) => (
            <li key={p.name} className="flex flex-wrap items-center gap-2 py-2 text-sm">
              <span className="font-medium">{p.name}</span>
              <span className="text-xs text-muted">{TYPES.find((t) => t.value === p.type)?.label}</span>
              {p.used_by.length > 0 && <Badge tone="accent">en uso</Badge>}
              <span className="ml-auto">{p.key_present ? <Badge tone="good">clave {p.key_hint}</Badge> : <Badge>sin clave</Badge>}</span>
              {p.base_url && <span className="w-full truncate text-xs text-muted">{p.base_url}</span>}
              {local && (
                <span className="flex w-full gap-2">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setCheck(null);
                      setEditingExisting(true);
                      setDraft({ name: p.name, type: p.type, base_url: p.base_url ?? "", key: "" });
                    }}
                  >
                    Editar
                  </Button>
                  <Button
                    variant="ghost"
                    className="hover:text-bad"
                    onClick={async () => {
                      if (
                        await confirm({
                          title: `¿Quitar «${p.name}»?`,
                          body: "Se borran su dirección y su clave.",
                          confirm: "Quitar",
                          danger: true,
                        })
                      )
                        remove.mutate(p.name);
                    }}
                  >
                    Quitar
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={remove.error} />
      {!local ? (
        <Notice tone="info">{LOCAL_ONLY_NOTE}</Notice>
      ) : draft ? (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (draft.name.trim()) save.mutate(draft);
          }}
        >
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Nombre</span>
            <input
              className={inputClass}
              value={draft.name}
              disabled={editingExisting}
              placeholder="mi-servidor"
              onChange={(e) => set({ name: e.target.value.toLowerCase() })}
            />
            <span className="text-xs text-muted">Minúsculas, números y guiones.</span>
          </label>
          <fieldset className="space-y-1 text-sm">
            <legend className="mb-1 font-medium">Tipo</legend>
            {TYPES.map((t) => (
              <label key={t.value} className="flex items-start gap-2">
                <input type="radio" name="tipo" checked={draft.type === t.value} onChange={() => set({ type: t.value })} />
                <span>
                  {t.label} <span className="text-xs text-muted">· {t.hint}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {draft.type !== "anthropic" && (
            <label className="block text-sm">
              <span className="mb-1 block font-medium">Dirección (URL)</span>
              <input
                className={inputClass}
                value={draft.base_url}
                placeholder={draft.type === "ollama" ? "http://192.168.1.20:11434" : "https://mi-servidor.com/v1"}
                onChange={(e) => set({ base_url: e.target.value })}
              />
            </label>
          )}
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Clave</span>
            <input
              className={cx(inputClass)}
              type="password"
              autoComplete="off"
              value={draft.key}
              placeholder={editingExisting ? "Déjalo vacío para conservar la que hay" : "Opcional si no la pide"}
              onChange={(e) => set({ key: e.target.value })}
            />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={!draft.name.trim() || save.isPending}>
              {save.isPending ? "Probando…" : "Guardar y probar"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setDraft(null)}>
              Cancelar
            </Button>
          </div>
        </form>
      ) : (
        <Button
          variant="secondary"
          onClick={() => {
            setCheck(null);
            setEditingExisting(false);
            setDraft(EMPTY);
          }}
        >
          Añadir una API
        </Button>
      )}
      {check && (
        <Notice tone={check.ok ? "good" : "warn"} className="mt-3">
          {check.ok ? "Funciona. " : ""}
          {check.detail}
          {!check.ok && check.fix ? ` ${check.fix}` : ""}
        </Notice>
      )}
      <ErrorNote error={save.error} />
    </Card>
  );
}
