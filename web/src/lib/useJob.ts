import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type Job, type JobEvent, type JobStatus } from "./api";

export interface JobState<R = any> {
  events: JobEvent[];
  status: JobStatus | "connecting" | "idle";
  result: R | null;
  error: string | null;
  runId: string | null;
}

/**
 * Sigue un job por SSE. EventSource reconecta solo y reenvía Last-Event-ID,
 * así que una pantalla bloqueada o un cambio de red no pierden eventos:
 * el servidor reanuda desde el último recibido.
 */
export function useJob<R = any>(jobId: string | null | undefined): JobState<R> {
  const qc = useQueryClient();
  const [state, setState] = useState<JobState<R>>({
    events: [],
    status: jobId ? "connecting" : "idle",
    result: null,
    error: null,
    runId: null,
  });
  const seen = useRef(new Set<number>());

  useEffect(() => {
    if (!jobId) {
      setState({ events: [], status: "idle", result: null, error: null, runId: null });
      return;
    }
    seen.current = new Set();
    setState({ events: [], status: "connecting", result: null, error: null, runId: null });
    const source = new EventSource(`/api/jobs/${jobId}/events`);
    let closed = false;

    const onEvent = (type: string) => (ev: MessageEvent) => {
      const seq = Number(ev.lastEventId);
      if (seq && seen.current.has(seq)) return;
      if (seq) seen.current.add(seq);
      const data = JSON.parse(ev.data);
      setState((s) => ({ ...s, status: "running", events: [...s.events, { seq, type, data }] }));
    };
    for (const type of ["log", "phase", "forecast", "turn", "verdict", "revision", "fact", "error"]) {
      source.addEventListener(type, onEvent(type));
    }
    source.addEventListener("end", async (ev) => {
      closed = true;
      source.close();
      const end = JSON.parse((ev as MessageEvent).data);
      let result: R | null = null;
      try {
        result = ((await api.get<Job<R>>(`/jobs/${jobId}`)).result ?? null) as R | null;
      } catch {
        /* el resultado se puede reconsultar luego */
      }
      setState((s) => ({ ...s, status: end.status, error: end.error, runId: end.run_id, result }));
      // Refresca las vistas que el job pudo cambiar.
      for (const key of ["ledger", "goals", "runs", "calibration", "practice", "jobs"]) {
        qc.invalidateQueries({ queryKey: [key] });
      }
    });
    source.onerror = () => {
      if (!closed) setState((s) => ({ ...s, status: s.status === "connecting" ? "connecting" : s.status }));
    };
    return () => {
      closed = true;
      source.close();
    };
  }, [jobId, qc]);

  return state;
}

/** Recuerda el último job lanzado por pantalla (sobrevive a recargar la app). */
export function useStoredJob(key: string): [string | null, (id: string | null) => void] {
  const storageKey = `oraqlo.job.${key}`;
  const [id, setId] = useState<string | null>(() => {
    try {
      return localStorage.getItem(storageKey);
    } catch {
      return null;
    }
  });
  const update = (value: string | null) => {
    setId(value);
    try {
      if (value) localStorage.setItem(storageKey, value);
      else localStorage.removeItem(storageKey);
    } catch {
      /* almacenamiento no disponible */
    }
  };
  return [id, update];
}
