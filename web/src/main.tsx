import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import { setRegistration } from "./lib/appVersion";
import { FeedbackProvider } from "./components/ui";
import { applyTheme } from "./lib/theme";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: true } },
});

// La app se actualiza sola cuando el servidor sirve una versión nueva (auto-update).
// El navegador solo busca un service worker nuevo al cargar la página; esta SPA casi
// nunca se recarga (y la ventana de escritorio vive en la bandeja), así que se pide
// a menudo y al volver a la app.
registerSW({
  immediate: true,
  onRegisteredSW(_url, reg) {
    if (!reg) return;
    setRegistration(reg);
    const check = () => void reg.update().catch(() => undefined);
    setInterval(check, 30 * 60_000);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") check();
    });
  },
});
applyTheme();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <FeedbackProvider>
          <App />
        </FeedbackProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
