import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/docs": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/openapi.json": { target: "http://127.0.0.1:8000", changeOrigin: false },
      "/redoc": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
  preview: {
    port: 4173,
  },
});
