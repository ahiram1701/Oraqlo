import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { VitePWA } from "vite-plugin-pwa";

// El build va DENTRO del paquete Python: el wheel de cada release lleva
// backend + frontend juntos (un solo artefacto para el auto-updater).
export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      strategies: "injectManifest",
      srcDir: "src",
      filename: "sw.ts",
      registerType: "autoUpdate",
      injectRegister: false,
      manifest: {
        name: "Oraqlo",
        short_name: "Oraqlo",
        description: "Motor de estrategia bajo incertidumbre: previsiones calibradas, debate adversarial y seguimiento de objetivos.",
        lang: "es",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#0b0d12",
        theme_color: "#0b0d12",
        icons: [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
        ],
      },
      injectManifest: { globPatterns: ["**/*.{js,css,html,png,svg,woff2}"] },
    }),
  ],
  build: {
    outDir: "../src/oraqlo/api/static",
    emptyOutDir: true,
    rolldownOptions: {
      output: {
        // Gráficos y Markdown solo se descargan al abrir las pantallas que los usan.
        // React va aparte (y con más prioridad): si no, acabaría dentro del primer
        // grupo que lo usa y la pantalla inicial tendría que descargar los gráficos.
        codeSplitting: {
          groups: [
            { name: "vendor", test: /node_modules[\\/](react|react-dom|scheduler|react-router|react-router-dom|@tanstack)[\\/]/, priority: 30 },
            { name: "charts", test: /node_modules[\\/](recharts|d3-|victory-vendor|decimal\.js)/, priority: 20 },
            {
              name: "markdown",
              test: /node_modules[\\/](react-markdown|remark-|mdast-|micromark|unified|hast-|unist-|vfile|property-information)/,
              priority: 10,
            },
          ],
        },
      },
    },
  },
  server: { proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: false } } },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
