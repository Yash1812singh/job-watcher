import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The backend runs on :8090; proxy /api so the browser talks to one origin.
// Two pages: Job Watcher (/) and Fill with AI (/fill/).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8090" },
  },
  build: {
    rollupOptions: {
      input: { main: "index.html", fill: "fill/index.html" },
    },
  },
});
