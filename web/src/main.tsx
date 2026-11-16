import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { registerSW } from "virtual:pwa-register";
import App from "./App";
import { FeedbackProvider } from "./components/ui";
import { applyTheme } from "./lib/theme";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, retry: 1, refetchOnWindowFocus: true } },
});

// La app se actualiza sola cuando el servidor sirve una versión nueva (auto-update).
registerSW({ immediate: true });
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
