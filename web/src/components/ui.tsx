import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type AnchorHTMLAttributes,
  type ComponentProps,
  type ReactNode,
} from "react";
import { Icon } from "./icons";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

export function Card({ children, className, id }: { children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={cx("scroll-mt-20 rounded-2xl border border-line bg-surface p-4 sm:p-5", className)}>
      {children}
    </section>
  );
}

export function SectionTitle({ children, aside }: { children: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="text-sm font-semibold tracking-wide text-muted uppercase">{children}</h2>
      {aside}
    </div>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger";

const BUTTON_BASE =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-medium transition active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100";
const BUTTON_VARIANTS: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:brightness-110",
  secondary: "bg-surface-2 text-fg border border-line hover:border-accent",
  ghost: "text-muted hover:text-fg",
  danger: "bg-bad/10 text-bad border border-bad/30 hover:bg-bad/20",
};

export function buttonClass(variant: Variant = "primary", className?: string) {
  return cx(BUTTON_BASE, BUTTON_VARIANTS[variant], className);
}

export function Button({
  variant = "primary",
  className,
  ...props
}: ComponentProps<"button"> & { variant?: Variant }) {
  return <button className={buttonClass(variant, className)} {...props} />;
}

/** Un enlace con aspecto de botón (descargas, sitios externos). */
export function LinkButton({
  variant = "primary",
  className,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & { variant?: Variant }) {
  // El texto llega en `children`, dentro de props.
  // oxlint-disable-next-line jsx-a11y/anchor-has-content
  return <a className={buttonClass(variant, className)} {...props} />;
}

export type Tone = "muted" | "good" | "warn" | "bad" | "accent";

export function Badge({ children, tone = "muted" }: { children: ReactNode; tone?: Tone }) {
  const tones: Record<Tone, string> = {
    muted: "bg-surface-2 text-muted",
    good: "bg-good/15 text-good",
    warn: "bg-warn/15 text-warn",
    bad: "bg-bad/15 text-bad",
    accent: "bg-accent/15 text-accent",
  };
  return (
    <span className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", tones[tone])}>
      {children}
    </span>
  );
}

/** `decorative`: dentro de un texto que ya anuncia el estado (evita leerlo dos veces). */
export function Spinner({ className, decorative = false }: { className?: string; decorative?: boolean }) {
  return (
    <span
      role={decorative ? undefined : "status"}
      aria-hidden={decorative || undefined}
      className={cx(
        "inline-block size-4 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none",
        className,
      )}
      aria-label={decorative ? undefined : "cargando"}
    />
  );
}

/** Bloque gris que ocupa el sitio de lo que está cargando (mejor que una pantalla en blanco). */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded-xl bg-surface-2 motion-reduce:animate-none", className)} />;
}

export function PageSkeleton() {
  return (
    <div className="space-y-4" role="status" aria-label="cargando">
      <Skeleton className="h-28" />
      <Skeleton className="h-44" />
      <Skeleton className="h-20" />
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">{children}</p>;
}

const NOTICE: Record<"info" | "warn" | "bad" | "good", { box: string; icon: "info" | "alert" | "x" | "check" }> = {
  info: { box: "bg-accent/10 text-accent", icon: "info" },
  warn: { box: "bg-warn/10 text-warn", icon: "alert" },
  bad: { box: "border border-bad/30 bg-bad/10 text-bad", icon: "x" },
  good: { box: "bg-good/10 text-good", icon: "check" },
};

/** Aviso con icono: sustituye a los recuadros de color hechos a mano. */
export function Notice({ tone = "info", children, className }: { tone?: keyof typeof NOTICE; children: ReactNode; className?: string }) {
  const n = NOTICE[tone];
  return (
    <div role={tone === "bad" ? "alert" : undefined} className={cx("flex gap-2 rounded-xl p-3 text-sm", n.box, className)}>
      <Icon name={n.icon} className="mt-0.5 size-4" />
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Notice tone="bad">
      <span>{message}</span>
      {onRetry && (
        <button type="button" onClick={onRetry} className="ml-2 inline-flex min-h-9 items-center font-medium underline">
          Reintentar
        </button>
      )}
    </Notice>
  );
}

export const inputClass =
  "w-full rounded-xl border border-line bg-surface-2 px-3 py-2.5 text-base text-fg placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 sm:text-sm";

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 text-sm">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={cx("relative h-6 w-11 shrink-0 rounded-full transition", checked ? "bg-accent" : "border border-line bg-surface-2")}
      >
        <span className={cx("absolute top-0.5 size-5 rounded-full bg-white shadow transition", checked ? "left-5.5" : "left-0.5")} />
      </button>
      {label}
    </label>
  );
}

// ── estados y veredictos en lenguaje llano ────────────────────────────────

const STATUS: Record<string, { label: string; tone: Tone }> = {
  activo: { label: "Activo", tone: "accent" },
  logrado: { label: "Logrado", tone: "good" },
  "no-logrado": { label: "No logrado", tone: "bad" },
  abandonado: { label: "Abandonado", tone: "muted" },
  pendiente: { label: "Pendiente", tone: "muted" },
  cumplido: { label: "Cumplido", tone: "good" },
  fallado: { label: "Fallado", tone: "bad" },
  queued: { label: "En cola", tone: "muted" },
  running: { label: "En curso", tone: "accent" },
  done: { label: "Lista", tone: "good" },
  failed: { label: "Falló", tone: "bad" },
  cancelled: { label: "Cancelada", tone: "muted" },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: "muted" as Tone };
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

