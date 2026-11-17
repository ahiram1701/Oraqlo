import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { ApiError, api, type Job, type JobEvent, type JobStatus } from "./api";

export interface JobState<R = any> {
  events: JobEvent[];
  status: JobStatus | "connecting" | "idle";
  result: R | null;
  error: string | null;
  runId: string | null;
}

const EVENT_TYPES = ["log", "phase", "forecast", "turn", "verdict", "revision", "fact", "questions", "answers"];
const TERMINAL: string[] = ["done", "failed", "cancelled"];
const REFRESH = ["ledger", "goals", "runs", "calibration", "practice", "jobs"];

/**
 * Sigue un job por SSE. Ante cortes de red, EventSource reconecta solo y el
 * servidor reanuda desde el último evento. Pero si el servidor responde algo que
 * no es 200 (502 mientras Oraqlo se reinicia, 404 de un job que ya no existe,
 * 401), EventSource se rinde para siempre: entonces se consulta el job y se
 * reconecta con espera creciente, o se olvida (`onGone`) si ya no existe.
 */
export function useJob<R = any>(jobId: string | null | undefined, onGone?: () => void): JobState<R> {
  const qc = useQueryClient();
  const [state, setState] = useState<JobState<R>>({
    events: [],
    status: jobId ? "connecting" : "idle",
    result: null,
    error: null,
    runId: null,
  });
  const seen = useRef(new Set<number>());
  const gone = useRef(onGone);
  gone.current = onGone;

  useEffect(() => {
    if (!jobId) {
      setState({ events: [], status: "idle", result: null, error: null, runId: null });
      return;
    }
    seen.current = new Set();
    setState({ events: [], status: "connecting", result: null, error: null, runId: null });
    let closed = false;
    let source: EventSource | null = null;
    let lastSeq = 0;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const finish = async (status: JobStatus, error: string | null, runId: string | null) => {
      closed = true;
      source?.close();
      let result: R | null = null;
      try {
        result = ((await api.get<Job<R>>(`/jobs/${jobId}`)).result ?? null) as R | null;
      } catch {
        /* el resultado se puede reconsultar luego */
      }
      setState((s) => ({ ...s, status, error, runId, result }));
      for (const key of REFRESH) qc.invalidateQueries({ queryKey: [key] });
    };

    const onEvent = (type: string) => (ev: MessageEvent) => {
      const seq = Number(ev.lastEventId);
      if (seq && seen.current.has(seq)) return;
      if (seq) {
        seen.current.add(seq);
        lastSeq = Math.max(lastSeq, seq);
      }
      attempt = 0;
      const data = JSON.parse(ev.data);
      setState((s) => ({ ...s, status: "running", events: [...s.events, { seq, type, data }] }));
    };

    const recover = async () => {
      if (closed) return;
      try {
        const job = await api.get<Job<R>>(`/jobs/${jobId}`);
        if (TERMINAL.includes(job.status)) return finish(job.status, job.error, job.run_id);
      } catch (e) {
        if (e instanceof ApiError && e.status === 404) {
          closed = true;
          setState({ events: [], status: "idle", result: null, error: null, runId: null });
          gone.current?.();
          return;
        }
      }
      // Sigue en curso (o el servidor aún no responde): reintentar con espera creciente.
      attempt += 1;
      timer = setTimeout(open, Math.min(30_000, 1000 * 2 ** attempt));
    };

    function open() {
      if (closed) return;
      source = new EventSource(`/api/jobs/${jobId}/events${lastSeq ? `?after=${lastSeq}` : ""}`);
      for (const type of EVENT_TYPES) source.addEventListener(type, onEvent(type));
      // El servidor llama "job_error" a sus errores: "error" es el evento nativo de
      // EventSource (cortes de conexión), que no trae datos.
      source.addEventListener("job_error", onEvent("error"));
      source.addEventListener("end", (ev) => {
        const end = JSON.parse((ev as MessageEvent).data);
        void finish(end.status, end.error, end.run_id);
      });
      source.onerror = () => {
        if (closed || !source || source.readyState !== EventSource.CLOSED) return; // reconecta solo
        void recover();
      };
    }

    open();
    return () => {
      closed = true;
      clearTimeout(timer);
      source?.close();
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
