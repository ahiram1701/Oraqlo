import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { vi } from "vitest";
import { useJob } from "./useJob";

class FakeSource {
  static CLOSED = 2;
  static last: FakeSource;
  readyState = 0;
  onerror: (() => void) | null = null;
  listeners: Record<string, ((ev: MessageEvent) => void)[]> = {};
  constructor(public url: string) {
    FakeSource.last = this;
  }
  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(fn);
  }
  close() {
    this.readyState = FakeSource.CLOSED;
  }
  /** El servidor respondió algo que no es 200: EventSource se rinde. */
  failForGood() {
    this.readyState = FakeSource.CLOSED;
    this.onerror?.();
  }
}

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

function mockJob(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })));
}

beforeEach(() => vi.stubGlobal("EventSource", FakeSource));
afterEach(() => vi.unstubAllGlobals());

test("si el job ya no existe (404), se olvida en vez de quedarse 'conectando'", async () => {
  const gone = vi.fn();
  mockJob(404, { detail: "Job no encontrado" });
  const { result } = renderHook(() => useJob("viejo", gone), { wrapper });
  expect(result.current.status).toBe("connecting");
  act(() => FakeSource.last.failForGood());
  await waitFor(() => expect(gone).toHaveBeenCalled());
  expect(result.current.status).toBe("idle");
});

test("si el servidor se reinició pero el job terminó, muestra el resultado", async () => {
  mockJob(200, { id: "j", status: "done", error: null, run_id: "r1", result: { ok: true } });
  const { result } = renderHook(() => useJob("j"), { wrapper });
  act(() => FakeSource.last.failForGood());
  await waitFor(() => expect(result.current.status).toBe("done"));
  expect(result.current.runId).toBe("r1");
  expect(result.current.result).toEqual({ ok: true });
});

test("los errores del job llegan como job_error y se muestran", async () => {
  mockJob(200, {});
  const { result } = renderHook(() => useJob("j"), { wrapper });
  act(() => {
    for (const fn of FakeSource.last.listeners.job_error ?? []) {
      fn(new MessageEvent("job_error", { data: JSON.stringify({ msg: "sin clave" }), lastEventId: "3" }));
    }
  });
  expect(result.current.events).toEqual([{ seq: 3, type: "error", data: { msg: "sin clave" } }]);
});
