import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import ConnectionCard from "../components/ConnectionCard";
import { LOCAL_ONLY_NOTE, useSetup } from "../components/ProvidersCard";
import { ImportCard } from "../components/SystemCards";
import { Button, Card, ErrorNote, SectionTitle, cx, inputClass } from "../components/ui";
import { api, PROVIDER_INFO, providerLabel, type Check, type ProvidersView } from "../lib/api";

const FEATURED = ["anthropic", "openai", "gemini", "openrouter", "ollama-local"];
const OTHERS = ["ollama-cloud", "mistral", "xai", "deepseek", "groq", "lmstudio"];

function Steps({ step }: { step: number }) {
  return (
    <ol className="mb-4 flex gap-2" aria-label="Pasos">
      {["Modelo", "Clave", "Listo"].map((label, i) => (
        <li
          key={label}
          className={cx("flex-1 rounded-full py-1 text-center text-xs", i <= step ? "bg-accent/15 text-accent" : "bg-surface-2 text-muted")}
          aria-current={i === step ? "step" : undefined}
        >
          {i + 1}. {label}
        </li>
      ))}
    </ol>
  );
}

export default function Welcome() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const setup = useSetup();
  const providers = useQuery({ queryKey: ["providers"], queryFn: () => api.get<ProvidersView>("/providers") });
  const [step, setStep] = useState(0);
  const [choice, setChoice] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const [key, setKey] = useState("");
  const [result, setResult] = useState<Check | null>(null);
  const local = setup.data?.local ?? true;

  const entry = providers.data?.providers.find((p) => p.name === choice);
  const needsKey = !!entry?.api_key_env;
  // Sin clave (Ollama, LM Studio): se elige uno de los modelos que ya tiene descargados.
  const models = useQuery({
    queryKey: ["models", choice],
    queryFn: () => api.get<{ models: string[] }>(`/providers/${choice}/models`),
    enabled: step === 1 && !!entry && !needsKey,
    retry: false,
  });
  const [model, setModel] = useState("");
  const pickedModel = model || models.data?.models[0] || null;

  const connect = useMutation({
    mutationFn: async () => {
      if (!entry) throw new Error("Elige un proveedor.");
      if (needsKey && key.trim()) await api.put(`/keys/${entry.api_key_env}`, { value: key });
      await api.put("/config", { roles: { default: { provider: entry.name, model: needsKey ? null : pickedModel } } });
      return api.post<Check>(`/providers/${entry.name}/test`);
    },
    onSuccess: (check) => {
      setResult(check);
      qc.invalidateQueries({ queryKey: ["providers"] });
      qc.invalidateQueries({ queryKey: ["setup"] });
      if (check.ok) setStep(2);
    },
  });
  const skip = useMutation({
    mutationFn: () => api.post("/setup/skip"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["setup"] });
      navigate("/");
    },
  });
  const finish = () => {
    qc.invalidateQueries({ queryKey: ["setup"] });
    navigate("/");
  };

  return (
    <div className="space-y-4">
      <Card>
        <Steps step={step} />
        {step === 0 && (
          <>
            <h2 className="text-lg font-semibold">Te damos la bienvenida a Oraqlo</h2>
            <p className="mt-1 mb-4 text-sm text-muted">
              Oraqlo piensa con un modelo de inteligencia artificial. Elige cuál usar: puedes cambiarlo cuando quieras en
              Ajustes, incluso uno distinto para cada rol del debate.
            </p>
            {!local && <p className="mb-3 rounded-xl bg-warn/10 p-3 text-sm text-warn">{LOCAL_ONLY_NOTE}</p>}
            <div className="grid gap-2 sm:grid-cols-2">
              {[...FEATURED, ...(more ? OTHERS : [])].map((name) => (
                <button
                  key={name}
                  type="button"
                  disabled={!local}
                  onClick={() => {
                    setChoice(name);
                    setResult(null);
                    setStep(1);
                  }}
                  className={cx(
                    "flex min-h-16 flex-col items-start justify-center rounded-xl border px-4 py-2 text-left transition disabled:opacity-50",
                    choice === name ? "border-accent bg-accent/10" : "border-line hover:border-accent",
                  )}
                >
                  <span className="font-medium">{providerLabel(name)}</span>
                  {PROVIDER_INFO[name]?.note && <span className="text-xs text-muted">{PROVIDER_INFO[name].note}</span>}
                </button>
              ))}
            </div>
            {!more && (
              <Button variant="ghost" className="mt-2 px-0" onClick={() => setMore(true)}>
                Ver más opciones
              </Button>
            )}
          </>
        )}

        {step === 1 && entry && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              connect.mutate();
            }}
          >
            <h2 className="text-lg font-semibold">{providerLabel(entry.name)}</h2>
            {needsKey ? (
              <>
                <p className="mt-1 mb-3 text-sm text-muted">
                  Pega tu clave de API. Se guarda solo en este PC y no se vuelve a mostrar completa.
                  {PROVIDER_INFO[entry.name]?.keyUrl && (
                    <>
                      {" "}
                      <a className="text-accent underline" href={PROVIDER_INFO[entry.name].keyUrl} target="_blank" rel="noreferrer">
                        Consigue tu clave aquí
                      </a>
                      .
                    </>
                  )}
                </p>
                <input
                  className={inputClass}
                  type="password"
                  autoComplete="off"
                  autoFocus
                  placeholder={entry.key_present ? `Ya hay una guardada (${entry.key_hint}); pega otra para cambiarla` : "Pega aquí tu clave"}
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  aria-label="Clave de API"
                />
              </>
            ) : (
              <>
                <p className="mt-1 mb-3 text-sm text-muted">
                  No necesita clave. Asegúrate de tenerlo abierto en este PC con al menos un modelo descargado.
                </p>
                {models.data?.models.length ? (
                  <label className="grid gap-1 text-sm">
                    <span className="text-muted">Modelo</span>
                    <select className={inputClass} value={pickedModel ?? ""} onChange={(e) => setModel(e.target.value)}>
                      {models.data.models.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : models.isError || models.data ? (
                  <p className="rounded-xl bg-warn/10 p-3 text-sm text-warn">
                    No encuentro modelos. Abre {providerLabel(entry.name)} y descarga uno; luego vuelve a intentarlo.
                  </p>
                ) : (
                  <p className="text-sm text-muted">Buscando tus modelos…</p>
                )}
              </>
            )}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                type="submit"
                disabled={connect.isPending || (needsKey ? !key.trim() && !entry.key_present : !pickedModel)}
              >
                {connect.isPending ? "Probando…" : "Conectar y probar"}
              </Button>
              <Button type="button" variant="ghost" onClick={() => setStep(0)}>
                Atrás
              </Button>
            </div>
            {result && !result.ok && (
              <p className="mt-3 rounded-xl border border-bad/30 bg-bad/10 p-3 text-sm text-bad">
                {result.detail} {result.fix}
              </p>
            )}
            <ErrorNote error={connect.error} />
          </form>
        )}

        {step === 2 && (
          <>
            <h2 className="text-lg font-semibold">¡Listo! Oraqlo ya puede pensar.</h2>
            <p className="mt-1 mb-4 text-sm text-muted">
              Conectado con {providerLabel(choice ?? "")}. {result?.detail} Dos cosas opcionales antes de empezar:
            </p>
            <Button onClick={finish}>Empezar a preguntar</Button>
          </>
        )}
      </Card>

      {step === 2 && (
        <>
          <ConnectionCard />
          <Card>
            <SectionTitle>¿Vienes de la versión anterior?</SectionTitle>
            <ImportCard compact />
          </Card>
        </>
      )}

      {step < 2 && (
        <p className="text-center text-sm">
          <Button variant="ghost" disabled={skip.isPending} onClick={() => skip.mutate()}>
            Configurarlo más tarde
          </Button>
        </p>
      )}
    </div>
  );
}
