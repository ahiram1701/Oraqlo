// Controles comunes de las listas: buscar, filtrar con chips y ordenar.
import { useCallback, useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./icons";
import { Button, cx, inputClass } from "./ui";

export function SearchInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <label className="relative block">
      <span className="sr-only">{label}</span>
      <Icon name="search" className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" />
      <input
        type="search"
        className={cx(inputClass, "pl-9")}
        placeholder="Buscar…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export interface Option<T extends string> {
  value: T;
  label: string;
  count?: number;
}

/** Chips de una sola elección (radiogrupo: flechas para moverse). */
/** Difumina el borde por el que hay más chips: se ve que se puede deslizar. */
export function fadeMask(edges: { start: boolean; end: boolean }): CSSProperties | undefined {
  if (!edges.start && !edges.end) return undefined;
  const mask = `linear-gradient(to right, ${edges.start ? "transparent 0, black 28px" : "black 0"}, ${
    edges.end ? "black calc(100% - 28px), transparent 100%" : "black 100%"
  })`;
  return { maskImage: mask, WebkitMaskImage: mask };
}

export function ChipGroup<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: Option<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: false, end: false });
  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const start = el.scrollLeft > 4;
    const end = el.scrollLeft + el.clientWidth < el.scrollWidth - 4;
    setEdges((prev) => (prev.start === start && prev.end === end ? prev : { start, end }));
  }, []);
  useEffect(() => {
    measure();
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [measure, options.length]);

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const i = options.findIndex((o) => o.value === value);
    const next = options[(i + step + options.length) % options.length];
    onChange(next.value);
    (e.currentTarget.querySelector(`[data-value="${next.value}"]`) as HTMLElement | null)?.focus();
  }
  return (
    <div
      ref={ref}
      role="radiogroup"
      aria-label={label}
      className="no-scrollbar -mx-1 flex gap-2 overflow-x-auto px-1"
      style={fadeMask(edges)}
      onKeyDown={onKey}
      onScroll={measure}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            tabIndex={on ? 0 : -1}
            data-value={o.value}
            onClick={() => onChange(o.value)}
            className={cx(
              "min-h-10 shrink-0 rounded-full border px-3.5 text-sm transition",
              on ? "border-accent bg-accent/15 text-accent" : "border-line text-muted hover:text-fg",
            )}
          >
            {o.label}
            {o.count != null && <span className="ml-1 tabular-nums opacity-70">{o.count}</span>}
          </button>
        );
      })}
    </div>
  );
}

export function SortSelect<T extends string>({
  options,
  value,
  onChange,
}: {
  options: Option<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm whitespace-nowrap text-muted">
      Ordenar
      <select className={cx(inputClass, "w-auto py-1.5")} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

/** «3 de 12» cuando hay filtros; si no, el total. */
export function countLabel(shown: number, total: number) {
  return shown === total ? String(total) : `${shown} de ${total}`;
}

/** Lo que se ve cuando los filtros lo esconden todo. */
export function FilteredEmpty({ onClear, children }: { onClear: () => void; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line p-6 text-center text-sm text-muted">
      <p>{children ?? "Nada coincide con estos filtros."}</p>
      <Button variant="secondary" className="mt-3" onClick={onClear}>
        Quitar filtros
      </Button>
    </div>
  );
}

export const includesText = (text: string, query: string) => !query.trim() || text.toLowerCase().includes(query.trim().toLowerCase());