/** Lo que significa cada decisión del Juez, en palabras de usuario. */
export const VERDICTS: Record<string, { label: string; tone: Tone; meaning: string }> = {
  emitir: { label: "Recomendado", tone: "good", meaning: "El panel respalda esta jugada." },
  revisar: { label: "Revisar", tone: "warn", meaning: "Puede funcionar, pero conviene ajustar algo antes." },
  descartar: { label: "No recomendado", tone: "bad", meaning: "El panel no la respalda: hay mejores opciones o demasiado riesgo." },
};

export function verdictInfo(decision: string | undefined) {
  return VERDICTS[decision ?? ""] ?? { label: decision ?? "—", tone: "muted" as Tone, meaning: "" };
}

export function VerdictBadge({ decision }: { decision: string | undefined }) {
  const v = verdictInfo(decision);
  return <Badge tone={v.tone}>{v.label}</Badge>;
}

/** "≈7 de cada 10" y una palabra para la probabilidad: más fácil que un porcentaje solo. */
export function plainProbability(p: number): string {
  const word =
    p >= 0.9
      ? "Casi seguro"
      : p >= 0.75
        ? "Muy probable"
        : p >= 0.55
          ? "Probable"
          : p >= 0.45
            ? "Como lanzar una moneda"
            : p >= 0.25
              ? "Poco probable"
              : "Muy improbable";
  return `${word} · ≈${Math.round(p * 10)} de cada 10`;
}

// ── confirmación y avisos con deshacer ────────────────────────────────────

interface ConfirmOptions {
  title: string;
  body?: ReactNode;
  confirm?: string;
  danger?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;
type ToastFn = (message: string, options?: { undo?: () => void; ms?: number }) => void;

const FeedbackContext = createContext<{ confirm: ConfirmFn; toast: ToastFn } | null>(null);

/** Hoja de confirmación propia (en vez de window.confirm) y toasts con "Deshacer". */
export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [ask, setAsk] = useState<(ConfirmOptions & { resolve: (ok: boolean) => void }) | null>(null);
  const [toastState, setToast] = useState<{ id: number; message: string; undo?: () => void } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => new Promise((resolve) => setAsk({ ...options, resolve })), []);
  const toast = useCallback<ToastFn>((message, options) => {
    clearTimeout(timer.current);
    const id = Date.now();
    setToast({ id, message, undo: options?.undo });
    timer.current = setTimeout(() => setToast((t) => (t?.id === id ? null : t)), options?.ms ?? 5000);
  }, []);

  useEffect(() => {
    if (!ask) return;
    returnFocus.current = document.activeElement as HTMLElement | null;
    confirmButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(false);
      // El foco no se escapa del diálogo: Tab da la vuelta entre sus botones.
      if (e.key === "Tab" && dialog.current) {
        const items = Array.from(dialog.current.querySelectorAll<HTMLElement>("button, [href], input, select, textarea"));
        if (items.length === 0) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      returnFocus.current?.focus?.(); // al cerrar, el foco vuelve a donde estaba
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ask]);

  function close(ok: boolean) {
    ask?.resolve(ok);
    setAsk(null);
  }

  return (
    <FeedbackContext.Provider value={{ confirm, toast }}>
      {children}
      {ask && (
        // Clic en el fondo = cancelar; con teclado se cancela con Escape (ver arriba).
        // oxlint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 p-4 sm:items-center" onClick={() => close(false)}>
          {/* Solo evita que el clic llegue al fondo; no es una acción. */}
          {/* oxlint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions */}
          <div
            ref={dialog}
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
            className="w-full max-w-sm rounded-2xl border border-line bg-surface p-5 shadow-2xl"
            style={{ marginBottom: "env(safe-area-inset-bottom)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="confirm-title" className="text-base font-semibold">
              {ask.title}
            </h2>
            {ask.body && <div className="mt-2 text-sm text-muted">{ask.body}</div>}
            <div className="mt-5 flex gap-2">
              <Button variant="secondary" className="flex-1" onClick={() => close(false)}>
                Cancelar
              </Button>
              <Button ref={confirmButton} variant={ask.danger ? "danger" : "primary"} className="flex-1" onClick={() => close(true)}>
                {ask.confirm ?? "Confirmar"}
              </Button>
            </div>
          </div>
        </div>
      )}
      {toastState && (
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-x-0 bottom-[calc(env(safe-area-inset-bottom)+5rem)] z-40 mx-auto flex w-[calc(100%-2rem)] max-w-md items-center gap-3 rounded-xl border border-line bg-surface-2 p-3 text-sm shadow-xl md:bottom-6"
        >
          <span className="flex-1">{toastState.message}</span>
          {toastState.undo && (
            <Button
              variant="ghost"
              className="min-h-9 px-2 text-accent"
              onClick={() => {
                toastState.undo?.();
                clearTimeout(timer.current);
                setToast(null);
              }}
            >
              Deshacer
            </Button>
          )}
        </div>
      )}
    </FeedbackContext.Provider>
  );
}

export function useFeedback() {
  const ctx = useContext(FeedbackContext);
  if (!ctx) throw new Error("useFeedback necesita <FeedbackProvider>");
  return ctx;
}
