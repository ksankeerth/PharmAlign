import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development Vite forwards /api to the server.
const api = process.env.PHARMALIGN_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    proxy: { "/api": api, "/health": api },
  },
  preview: { port: 5181, proxy: { "/api": api, "/health": api } },
  build: { chunkSizeWarningLimit: 900 },
});
