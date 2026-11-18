// La misma explicación de las cifras en Práctica y en Calibración.

export function HowToRead() {
  return (
    <details className="mt-3 text-xs text-muted">
      <summary className="cursor-pointer select-none">¿Cómo leer estas cifras?</summary>
      <ul className="mt-2 list-disc space-y-1 pl-4">
        <li>
          <b className="text-fg">Frente al azar</b>: cuánto mejor lo hace que repartir la probabilidad por igual entre las opciones.
          0% es como tirar una moneda; cuanto más alto, mejor.
        </li>
        <li>
          <b className="text-fg">Error (Brier)</b>: el mismo dato en bruto. 0 es perfecto y menos es mejor; en una pregunta de sí/no
          el azar saca 0.50.
        </li>
        <li>
          <b className="text-fg">Duelo</b>: una pregunta de Polymarket en la que Oraqlo predijo sin ver el precio del mercado. Gana
          quien se acercó más a lo que pasó. No cuentan las que ya se sabían cuando Oraqlo predijo.
        </li>
        <li>
          Calibración con el tipo «Práctica» cuenta exactamente los mismos duelos que la pantalla de Práctica, así que la cifra de
          Oraqlo coincide. Con «Todo» suma también tus predicciones.
        </li>
      </ul>
    </details>
  );
}
