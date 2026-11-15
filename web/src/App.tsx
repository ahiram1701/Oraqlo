import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { cx } from "./components/ui";
import { api, type Goal } from "./lib/api";
import Ask from "./pages/Ask";
import Calibration from "./pages/Calibration";
import CaseEditor from "./pages/CaseEditor";
import { GoalDetail, GoalsList } from "./pages/Goals";
import { HistoryList, RunDetail } from "./pages/History";
import Ledger from "./pages/Ledger";
import Practice from "./pages/Practice";
import Settings from "./pages/Settings";

const ICONS: Record<string, ReactNode> = {
  ask: <path d="M12 3a9 9 0 1 0 9 9M12 7v5l3 2" />,
  goals: <path d="M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18Zm0-5a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-3.5a.5.5 0 1 0 0-1 .5.5 0 0 0 0 1Z" />,
  ledger: <path d="M5 4h14v16H5zM9 8h6M9 12h6M9 16h3" />,
  practice: <path d="M4 19V9m5 10V5m5 14v-7m5 7V8" />,
  more: <path d="M5 12h.01M12 12h.01M19 12h.01" />,
  case: <path d="M4 7h16v12H4zM9 7V5h6v2" />,
  calibration: <path d="M4 20 20 4M4 4h4M4 4v4M20 20h-4M20 20v-4" />,
  history: <path d="M4 12a8 8 0 1 0 2.3-5.6L4 8.7M4 4v4.7h4.7M12 8v4l3 2" />,
  settings: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm7.4-3a7.4 7.4 0 0 0-.1-1.3l2-1.6-2-3.4-2.4 1a7.6 7.6 0 0 0-2.2-1.3L14.4 2h-4.8l-.3 2.4a7.6 7.6 0 0 0-2.2 1.3l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.6l-2 1.6 2 3.4 2.4-1a7.6 7.6 0 0 0 2.2 1.3l.3 2.4h4.8l.3-2.4a7.6 7.6 0 0 0 2.2-1.3l2.4 1 2-3.4-2-1.6c.1-.4.1-.9.1-1.3Z" />,
};

function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {ICONS[name]}
    </svg>
  );
}

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

const PRIMARY: NavItem[] = [
  { to: "/", label: "Preguntar", icon: "ask", end: true },
  { to: "/objetivos", label: "Objetivos", icon: "goals" },
  { to: "/ledger", label: "Ledger", icon: "ledger" },
  { to: "/practica", label: "Práctica", icon: "practice" },
];
const SECONDARY: NavItem[] = [
  { to: "/caso", label: "Caso", icon: "case" },
  { to: "/calibracion", label: "Calibración", icon: "calibration" },
  { to: "/historial", label: "Historial", icon: "history" },
  { to: "/ajustes", label: "Ajustes", icon: "settings" },
];

const TITLES: [RegExp, string][] = [
  [/^\/$/, "Preguntar"],
  [/^\/objetivos/, "Objetivos"],
  [/^\/ledger/, "Ledger"],
  [/^\/practica/, "Práctica"],
  [/^\/caso/, "Caso"],
  [/^\/calibracion/, "Calibración"],
  [/^\/historial/, "Historial"],
  [/^\/ajustes/, "Ajustes"],
  [/^\/mas/, "Más"],
];

function useOverdueCount() {
  const { data } = useQuery({ queryKey: ["goals"], queryFn: () => api.get<Goal[]>("/goals"), staleTime: 60_000 });
  return data?.reduce((n, g) => n + (g.status === "activo" ? g.overdue.length : 0), 0) ?? 0;
}

function SideNav() {
  const overdue = useOverdueCount();
  return (
    <nav className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col gap-1 border-r border-line p-4 md:flex">
      <div className="mb-6 flex items-center gap-2 px-2">
        <img src="/icons/icon-192.png" alt="" className="size-7 rounded-lg" />
        <span className="text-lg font-semibold">Oraqlo</span>
      </div>
      {[...PRIMARY, ...SECONDARY].map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cx("flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition", isActive ? "bg-accent/15 text-accent" : "text-muted hover:text-fg")
          }
        >
          <Icon name={item.icon} />
          {item.label}
          {item.to === "/objetivos" && overdue > 0 && <span className="ml-auto rounded-full bg-warn px-1.5 text-xs text-black">{overdue}</span>}
        </NavLink>
      ))}
    </nav>
  );
}

function BottomNav() {
  const overdue = useOverdueCount();
  const location = useLocation();
  const inMore = SECONDARY.some((s) => location.pathname.startsWith(s.to)) || location.pathname === "/mas";
  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-20 grid grid-cols-5 border-t border-line bg-surface/95 backdrop-blur md:hidden"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      {[...PRIMARY, { to: "/mas", label: "Más", icon: "more" } as NavItem].map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cx(
              "relative flex flex-col items-center gap-0.5 py-2 text-[11px]",
              isActive || (item.to === "/mas" && inMore) ? "text-accent" : "text-muted",
            )
          }
        >
          <Icon name={item.icon} />
          {item.label}
          {item.to === "/objetivos" && overdue > 0 && (
            <span className="absolute top-1 right-[calc(50%-18px)] size-2 rounded-full bg-warn" aria-label={`${overdue} hitos vencidos`} />
          )}
        </NavLink>
      ))}
    </nav>
  );
}

function More() {
  return (
    <div className="grid grid-cols-2 gap-3">
      {SECONDARY.map((item) => (
        <NavLink key={item.to} to={item.to} className="flex flex-col gap-2 rounded-2xl border border-line bg-surface p-4">
          <Icon name={item.icon} />
          <span className="text-sm font-medium">{item.label}</span>
        </NavLink>
      ))}
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const title = TITLES.find(([re]) => re.test(location.pathname))?.[1] ?? "Oraqlo";
  return (
    <div className="flex min-h-dvh">
      <SideNav />
      <main className="safe-bottom mx-auto w-full max-w-3xl px-4 pt-[calc(env(safe-area-inset-top)+1rem)] sm:px-6">
        <header className="mb-4 flex items-center gap-2 md:mb-6">
          <img src="/icons/icon-192.png" alt="" className="size-7 rounded-lg md:hidden" />
          <h1 className="text-xl font-semibold">{title}</h1>
        </header>
        <Routes>
          <Route path="/" element={<Ask />} />
          <Route path="/objetivos" element={<GoalsList />} />
          <Route path="/objetivos/:id" element={<GoalDetail />} />
          <Route path="/ledger" element={<Ledger />} />
          <Route path="/practica" element={<Practice />} />
          <Route path="/caso" element={<CaseEditor />} />
          <Route path="/calibracion" element={<Calibration />} />
          <Route path="/historial" element={<HistoryList />} />
          <Route path="/historial/:id" element={<RunDetail />} />
          <Route path="/ajustes" element={<Settings />} />
          <Route path="/mas" element={<More />} />
          <Route path="*" element={<p className="text-muted">Página no encontrada.</p>} />
        </Routes>
      </main>
      <BottomNav />
    </div>
  );
}
