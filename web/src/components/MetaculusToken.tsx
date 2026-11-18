import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type OptionsView } from "../lib/api";
import { LOCAL_ONLY_NOTE } from "../lib/setup";
import { Badge, Button, ErrorNote, Notice, cx, inputClass, useFeedback } from "./ui";

export const METACULUS_TOKEN_URL = "https://www.metaculus.com/aib/";
const ENV = "METACULUS_TOKEN";

/** El token de Metaculus, para que cuente como una voz más del comité. Todo desde aquí. */
export function MetaculusToken({ options, local }: { options: OptionsView; local: boolean }) {
  const qc = useQueryClient();
  const { confirm } = useFeedback();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: ["options"] });
  const test = useMutation({
    mutationFn: () => api.post<{ ok: boolean; detail: string }>("/metaculus/test"),
    onSuccess: setResult,
  });
  const save = useMutation({
    mutationFn: async (token: string) => {
      await api.put(`/keys/${ENV}`, { value: token });
      return api.post<{ ok: boolean; detail: string }>("/metaculus/test");
    },
    onSuccess: (check) => {
      setResult(check);
      refresh();
      if (check.ok) {
        setEditing(false);
        setValue("");
      }
    },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/keys/${ENV}`),
    onSuccess: () => {
      setResult(null);
      refresh();
    },
  });

  return (
    <div className="rounded-xl bg-surface-2 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Token de Metaculus</span>
        <span className="ml-auto">
          {options.metaculus ? <Badge tone="good">token {options.metaculus_hint}</Badge> : <Badge>sin token</Badge>}
        </span>
      </div>
      <p className="mt-0.5 text-xs text-muted">Opcional y gratis: suma las previsiones de la comunidad de Metaculus.</p>
      {editing ? (
        <form
          className="mt-2 flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) save.mutate(value.trim());
          }}
        >
          <input
            className={cx(inputClass, "min-w-0 flex-1")}
            type="password"
            autoComplete="off"
            placeholder="Pega aquí tu token"
            aria-label="Token de Metaculus"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
          <Button type="submit" disabled={!value.trim() || save.isPending}>
            {save.isPending ? "Probando…" : "Guardar"}
          </Button>
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
            Cancelar
          </Button>
          <a className="w-full text-xs text-accent underline" href={METACULUS_TOKEN_URL} target="_blank" rel="noreferrer">
            ¿No tienes token? Consíguelo aquí
          </a>
        </form>
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={!local}
            title={local ? undefined : LOCAL_ONLY_NOTE}
            onClick={() => {
              setResult(null);
              setEditing(true);
            }}
          >
            {options.metaculus ? "Cambiar token" : "Añadir token"}
          </Button>
          {options.metaculus && (
            <Button variant="secondary" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Probando…" : "Probar"}
            </Button>
          )}
          {options.metaculus && local && (
            <Button
              variant="ghost"
              className="hover:text-bad"
              onClick={async () => {
                if (
                  await confirm({
                    title: "¿Quitar el token de Metaculus?",
                    body: "Oraqlo dejará de consultar Metaculus; Polymarket y Manifold siguen.",
                    confirm: "Quitar",
                    danger: true,
                  })
                )
                  remove.mutate();
              }}
            >
              Quitar
            </Button>
          )}
        </div>
      )}
      {result && (
        <Notice tone={result.ok ? "good" : "warn"} className="mt-2">
          {result.detail}
        </Notice>
      )}
      <ErrorNote error={save.error ?? test.error ?? remove.error} />
    </div>
  );
}
