import { useQuery } from "@tanstack/react-query";
import { api, type SetupStatus } from "./api";

/** ¿Hay un modelo listo? ¿Se está usando desde el propio PC? */
export function useSetup() {
  return useQuery({ queryKey: ["setup"], queryFn: () => api.get<SetupStatus>("/setup") });
}

export const LOCAL_ONLY_NOTE = "Solo se puede cambiar desde el PC donde corre Oraqlo.";
