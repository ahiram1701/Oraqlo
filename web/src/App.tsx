import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Icon, type IconName } from "./components/icons";
import { cx, PageSkeleton } from "./components/ui";
import { api, type Goal } from "./lib/api";
import { useSetup } from "./lib/setup";
import Ask from "./pages/Ask";
import Ledger from "./pages/Ledger";

// Lo pesado (gráficos, Markdown, QR) se carga solo al abrir esa pantalla.
const GoalsList = lazy(() => import("./pages/Goals").then((m) => ({ default: m.GoalsList })));
const HistoryList = lazy(() => import("./pages/History").then((m) => ({ default: m.HistoryList })));
const GoalDetail = lazy(() => import("./pages/Goals").then((m) => ({ default: m.GoalDetail })));
const RunDetail = lazy(() => import("./pages/History").then((m) => ({ default: m.RunDetail })));
const Practice = lazy(() => import("./pages/Practice"));
const Calibration = lazy(() => import("./pages/Calibration"));
const CaseEditor = lazy(() => import("./pages/CaseEditor"));
const Settings = lazy(() => import("./pages/Settings"));
const Welcome = lazy(() => import("./pages/Welcome"));

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
}

/** Todas las secciones. En el celular, el primer grupo va en la barra y el segundo en «Más». */
export const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: "Usar",
    items: [
      { to: "/", label: "Preguntar", icon: "ask", end: true },
      { to: "/objetivos", label: "Objetivos", icon: "goals" },
      { to: "/predicciones", label: "Predicciones", icon: "ledger" },
      { to: "/historial", label: "Historial", icon: "history" },
    ],
  },
  {
    group: "Precisión y ajustes",
    items: [
      { to: "/practica", label: "Práctica", icon: "practice" },
      { to: "/calibracion", label: "Calibración", icon: "calibration" },
      { to: "/caso", label: "Caso", icon: "case" },
      { to: "/ajustes", label: "Ajustes", icon: "settings" },
    ],
  },
];

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Preguntar"],
  [/^\/objetivos\/.+/, "Objetivo"],
  [/^\/objetivos/, "Objetivos"],
  [/^\/predicciones/, "Predicciones"],
  [/^\/practica/, "Práctica"],
  [/^\/caso/, "Caso"],
  [/^\/calibracion/, "Calibración"],
  [/^\/historial\/.+/, "Resultado"],
  [/^\/historial/, "Historial"],
  [/^\/ajustes/, "Ajustes"],
  [/^\/bienvenida/, "Bienvenida"],
];

/** Pantallas de detalle → a qué lista vuelve el botón atrás. */
const PARENT: [RegExp, string][] = [
  [/^\/objetivos\/.+/, "/objetivos"],
  [/^\/historial\/.+/, "/historial"],
];

function useOverdueCount() {
  const { data } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals"), staleTime: 60_000 });
  return data?.reduce((n, g) => n + (g.status === "activo" ? g.overdue.length : 0), 0) ?? 0;
}

function navLabel(item: NavItem, overdue: number) {
  return item.to === "/objetivos" && overdue > 0 ? `${item.label} (${overdue} hitos vencidos)` : item.label;
}

