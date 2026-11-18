import { lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import { PageSkeleton, cx } from "../components/ui";

const Practice = lazy(() => import("./Practice"));
const Calibration = lazy(() => import("./Calibration"));

type Tab = "practica" | "calibracion";

const TABS: { tab: Tab; to: string; label: string; hint: string }[] = [
  { tab: "practica", to: "/practica", label: "Práctica", hint: "Oraqlo contra el mercado" },
  { tab: "calibracion", to: "/calibracion", label: "Calibración", hint: "Cuánto acierta" },
];

/** Precisión: entrenar (Práctica) y medir (Calibración), en una sola pestaña. */
export default function Precision({ tab }: { tab: Tab }) {
  return (
    <div className="space-y-4">
      <nav aria-label="Precisión" className="grid grid-cols-2 gap-1 rounded-2xl border border-line bg-surface-2 p-1">
        {TABS.map((t) => {
          const active = t.tab === tab;
          return (
            <Link
              key={t.tab}
              to={t.to}
              replace
              aria-current={active ? "page" : undefined}
              className={cx(
                "flex min-h-11 flex-col items-center justify-center rounded-xl px-2 py-1.5 text-center transition-colors",
                active ? "bg-surface font-medium text-fg shadow-sm" : "text-muted hover:text-fg",
              )}
            >
              <span className="text-sm">{t.label}</span>
              <span className="text-[11px] text-muted">{t.hint}</span>
            </Link>
          );
        })}
      </nav>
      <Suspense fallback={<PageSkeleton />}>{tab === "practica" ? <Practice /> : <Calibration />}</Suspense>
    </div>
  );
}
