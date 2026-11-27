import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "../components/icons";
import { SignalList, Sparkline } from "../components/Insights";
import { ChipGroup, FilteredEmpty, SearchInput, SortSelect, countLabel, includesText, type Option } from "../components/listControls";
import { Badge, Button, Card, Empty, ErrorNote, SectionTitle, Skeleton, Toggle, cx, inputClass, useFeedback } from "../components/ui";
import {
  FORECAST_KIND_LABEL,
  api,
  displayQuestion,
  fmtDate,
  forecastKind,
  parseDate,
  pct,
  sourceLabel,
  type Forecast,
  type ForecastKind,
  type ResolvedForecast,
  type WatchView,
} from "../lib/api";
import { useStoredState } from "../lib/useStoredState";

/**
 * El Brier relativo al azar de esa pregunta (repartir por igual entre sus `k` opciones
 * saca 1 − 1/k). Así un 0.6 no es «fallo» en una pregunta de 4 opciones, donde el azar
 * saca 0.75. Con sí/no las bandas son las de siempre: 0.1, 0.25 y 0.5.
 */
function vsChance(brier: number, k: number) {
  return brier / (1 - 1 / Math.max(k, 2));
}

/** El acierto en palabras: el color solo no basta. */
function brierWords(b: number, k = 2) {
  const x = vsChance(b, k);
  return x < 0.2 ? "acierto muy bueno" : x < 0.5 ? "acierto bueno" : x < 1 ? "acierto flojo" : "fallo";
}

/**
 * Resoluciones esperando los 5 s de «Deshacer». Viven fuera de la fila: si la fila
 * desaparece (cambiar de página, filtrar con la búsqueda), el envío sigue en pie.
 * Antes el temporizador se cancelaba al desmontar y la resolución se perdía.
 */
const pendingResolutions = new Map<string, { outcome: string; timer: ReturnType<typeof setTimeout> }>();
export const UNDO_MS = 5000;
const RESOLUTION_FAILED = "oraqlo:resolution-failed";