function SideNav() {
  const overdue = useOverdueCount();
  return (
    <nav className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-1 border-r border-line p-4 md:flex" aria-label="Secciones">
      <div className="mb-6 flex items-center gap-2.5 px-2">
        <img src="/icons/icon-192.png" alt="" className="size-8 rounded-xl" />
        <span className="brand-text text-xl font-semibold tracking-tight">Oraqlo</span>
      </div>
      {NAV.map((group) => (
        <div key={group.group} className="mb-3">
          <p className="mb-1 px-3 text-xs font-semibold tracking-wider text-muted uppercase">{group.group}</p>
          {group.items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              aria-label={navLabel(item, overdue)}
              className={({ isActive }) =>
                cx(
                  "flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition",
                  isActive ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
                )
              }
            >
              <Icon name={item.icon} />
              {item.label}
              {item.to === "/objetivos" && overdue > 0 && (
                <span aria-hidden className="ml-auto rounded-full bg-warn px-1.5 text-xs font-semibold text-black">
                  {overdue}
                </span>
              )}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

/** Barra inferior: lo de uso diario a un toque; el resto, en «Más». */
function BottomNav() {
  const overdue = useOverdueCount();
  const location = useLocation();
  const [primary, secondary] = [NAV[0].items, NAV[1].items];
  const [open, setOpen] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  const moreButton = useRef<HTMLButtonElement>(null);
  const inMore = secondary.some((item) => location.pathname.startsWith(item.to));

  useEffect(() => setOpen(false), [location.pathname]); // al navegar, se cierra

  useEffect(() => {
    if (!open) return;
    sheet.current?.querySelector<HTMLElement>("a")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        moreButton.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const tab = (active: boolean) =>
    cx("relative flex min-w-0 flex-1 flex-col items-center gap-0.5 py-2 text-[11px]", active ? "text-accent" : "text-muted");
  const pill = (active: boolean) => cx("rounded-full px-3 py-0.5 transition", active && "bg-accent/15");

  return (
    <>
      {open && (
        <div className="fixed inset-0 z-20 bg-black/40 md:hidden" aria-hidden onClick={() => setOpen(false)} />
      )}
      <nav
        aria-label="Secciones"
        className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 backdrop-blur md:hidden"
        style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      >
        {open && (
          <div
            ref={sheet}
            id="mas-secciones"
            className="grid grid-cols-2 gap-2 border-b border-line p-3"
            aria-label="Más secciones"
            role="group"
          >
            {secondary.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  cx(
                    "flex min-h-12 items-center gap-3 rounded-xl px-3 text-sm",
                    isActive ? "bg-accent/15 font-medium text-accent" : "bg-surface-2 text-fg",
                  )
                }
              >
                <Icon name={item.icon} />
                {item.label}
              </NavLink>
            ))}
          </div>
        )}
        <div className="flex px-0.5">
          {primary.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              aria-label={navLabel(item, overdue)}
              className={({ isActive }) => tab(isActive && !open)}
            >
              {({ isActive }) => (
                <>
                  <span className={pill(isActive && !open)}>
                    <Icon name={item.icon} />
                  </span>
                  <span className="max-w-full truncate">{item.label}</span>
                  {item.to === "/objetivos" && overdue > 0 && (
                    <span aria-hidden className="absolute top-1.5 right-[calc(50%-1.25rem)] size-2 rounded-full bg-warn" />
                  )}
                </>
              )}
            </NavLink>
          ))}
          <button
            ref={moreButton}
            type="button"
            className={tab(inMore || open)}
            aria-expanded={open}
            aria-controls="mas-secciones"
            aria-current={inMore ? "page" : undefined}
            onClick={() => setOpen(!open)}
          >
            <span className={pill(inMore || open)}>
              <Icon name={open ? "x" : "more"} />
            </span>
            Más
          </button>
        </div>
      </nav>
    </>
  );
}

function Header({ title }: { title: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const parent = PARENT.find(([re]) => re.test(location.pathname))?.[1];
  return (
    <header className="mb-4 flex min-h-11 items-center gap-2 md:mb-6">
      {parent ? (
        <button
          type="button"
          aria-label="Volver"
          onClick={() => (window.history.length > 1 ? navigate(-1) : navigate(parent))}
          className="-ml-2 inline-flex size-11 items-center justify-center rounded-xl text-muted hover:text-fg"
        >
          <Icon name="back" />
        </button>
      ) : (
        <img src="/icons/icon-192.png" alt="" className="size-7 rounded-lg md:hidden" />
      )}
      <h1 className="text-[1.375rem] font-semibold tracking-tight">{title}</h1>
    </header>
  );
}

export default function App() {
  const location = useLocation();
  const title = TITLES.find(([re]) => re.test(location.pathname))?.[1] ?? "Oraqlo";
  const navigate = useNavigate();

  useEffect(() => {
    document.title = title === "Preguntar" ? "Oraqlo" : `${title} · Oraqlo`;
  }, [title]);

  // Tocar un aviso con la app abierta: el service worker pide navegar sin recargar.
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    const onMessage = (e: MessageEvent) => {
      if (e.data?.type === "navigate" && typeof e.data.url === "string") navigate(e.data.url);
    };
    navigator.serviceWorker.addEventListener("message", onMessage);
    return () => navigator.serviceWorker.removeEventListener("message", onMessage);
  }, [navigate]);

  // Primer arranque: sin un modelo listo, todo empieza por la bienvenida.
  const setup = useSetup();
  if (setup.data?.needs_setup && location.pathname !== "/bienvenida") return <Navigate to="/bienvenida" replace />;
  const welcome = location.pathname === "/bienvenida";

  return (
    <div className="flex min-h-dvh">
      {!welcome && <SideNav />}
      <main className="safe-bottom mx-auto w-full max-w-3xl px-4 pt-[calc(env(safe-area-inset-top)+1rem)] sm:px-6">
        <Header title={title} />
        <Suspense fallback={<PageSkeleton />}>
          <Routes>
            <Route path="/" element={<Ask />} />
            <Route path="/objetivos" element={<GoalsList />} />
            <Route path="/objetivos/:id" element={<GoalDetail />} />
            <Route path="/predicciones" element={<Ledger />} />
            <Route path="/ledger" element={<Navigate to="/predicciones" replace />} />
            <Route path="/practica" element={<Practice />} />
            <Route path="/caso" element={<CaseEditor />} />
            <Route path="/calibracion" element={<Calibration />} />
            <Route path="/historial" element={<HistoryList />} />
            <Route path="/historial/:id" element={<RunDetail />} />
            <Route path="/ajustes" element={<Settings />} />
            <Route path="/mas" element={<Navigate to="/" replace />} />
            <Route path="/bienvenida" element={<Welcome />} />
            <Route
              path="*"
              element={
                <div className="rounded-2xl border border-dashed border-line p-8 text-center">
                  <p className="font-medium">Esta página no existe.</p>
                  <NavLink to="/" className="mt-2 inline-block text-sm text-accent underline">
                    Ir a Preguntar
                  </NavLink>
                </div>
              }
            />
          </Routes>
        </Suspense>
      </main>
      {!welcome && <BottomNav />}
    </div>
  );
}
