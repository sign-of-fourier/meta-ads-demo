import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    allowedHosts: ["unlustred-mattie-intertergal.ngrok-free.dev"],
    proxy: {
      "/api": "http://localhost:8000",
      "/auth/signup": "http://localhost:8000",
      "/auth/login": "http://localhost:8000",
      "/auth/meta": "http://localhost:8000",
      "/me": "http://localhost:8000",
    },
    headers: {
      "ngrok-skip-browser-warning": "true",
    },
  },
});
