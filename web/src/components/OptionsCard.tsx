import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type OptionsView } from "../lib/api";
import { LOCAL_ONLY_NOTE, useSetup } from "../lib/setup";
import { Card, ErrorNote, Notice, SectionTitle, Skeleton, Toggle, inputClass } from "./ui";

function NumberField({
  value,
  label,
  min,
  max,
  suffix,
  disabled,
  onSave,
}: {
  value: number;
  label: string;
  min: number;
  max: number;
  suffix: string;
  disabled: boolean;
  onSave: (value: number) => void;
}) {
  return (
    <label className="flex flex-wrap items-center gap-2 text-sm">
      <span className="min-w-40">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        disabled={disabled}
        defaultValue={value}
        className={`${inputClass} w-24`}
        onBlur={(e) => {
          const next = Number(e.target.value);
          if (Number.isFinite(next) && next >= min && next <= max && next !== value) onSave(next);
        }}
      />
      <span className="text-muted">{suffix}</span>
    </label>
  );
}

/** Cómo predice Oraqlo (voces extra del comité) y qué vigila por su cuenta. */
export default function OptionsCard() {
  const qc = useQueryClient();
  const setup = useSetup();
  const local = !!setup.data?.local;
  const view = useQuery({
    queryKey: ["options"],
    queryFn: () => api.get<OptionsView>("/options"),
  });
  const save = useMutation({
    mutationFn: (changes: Partial<OptionsView>) => api.put<OptionsView>("/options", changes),
    onSuccess: (data) => qc.setQueryData(["options"], data),
  });
  const o = view.data;

  return (
    <Card id="prediccion">
      <SectionTitle>Predicción y vigilancia</SectionTitle>
      {view.isLoading || !o ? (
        view.error ? (
          <ErrorNote error={view.error} onRetry={() => view.refetch()} />
        ) : (
          <Skeleton className="h-24" />
        )
      ) : (
        <div className="space-y-4">
          {!local && <Notice tone="info">{LOCAL_ONLY_NOTE}</Notice>}
          <div className="space-y-2">
            <Toggle
              checked={o.markets}
              onChange={(v) => local && save.mutate({ markets: v })}
              label="Usar mercados de predicción como una voz más"
            />
            <p className="text-xs text-muted">
              Si en {o.metaculus ? "Polymarket, Manifold o Metaculus" : "Polymarket o Manifold"} se apuesta sobre exactamente lo mismo, su precio entra
              en el comité con el peso que se gane acertando. Solo lectura: no se apuesta nada.
              {!o.metaculus && " Para Metaculus, añade METACULUS_TOKEN al .env."}
            </p>
            <Toggle
              checked={o.decompose}
              onChange={(v) => local && save.mutate({ decompose: v })}
              label="Estimar por partes las preguntas compuestas"
            />
            <Toggle
              checked={o.reference}
              onChange={(v) => local && save.mutate({ reference: v })}
              label="Recordar preguntas parecidas ya resueltas"
            />
          </div>
          <div className="space-y-2 border-t border-line pt-4">
            <Toggle
              checked={o.watch}
              onChange={(v) => local && save.mutate({ watch: v })}
              label="Vigilar lo nuevo: revisar las noticias y avisar si algo cambia"
            />
            {!o.web_search && (
              <Notice tone="warn">Vigilar necesita la búsqueda web (la clave de ollama.com en «Búsqueda web»).</Notice>
            )}
            <NumberField
              value={o.watch_interval_hours}
              label="Revisar cada"
              min={1}
              max={720}
              suffix="horas"
              disabled={!local || save.isPending}
              onSave={(v) => save.mutate({ watch_interval_hours: v })}
            />
            <NumberField
              value={o.watch_daily_budget}
              label="Como mucho"
              min={0}
              max={100}
              suffix="revisiones al día"
              disabled={!local || save.isPending}
              onSave={(v) => save.mutate({ watch_daily_budget: v })}
            />
            <NumberField
              value={o.watch_alert_points}
              label="Avisar si se mueve"
              min={1}
              max={100}
              suffix="puntos o más"
              disabled={!local || save.isPending}
              onSave={(v) => save.mutate({ watch_alert_points: v })}
            />
          </div>
          <ErrorNote error={save.error} />
        </div>
      )}
    </Card>
  );
}
