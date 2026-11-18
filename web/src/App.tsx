import { useQuery } from "@tanstack/react-query";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { Icon, type IconName } from "./components/icons";
import UpdateBanner from "./components/UpdateBanner";
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
const Precision = lazy(() => import("./pages/Precision"));
const CaseEditor = lazy(() => import("./pages/CaseEditor"));
const Settings = lazy(() => import("./pages/Settings"));
const Welcome = lazy(() => import("./pages/Welcome"));

interface NavItem {
  to: string;
  label: string;
  icon: IconName;
  /** Rutas que encienden esta pestaña (además de `to`). */
  match: RegExp;
}

/** Las cinco secciones: las mismas en el celular (barra inferior) y en el escritorio. */
export const NAV: NavItem[] = [
  { to: "/", label: "Preguntar", icon: "ask", match: /^\/($|historial)/ },
  { to: "/objetivos", label: "Objetivos", icon: "goals", match: /^\/objetivos/ },
  { to: "/predicciones", label: "Predicciones", icon: "ledger", match: /^\/predicciones/ },
  { to: "/practica", label: "Precisión", icon: "precision", match: /^\/(practica|calibracion)/ },
  { to: "/ajustes", label: "Ajustes", icon: "settings", match: /^\/(ajustes|caso)/ },
];

export function activeNav(pathname: string): string | null {
  return NAV.find((item) => item.match.test(pathname))?.to ?? null;
}

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Preguntar"],
  [/^\/objetivos\/.+/, "Objetivo"],
  [/^\/objetivos/, "Objetivos"],
  [/^\/predicciones/, "Predicciones"],
  [/^\/(practica|calibracion)/, "Precisión"],
  [/^\/caso/, "Editor de casos"],
  [/^\/historial\/.+/, "Resultado"],
  [/^\/historial/, "Historial"],
  [/^\/ajustes/, "Ajustes"],
  [/^\/bienvenida/, "Bienvenida"],
];

/** Pantallas de detalle → a qué lista vuelve el botón atrás. */
const PARENT: [RegExp, string][] = [
  [/^\/objetivos\/.+/, "/objetivos"],
  [/^\/historial\/.+/, "/historial"],
  [/^\/historial$/, "/"],
  [/^\/caso/, "/"],
];

/** Lo que espera al usuario en cada sección: hitos vencidos y predicciones por resolver. */
function useNavBadges(): Record<string, number> {
  const goals = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals"), staleTime: 60_000 });
  const due = useQuery({
    queryKey: ["ledger", "overdue"],
    queryFn: () => api.get<{ count: number }>("/ledger/overdue"),
    staleTime: 60_000,
  });
  return {
    "/objetivos": goals.data?.reduce((n, g) => n + (g.status === "activo" ? g.overdue.length : 0), 0) ?? 0,
    "/predicciones": due.data?.count ?? 0,
  };
}

const BADGE_WORDS: Record<string, string> = {
  "/objetivos": "hitos vencidos",
  "/predicciones": "para resolver",
};

function navLabel(item: NavItem, badges: Record<string, number>) {
  const n = badges[item.to] ?? 0;
  return n > 0 ? `${item.label} (${n} ${BADGE_WORDS[item.to]})` : item.label;
}

/** «9+» a partir de 10: el número exacto va en la etiqueta accesible. */
export function badgeText(n: number): string {
  return n > 9 ? "9+" : String(n);
}

function SideNav() {
  const badges = useNavBadges();
  const location = useLocation();
  const current = activeNav(location.pathname);
  return (
    <nav className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-1 border-r border-line p-4 md:flex" aria-label="Secciones">
      <div className="mb-6 flex items-center gap-2.5 px-2">
        <img src="/icons/icon-192.png" alt="" className="size-8 rounded-xl" />
        <span className="brand-text text-xl font-semibold tracking-tight">Oraqlo</span>
      </div>
      {NAV.map((item) => {
        const active = current === item.to;
        return (
          <Link
            key={item.to}
            to={item.to}
            aria-label={navLabel(item, badges)}
            aria-current={active ? "page" : undefined}
            className={cx(
              "flex min-h-11 items-center gap-3 rounded-xl px-3 text-sm transition",
              active ? "bg-accent/15 font-medium text-accent" : "text-muted hover:bg-surface-2 hover:text-fg",
            )}
          >
            <Icon name={item.icon} />
            {item.label}
            {(badges[item.to] ?? 0) > 0 && (
              <span aria-hidden className="ml-auto rounded-full bg-warn px-1.5 text-xs font-semibold text-black">
                {badgeText(badges[item.to])}
              </span>
            )}
          </Link>
        );
      })}
      <Link to="/caso" className="mt-auto px-3 py-2 text-xs text-muted hover:text-fg">
        Editor de casos (avanzado)
      </Link>
    </nav>
  );
}

/** ¿Está abierto el teclado del celular? (la ventana visible se encoge mucho) */
function useKeyboardOpen(): boolean {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const onResize = () => setOpen(window.innerHeight - vv.height > 150);
    vv.addEventListener("resize", onResize);
    return () => vv.removeEventListener("resize", onResize);
  }, []);
  return open;
}

/** Barra inferior del celular: las cinco secciones a un toque, sin nada escondido. */
function BottomNav() {
  const badges = useNavBadges();
  const location = useLocation();
  const current = activeNav(location.pathname);
  const keyboard = useKeyboardOpen();
  if (keyboard) return null; // no tapa lo que escribes

  return (
    <nav
      aria-label="Secciones"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 shadow-[0_-8px_24px_-12px_rgba(0,0,0,0.5)] backdrop-blur-md md:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <ul className="mx-auto flex h-16 max-w-lg items-stretch px-1">
        {NAV.map((item) => {
          const active = current === item.to;
          const n = badges[item.to] ?? 0;
          return (
            <li key={item.to} className="flex min-w-0 flex-1">
              <Link
                to={item.to}
                aria-label={navLabel(item, badges)}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "flex min-w-0 flex-1 flex-col items-center justify-center gap-1 text-xs transition-colors active:scale-95",
                  active ? "font-medium text-accent" : "text-muted",
                )}
              >
                <span
                  className={cx(
                    "relative flex h-8 w-14 items-center justify-center rounded-full transition-colors",
                    active && "bg-accent/15",
                  )}
                >
                  <Icon name={item.icon} className="size-6" />
                  {n > 0 && (
                    <span
                      aria-hidden
                      className="absolute -top-1 right-1.5 min-w-4.5 rounded-full bg-warn px-1 text-center text-[10px] leading-4.5 font-semibold text-black"
                    >
                      {badgeText(n)}
                    </span>
                  )}
                </span>
                <span className="max-w-full truncate text-[11.5px] tracking-tight">{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
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
        <UpdateBanner />
        <Header title={title} />
        <Suspense fallback={<PageSkeleton />}>
          <Routes>
            <Route path="/" element={<Ask />} />
            <Route path="/objetivos" element={<GoalsList />} />
            <Route path="/objetivos/:id" element={<GoalDetail />} />
            <Route path="/predicciones" element={<Ledger />} />
            <Route path="/ledger" element={<Navigate to="/predicciones" replace />} />
            <Route path="/practica" element={<Precision tab="practica" />} />
            <Route path="/caso" element={<CaseEditor />} />
            <Route path="/calibracion" element={<Precision tab="calibracion" />} />
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
