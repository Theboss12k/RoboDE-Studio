import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.STUDIO_API || "http://127.0.0.1:8800";
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: { proxy: { "/api": { target: api, ws: true } } },
  build: { chunkSizeWarningLimit: 1600 },
});
