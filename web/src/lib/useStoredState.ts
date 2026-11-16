import { useEffect, useState } from "react";

/**
 * Como useState, pero recordado en este dispositivo (localStorage, JSON).
 * Para preferencias de pantalla —orden y filtros de una lista—, nunca para datos:
 * si el almacenamiento falla o está bloqueado, se usa el valor por defecto.
 */
export function useStoredState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw != null) {
        const parsed = JSON.parse(raw);
        // Un valor guardado de otra forma (versión anterior) no rompe la pantalla.
        if (typeof parsed === typeof initial) return parsed as T;
      }
    } catch {
      /* sin almacenamiento */
    }
    return initial;
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* sin almacenamiento */
    }
  }, [key, value]);
  return [value, setValue];
}
