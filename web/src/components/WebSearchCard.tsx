import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Check, type ProvidersView } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Badge, Button, Card, ErrorNote, SectionTitle, cx, inputClass, useFeedback } from "./ui";

export interface OllamaStatus {
  local: boolean;
  key_present: boolean;
  key_hint: string | null;
  key_env: string;
  web_search: boolean;
}

/** Ollama en una pantalla: el del PC, la clave de ollama.com y la búsqueda web. */
export function useOllamaStatus() {
  return useQuery({ queryKey: ["ollama"], queryFn: () => api.get<OllamaStatus>("/ollama"), staleTime: 30_000 });
}

function Row({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <li className="flex items-start gap-2 py-1.5 text-sm">
      <span aria-hidden className={cx("mt-0.5 font-semibold", ok ? "text-good" : "text-muted")}>
        {ok ? "✓" : "–"}
      </span>
      <span>
        <span className="font-medium">{label}</span> <span className="text-muted">· {detail}</span>
      </span>
    </li>
  );
}

export const WEB_SEARCH_KEY_URL = "https://ollama.com/settings/keys";
export const WEB_SEARCH_ANCHOR = "busqueda-web";

/** Estado de la clave de búsqueda web (es la misma con cualquier modelo de IA). */
export function useWebSearch() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  return { ...providers, webSearch: providers.data?.web_search };
}

/** Guarda la clave de búsqueda web y la prueba con una búsqueda real. */
export function useWebSearchKey(onDone?: (check: Check) => void) {
  const qc = useQueryClient();
  const { webSearch } = useWebSearch();
  const env = webSearch?.api_key_env ?? "OLLAMA_API_KEY";
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["providers"] });
    qc.invalidateQueries({ queryKey: ["ollama"] });
  };
  const save = useMutation({
    mutationFn: async (value: string) => {
      await api.put<ProvidersView>(`/keys/${env}`, { value });
      return api.post<Check>("/websearch/test");
    },
    onSuccess: (check) => {
      refresh();
      onDone?.(check);
    },
  });
  const remove = useMutation({ mutationFn: () => api.del(`/keys/${env}`), onSuccess: refresh });
  const test = useMutation({ mutationFn: () => api.post<Check>("/websearch/test") });
  return { save, remove, test };
}

/**
 * «Cuenta de Ollama»: una sola clave de ollama.com sirve para los modelos en la nube
 * (proveedor «Ollama Cloud») y para investigar en internet. Antes la pedían dos
 * tarjetas sin decir que era la misma, y quitarla en una apagaba la otra.
 */
export default function WebSearchCard() {
  const { webSearch, error } = useWebSearch();
  const status = useOllamaStatus();
  const { confirm } = useFeedback();
  const setup = useSetup();
  const local = setup.data?.local ?? false;
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [result, setResult] = useState<Check | null>(null);
  const { save, remove, test } = useWebSearchKey((check) => {
    setResult(check);
    if (check.ok) {
      setEditing(false);
      setValue("");
    }
  });
  const shown = test.data ?? result;

  return (
    <Card id={WEB_SEARCH_ANCHOR}>
      <SectionTitle
        aside={
          webSearch?.key_present ? (
            <Badge tone="good">clave {webSearch.key_hint}</Badge>
          ) : (
            <Badge tone="muted">sin clave</Badge>
          )
        }
      >
        Cuenta de Ollama
      </SectionTitle>
      <p className="text-sm text-muted">
        Una clave gratuita de ollama.com sirve para dos cosas: usar los <b>modelos en la nube</b> de Ollama sin instalar
        nada (proveedor «Ollama Cloud») e <b>investigar en internet</b> antes de responder, con cualquier modelo.
        {!local && ` ${LOCAL_ONLY_NOTE}`}
      </p>
      <ul className="mt-2">
        <Row
          ok={!!status.data?.local}
          label="Ollama en este PC"
          detail={
            status.data?.local
              ? "responde; con la sesión iniciada en la app de Ollama también usa modelos «:cloud»"
              : "no responde (opcional si usas la clave)"
          }
        />
        <Row
          ok={!!webSearch?.key_present}
          label="Modelos en la nube con clave"
          detail={webSearch?.key_present ? "listos (proveedor «Ollama Cloud»)" : "añade la clave"}
        />
        <Row
          ok={!!webSearch?.key_present}
          label="Investigar en internet"
          detail={webSearch?.key_present ? "activada" : "desactivada: falta la clave"}
        />
      </ul>

      {editing ? (
        <form
          className="mt-3 flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (value.trim()) save.mutate(value);
          }}
        >
          <input
            className={cx(inputClass, "min-w-0 flex-1")}
            type="password"
            autoComplete="off"
            placeholder="Pega aquí tu clave de ollama.com"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label="Clave de ollama.com"
            autoFocus
          />
          <Button type="submit" disabled={!value.trim() || save.isPending}>
            {save.isPending ? "Probando…" : "Guardar"}
          </Button>
          <Button type="button" variant="ghost" onClick={() => setEditing(false)}>
            Cancelar
          </Button>
          <a className="w-full text-xs text-accent underline" href={WEB_SEARCH_KEY_URL} target="_blank" rel="noreferrer">
            ¿No tienes clave? Créala gratis en ollama.com
          </a>
        </form>
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button
            variant="secondary"
            disabled={!local}
            title={local ? undefined : LOCAL_ONLY_NOTE}
            onClick={() => {
              setResult(null);
              test.reset();
              setEditing(true);
            }}
          >
            {webSearch?.key_present ? "Cambiar clave" : "Añadir clave"}
          </Button>
          {webSearch?.key_present && (
            <Button variant="secondary" disabled={test.isPending} onClick={() => test.mutate()}>
              {test.isPending ? "Probando…" : "Probar"}
            </Button>
          )}
          {webSearch?.key_present && local && (
            <Button
              variant="ghost"
              disabled={remove.isPending}
              onClick={async () => {
                const ok = await confirm({
                  title: "¿Quitar la clave de Ollama?",
                  body: "Se apagarán la búsqueda en internet y los modelos de «Ollama Cloud». Los modelos del Ollama de este PC siguen funcionando.",
                  confirm: "Quitar",
                  danger: true,
                });
                if (ok) remove.mutate();
              }}
            >
              Quitar
            </Button>
          )}
        </div>
      )}
      {shown && (
        <p className={cx("mt-2 text-sm", shown.ok ? "text-good" : "text-bad")}>
          {shown.ok ? "✓ " : "✗ "}
          {shown.detail} {!shown.ok && shown.fix}
        </p>
      )}
      <ErrorNote error={save.error ?? remove.error ?? test.error ?? error} />
    </Card>
  );
}