function postResolution(id: string, outcome: string, keepalive = false) {
  return fetch(`/api/ledger/${id}/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outcome }),
    keepalive, // al cerrar la app, el envío sobrevive a la página
  });
}

/** Cerrar la ventana o la pestaña durante el plazo: se envía ya, sin esperar. */
function flushPendingResolutions() {
  for (const [id, { outcome, timer }] of pendingResolutions) {
    clearTimeout(timer);
    void postResolution(id, outcome, true).catch(() => undefined);
  }
  pendingResolutions.clear();
}
if (typeof window !== "undefined") window.addEventListener("pagehide", flushPendingResolutions);

export function scheduleResolution(id: string, outcome: string, send: () => void) {
  cancelResolution(id);
  const timer = setTimeout(() => {
    pendingResolutions.delete(id);
    send();
  }, UNDO_MS);
  pendingResolutions.set(id, { outcome, timer });
}

export function cancelResolution(id: string) {
  const entry = pendingResolutions.get(id);
  if (entry) clearTimeout(entry.timer);
  pendingResolutions.delete(id);
}

/** Cuándo se sabrá (ms): creación + horizonte. Sin fecha, al final. */
export function dueTime(f: Forecast): number {
  return f.created_at ? parseDate(f.created_at).getTime() + f.horizon_days * 864e5 : Number.MAX_SAFE_INTEGER;
}

/** Ya debería saberse y le toca al usuario marcarla (la Práctica la resuelve Polymarket). */
export function overdueForUser(f: Forecast, now = Date.now()): boolean {
  return dueTime(f) <= now && forecastKind(f.source, f.question) !== "practica";
}

/** Qué le falta a Polymarket para resolver una de Práctica ya vencida. */
export function marketStatusLabel(status: string | null | undefined): string {
  if (status === "proposed") return "resultado propuesto, en revisión";
  if (status === "disputed") return "resultado impugnado: puede tardar días";
  if (status === "abierto") return "Polymarket aún la cotiza: sus reglas la resuelven después de esta fecha";
  return "esperando a que Polymarket la resuelva";
}

const topProb = (f: Forecast) => Math.max(0, ...Object.values(f.outcomes ?? {}));
const created = (f: Forecast) => (f.created_at ? parseDate(f.created_at).getTime() : 0);

/** «tuyas»: todo menos la Práctica, que se resuelve sola con el mercado. */
type KindFilter = "tuyas" | "todas" | ForecastKind;

export function kindMatches(filter: KindFilter, kind: ForecastKind): boolean {
  if (filter === "todas") return true;
  if (filter === "tuyas") return kind !== "practica";
  return kind === filter;
}
type OpenState = "todas" | "vencidas" | "por-venir";
type OpenSort = "se-sabra" | "recientes" | "antiguas" | "probable";
type Accuracy = "todas" | "bueno" | "flojo" | "fallo";
type ResolvedSort = "resueltas" | "mejor" | "peor" | "creadas";

const OPEN_SORTS: Option<OpenSort>[] = [
  { value: "se-sabra", label: "Cuándo se sabrá" },
  { value: "recientes", label: "Más recientes" },
  { value: "antiguas", label: "Más antiguas" },
  { value: "probable", label: "Más seguras primero" },
];
const RESOLVED_SORTS: Option<ResolvedSort>[] = [
  { value: "resueltas", label: "Resueltas hace poco" },
  { value: "mejor", label: "Mejor acierto" },
  { value: "peor", label: "Peor acierto" },
  { value: "creadas", label: "Creadas hace poco" },
];

/** Bandas de acierto: las mismas palabras que en cada fila («muy bueno» cuenta como bueno). */
export function accuracyBand(brier: number, k = 2): Exclude<Accuracy, "todas"> {
  const x = vsChance(brier, k);
  return x < 0.5 ? "bueno" : x < 1 ? "flojo" : "fallo";
}

export function sortOpen(list: Forecast[], by: OpenSort): Forecast[] {
  const out = [...list];
  if (by === "se-sabra") out.sort((a, b) => dueTime(a) - dueTime(b));
  if (by === "recientes") out.sort((a, b) => created(b) - created(a));
  if (by === "antiguas") out.sort((a, b) => created(a) - created(b));
  if (by === "probable") out.sort((a, b) => topProb(b) - topProb(a));
  return out;
}

export function sortResolved(list: ResolvedForecast[], by: ResolvedSort): ResolvedForecast[] {
  const out = [...list];
  const resolved = (r: ResolvedForecast) => parseDate(r.resolved_at).getTime();
  if (by === "resueltas") out.sort((a, b) => resolved(b) - resolved(a));
  if (by === "mejor") out.sort((a, b) => a.brier - b.brier);
  if (by === "peor") out.sort((a, b) => b.brier - a.brier);
  if (by === "creadas") out.sort((a, b) => created(b) - created(a));
  return out;
}

function sortedOutcomes(outcomes: Record<string, number>) {
  return Object.entries(outcomes).sort((a, b) => b[1] - a[1]);
}

export default function Ledger() {
  const { data, error, isLoading } = useQuery({
    queryKey: ["ledger"],
    queryFn: () => api.get<{ open: Forecast[]; resolved: ResolvedForecast[]; overdue?: number }>("/ledger"),
  });
  const [query, setQuery] = useState("");
  // Clave nueva: antes se guardaba «todas» y la Práctica llenaba la lista de cosas que no hay que tocar.
  const [kind, setKind] = useStoredState<KindFilter>("oraqlo.ledger.kind.v2", "tuyas");
  const [state, setState] = useStoredState<OpenState>("oraqlo.ledger.state", "todas");
  const [openSort, setOpenSort] = useStoredState<OpenSort>("oraqlo.ledger.openSort", "se-sabra");
  const [accuracy, setAccuracy] = useStoredState<Accuracy>("oraqlo.ledger.accuracy", "todas");
  const [resolvedSort, setResolvedSort] = useStoredState<ResolvedSort>("oraqlo.ledger.resolvedSort", "resueltas");

  const allOpen = data?.open ?? [];
  const allResolved = data?.resolved ?? [];
  const total = allOpen.length + allResolved.length;
  const now = Date.now();
  const kindOk = (f: Forecast) => kindMatches(kind, forecastKind(f.source, f.question));

  const open = useMemo(() => {
    const filtered = allOpen.filter((f) => {
      const overdue = overdueForUser(f, now);
      return (
        kindOk(f) &&
        includesText(f.question, query) &&
        (state === "todas" || (state === "vencidas" ? overdue : !overdue))
      );
    });
    return sortOpen(filtered, openSort);
  }, [data, query, kind, state, openSort]);
  const resolved = useMemo(
    () =>
      sortResolved(
        allResolved.filter(
          (r) => kindOk(r) && includesText(r.question, query) && (accuracy === "todas" || accuracyBand(r.brier, Object.keys(r.outcomes).length) === accuracy),
        ),
        resolvedSort,
      ),
    [data, query, kind, accuracy, resolvedSort],
  );

  // Solo los tipos que existen: no ofrecer filtros que dejan la lista vacía.
  const kindOptions = useMemo(() => {
    const counts = new Map<ForecastKind, number>();
    for (const f of [...allOpen, ...allResolved]) {
      const k = forecastKind(f.source, f.question);
      counts.set(k, (counts.get(k) ?? 0) + 1);
    }
    const mine = [...counts].filter(([k]) => k !== "practica").reduce((n, [, c]) => n + c, 0);
    const opts: Option<KindFilter>[] = [{ value: "tuyas", label: "Tuyas", count: mine }];
    for (const k of ["prevision", "objetivo", "panel", "practica"] as const)
      if (counts.get(k)) opts.push({ value: k, label: FORECAST_KIND_LABEL[k], count: counts.get(k) });
    opts.push({ value: "todas", label: "Todas" });
    return opts;
  }, [data]);

  const clearOpen = () => {
    setQuery("");
    setKind("tuyas");
    setState("todas");
  };
  const clearResolved = () => {
    setQuery("");
    setKind("tuyas");
    setAccuracy("todas");
  };

  return (
    <div className="space-y-4">
      <ErrorNote error={error} />
      {total > 0 && (
        <div className="space-y-2">
          <SearchInput value={query} onChange={setQuery} label="Buscar predicciones" />
          {kindOptions.length > 2 && <ChipGroup label="Tipo" options={kindOptions} value={kind} onChange={setKind} />}
          {kind === "practica" && (
            <p className="text-xs text-muted">
              Las de Práctica se resuelven solas cuando cierra el mercado: no tienes que marcarlas.
            </p>
          )}
        </div>
      )}
      <Card>
        <SectionTitle aside={<Badge>{countLabel(open.length, allOpen.length)}</Badge>}>Pendientes</SectionTitle>
        <p className="-mt-1 mb-3 text-xs text-muted">
          {data?.overdue
            ? `${data.overdue} ya deberían saberse. Toca una y marca qué pasó: así Oraqlo aprende.`
            : "Toca una cuando sepas qué pasó."}
        </p>
        {allOpen.length > 1 && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <ChipGroup
              label="Estado"
              options={[
                { value: "todas", label: "Todas" },
                { value: "vencidas", label: "Ya deberían saberse" },
                { value: "por-venir", label: "Por venir" },
              ]}
              value={state}
              onChange={setState}
            />
            <SortSelect options={OPEN_SORTS} value={openSort} onChange={setOpenSort} />
          </div>
        )}
        {isLoading && <Skeleton className="h-16" />}
        {data && allOpen.length === 0 && (
          <Empty>
            No hay predicciones pendientes. Cada previsión que hagas en{" "}
            <Link to="/" className="text-accent underline">
              Preguntar
            </Link>{" "}
            queda aquí hasta que sepas qué pasó.
          </Empty>
        )}
        {allOpen.length > 0 && open.length === 0 && <FilteredEmpty onClear={clearOpen} />}
        <div className="space-y-3">
          {open.map((f) => (
            <OpenForecast key={f.id} f={f} />
          ))}
        </div>
      </Card>
      <Card>
        <SectionTitle aside={<Badge>{countLabel(resolved.length, allResolved.length)}</Badge>}>Resueltas</SectionTitle>
        {total > 0 && <ExportLink className="-mt-1 mb-3" />}
        {allResolved.length > 1 && (
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <ChipGroup
              label="Acierto"
              options={[
                { value: "todas", label: "Todas" },
                { value: "bueno", label: "Buen acierto" },
                { value: "flojo", label: "Flojo" },
                { value: "fallo", label: "Fallos" },
              ]}
              value={accuracy}
              onChange={setAccuracy}
            />
            <SortSelect options={RESOLVED_SORTS} value={resolvedSort} onChange={setResolvedSort} />
          </div>
        )}
        {data && allResolved.length === 0 && (
          <Empty>
            Cuando marques cómo acabó una predicción, aparecerá aquí con su puntuación (Brier: 0 es perfecto; menos es mejor). Así
            Oraqlo aprende.
          </Empty>
        )}
        {allResolved.length > 0 && resolved.length === 0 && <FilteredEmpty onClear={clearResolved} />}
        <ul className="divide-y divide-line">
          {resolved.map((r) => (
            <ResolvedRow key={r.id} r={r} />
          ))}
        </ul>
      </Card>
    </div>
  );
}

export function OpenForecast({ f }: { f: Forecast }) {
  const qc = useQueryClient();
  const { confirm, toast } = useFeedback();
  const [open, setOpen] = useState(false);
  const [other, setOther] = useState("");
  // Si se volvió a la página durante el plazo, la fila sigue marcada.
  const [pendingOutcome, setPendingOutcome] = useState<string | null>(() => pendingResolutions.get(f.id)?.outcome ?? null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["ledger"] });
    qc.invalidateQueries({ queryKey: ["calibration"] });
    qc.invalidateQueries({ queryKey: ["evaluate"] });
  };
  const discard = useMutation({ mutationFn: () => api.del(`/ledger/open/${f.id}`), onSuccess: refresh });
  useEffect(() => {
    const onFail = (e: Event) => (e as CustomEvent<string>).detail === f.id && setPendingOutcome(null);
    window.addEventListener(RESOLUTION_FAILED, onFail);
    return () => window.removeEventListener(RESOLUTION_FAILED, onFail);
  }, [f.id]);

  /** Resolver afecta a la calibración: 5 s para deshacer antes de enviarlo. */
  function choose(outcome: string) {
    setPendingOutcome(outcome);
    // Fuera del ciclo de vida de la fila (sin setState aquí: puede ya no existir).
    scheduleResolution(f.id, outcome, () => {
      api
        .post(`/ledger/${f.id}/resolve`, { outcome })
        .then(refresh)
        .catch((e: unknown) => {
          window.dispatchEvent(new CustomEvent(RESOLUTION_FAILED, { detail: f.id }));
          refresh();
          toast(`No se pudo guardar «${outcome}»: ${e instanceof Error ? e.message : "error"}`);
        });
    });
    toast(`Marcada: «${outcome}»`, {
      undo: () => {
        cancelResolution(f.id);
        setPendingOutcome(null);
      },
    });
  }

  const outcomes = sortedOutcomes(f.outcomes);
  const top = outcomes[0];
  const due = f.created_at ? new Date(parseDate(f.created_at).getTime() + f.horizon_days * 864e5) : null;
  const practice = forecastKind(f.source, f.question) === "practica";
  const pastDue = due ? due < new Date() : false;
  const overdue = pastDue && !practice;

  if (pendingOutcome)
    return (
      <div className="rounded-xl border border-dashed border-line p-3 text-sm text-muted" aria-live="polite">
        «{displayQuestion(f.question)}» → <b className="text-fg">{pendingOutcome}</b>
      </div>
    );

  return (
    <div className="rounded-xl border border-line bg-surface-2">
      <button type="button" aria-expanded={open} className="w-full p-3 text-left" onClick={() => setOpen(!open)}>
        <p className="text-sm font-medium">{displayQuestion(f.question)}</p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
          <span>{top ? `Previó: ${top[0]} (${pct(top[1])})` : "Intervalo numérico"}</span>
          <span aria-hidden>·</span>
          <span>{sourceLabel(f.source)}</span>
          <span aria-hidden>·</span>
          <span>se sabrá el {due?.toLocaleDateString("es", { day: "numeric", month: "short" }) ?? "—"}</span>
          {overdue && <Badge tone="warn">ya debería saberse</Badge>}
          {pastDue && practice && <Badge>{marketStatusLabel(f.market_status)}</Badge>}
          {f.watch?.enabled && <Badge>{f.watch.versions > 1 ? `vigilada · ${f.watch.versions} versiones` : "vigilada"}</Badge>}
        </p>
      </button>
      {open && (
        <div className="space-y-3 border-t border-line p-3">
          <p className="text-sm font-medium">¿Qué pasó?</p>
          <div className="flex flex-wrap gap-2">
            {outcomes.map(([label, p]) => (
              <Button key={label} variant="secondary" onClick={() => choose(label)}>
                {label} <span className="text-muted">{pct(p)}</span>
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (other.trim()) choose(other.trim());
            }}
          >
            <input
              className={inputClass}
              placeholder={
                f.numeric
                  ? `O escribe la cifra que salió${f.numeric.unit ? ` (${f.numeric.unit})` : ""}…`
                  : outcomes.length
                    ? "Otro resultado…"
                    : "Escribe el valor que salió…"
              }
              aria-label={f.numeric ? "Cifra que salió" : outcomes.length ? "Otro resultado" : "Valor que salió"}
              inputMode={f.numeric || !outcomes.length ? "decimal" : undefined}
              value={other}
              onChange={(e) => setOther(e.target.value)}
            />
            <Button type="submit" variant="secondary" disabled={!other.trim()}>
              Marcar
            </Button>
          </form>
          <Button
            variant="ghost"
            className="px-0 text-xs"
            onClick={async () => {
              if (
                await confirm({
                  title: "¿Descartar sin puntuar?",
                  body: "Se quita de la lista y no cuenta para la calibración.",
                  confirm: "Descartar",
                  danger: true,
                })
              )
                discard.mutate();
            }}
          >
            Descartar sin puntuar
          </Button>
          <ErrorNote error={discard.error} />
          <WatchPanel id={f.id} watch={f.watch ?? null} top={top?.[0]} />
        </div>
      )}
    </div>
  );
}

/** Vigilancia de una previsión: encenderla o apagarla, su historia y sus señales. */
export function WatchPanel({ id, watch, top }: { id: string; watch: WatchView | null; top?: string }) {
  const qc = useQueryClient();
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.put<WatchView>(`/ledger/${id}/watch`, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ledger"] }),
  });
  const last = watch?.history.at(-1);
  return (
    <div className="space-y-2 border-t border-line pt-3 text-sm">
      <Toggle
        checked={!!watch?.enabled}
        onChange={(v) => toggle.mutate(v)}
        label="Vigilar: revisar las noticias y avisarme si cambia"
      />
      <ErrorNote error={toggle.error} />
      {watch && watch.history.length > 1 && top && (
        <div className="flex flex-wrap items-center gap-3">
          <Sparkline watch={watch} label={top} />
          <span className="text-xs text-muted">
            «{top}»: {watch.history.map((h) => pct(h.outcomes[top] ?? 0)).join(" → ")}
          </span>
        </div>
      )}
      {last && watch && watch.history.length > 1 && <p className="text-xs text-muted">Último cambio: {last.why}</p>}
      {watch && watch.signals.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted">Qué vigila ({watch.signals.length})</summary>
          <SignalList watch={watch} />
        </details>
      )}
      {watch?.last_check && <p className="text-xs text-muted">Última revisión: {fmtDate(watch.last_check)}</p>}
    </div>
  );
}

function ResolvedRow({ r }: { r: ResolvedForecast }) {
  const qc = useQueryClient();
  const { confirm } = useFeedback();
  const [open, setOpen] = useState(false);
  const del = useMutation({
    mutationFn: () => api.del(`/ledger/resolved/${r.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ledger"] });
      qc.invalidateQueries({ queryKey: ["calibration"] });
      qc.invalidateQueries({ queryKey: ["evaluate"] });
    },
  });
  // Clases literales: Tailwind no detecta nombres construidos dinámicamente.
  const k = Object.keys(r.outcomes).length;
  const x = vsChance(r.brier, k);
  const toneClass = x < 0.2 ? "text-good" : x < 0.5 ? "text-accent" : x < 1 ? "text-warn" : "text-bad";
  return (
    <li className="py-2">
      <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex w-full items-start justify-between gap-3 py-1 text-left">
        <span className="min-w-0">
          <span className={cx("block text-sm", !open && "line-clamp-2")}>{displayQuestion(r.question)}</span>
          <span className="text-xs text-muted">
            Pasó: <b className="text-fg">{r.actual_outcome}</b> · le daba {pct(r.outcomes[r.actual_outcome] ?? 0)}
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end">
          <span className={cx("text-sm font-semibold tabular-nums", toneClass)}>{r.brier.toFixed(2)}</span>
          <span className="text-xs text-muted">{brierWords(r.brier, k)}</span>
        </span>
      </button>
      {open && (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
          <span>
            Resuelta el {fmtDate(r.resolved_at)} · {sourceLabel(r.source)}
          </span>
          <Button
            variant="ghost"
            className="text-xs hover:text-bad"
            onClick={async () => {
              if (
                await confirm({
                  title: "¿Borrar esta predicción resuelta?",
                  body: "Desaparece del historial de calibración y la fiabilidad se recalcula sin ella.",
                  confirm: "Borrar",
                  danger: true,
                })
              )
                del.mutate();
            }}
          >
            Borrar
          </Button>
          <div className="w-full">
            <ErrorNote error={del.error} />
          </div>
        </div>
      )}
    </li>
  );
}

/** Todas las predicciones como CSV, para una hoja de cálculo. */
export function ExportLink({ className }: { className?: string }) {
  return (
    <a href="/api/ledger.csv" download className={cx("inline-flex items-center gap-1.5 text-sm text-accent underline", className)}>
      <Icon name="download" className="size-4" /> Exportar todo (CSV)
    </a>
  );
}
