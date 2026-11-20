import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The client calls /api/v1 on the Java server (STUDIO_API overrides the target).
const api = process.env.STUDIO_API || "http://127.0.0.1:8800";
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    proxy: {
      "/api/v1": { target: api, ws: true },
    },
  },
  build: { chunkSizeWarningLimit: 1600 },
});
