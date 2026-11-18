import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type CommitteeView } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, Notice, SectionTitle, Skeleton, cx } from "./ui";

type Mode = CommitteeView["mode"];

const MODES: { value: Mode; label: string; hint: string }[] = [
  { value: "auto", label: "Recomendado", hint: "Oraqlo elige modelos de familias distintas del catálogo." },
  { value: "custom", label: "Elegir", hint: "Tú decides qué modelos acompañan al Oráculo." },
  { value: "off", label: "Sin comité", hint: "Solo el modelo del Oráculo (más rápido y barato)." },
];

const gb = (size: number | null) => (size ? `${Math.round(size / 1e9)} GB` : null);

/** Varias IA predicen cada pregunta y Oraqlo las combina según lo que acierta cada una. */
export default function CommitteeCard() {
  const qc = useQueryClient();
  const setup = useSetup();
  const local = !!setup.data?.local;
  const view = useQuery({ queryKey: ["committee"], queryFn: () => api.get<CommitteeView>("/committee"), retry: false });
  const [mode, setMode] = useState<Mode>("auto");
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => {
    if (!view.data) return;
    setMode(view.data.mode);
    setPicked(view.data.mode === "custom" ? view.data.models : view.data.recommended);
  }, [view.data]);
  const save = useMutation({
    mutationFn: () => api.put<CommitteeView>("/committee", { mode, models: mode === "custom" ? picked : [] }),
    onSuccess: (data) => qc.setQueryData(["committee"], data),
  });

  const d = view.data;
  // Los del comité actual y los recomendados, arriba (sin saltos al marcar); el resto por nombre.
  const rank = (name: string) => (d?.models.includes(name) ? 0 : d?.recommended.includes(name) ? 1 : 2);
  const sorted = [...(d?.catalog ?? [])].sort((x, y) => rank(x.name) - rank(y.name) || x.name.localeCompare(y.name));
  const dirty = !!d && (mode !== d.mode || (mode === "custom" && picked.join("|") !== d.models.join("|")));
  const toggle = (name: string) =>
    setPicked((cur) => (cur.includes(name) ? cur.filter((m) => m !== name) : cur.length < 8 ? [...cur, name] : cur));

  return (
    <Card id="comite">
      <SectionTitle>Comité de predicción</SectionTitle>
      <p className="mb-3 text-sm text-muted">
        Para cada pregunta, varias IA de familias distintas estiman por su cuenta, cada una con un enfoque (tasa base, datos del
        caso, abogado del diablo, ritmo en el tiempo). Oraqlo las combina y da más voz a la que más acierta en la Práctica. Más
        llamadas, pero predicciones más fiables.
      </p>
      {view.isLoading ? (
        <Skeleton className="h-24" />
      ) : !d ? (
        <ErrorNote error={view.error} onRetry={() => view.refetch()} />
      ) : !d.available ? (
        <Notice tone="warn">
          El comité usa modelos de la nube de Ollama: inicia sesión en la app de Ollama de este PC (y usa un modelo «…:cloud» para el
          Oráculo) o pega tu clave de ollama.com en «Búsqueda web».
        </Notice>
      ) : (
        <>
          <p className="mb-3 text-sm">
            Ahora:{" "}
            {d.models.length ? (
              <>
                <b>{d.lead}</b> + {d.models.join(", ")}
              </>
            ) : (
              <>
                solo <b>{d.lead}</b>
              </>
            )}
          </p>
          <div className="mb-3 grid grid-cols-3 gap-2" role="radiogroup" aria-label="Comité">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                disabled={!local}
                title={m.hint}
                onClick={() => setMode(m.value)}
                className={cx(
                  "min-h-11 rounded-xl border px-2 text-sm font-medium transition disabled:opacity-60",
                  mode === m.value ? "border-accent bg-accent/10 text-fg" : "border-line text-muted hover:text-fg",
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="mb-3 text-xs text-muted">{MODES.find((m) => m.value === mode)?.hint}</p>
          {mode === "custom" && (
            <ul className="mb-3 max-h-64 divide-y divide-line overflow-y-auto rounded-xl border border-line">
              {sorted.map((m) => (
                <li key={m.name}>
                  <label className="flex min-h-10 cursor-pointer items-center gap-3 px-3 text-sm">
                    <input
                      type="checkbox"
                      checked={picked.includes(m.name)}
                      disabled={!local}
                      onChange={() => toggle(m.name)}
                    />
                    <span className="min-w-0 flex-1 truncate">{m.name}</span>
                    {d.recommended.includes(m.name) && <Badge tone="good">recomendado</Badge>}
                    {gb(m.size) && <span className="text-xs text-muted">{gb(m.size)}</span>}
                  </label>
                </li>
              ))}
            </ul>
          )}
          {local ? (
            <Button disabled={!dirty || save.isPending || (mode === "custom" && !picked.length)} onClick={() => save.mutate()}>
              {save.isPending ? "Guardando…" : "Guardar"}
            </Button>
          ) : (
            <p className="text-xs text-muted">{LOCAL_ONLY_NOTE}</p>
          )}
          <ErrorNote error={save.error} />
        </>
      )}
    </Card>
  );
}
