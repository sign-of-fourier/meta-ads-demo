import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const port = parseInt(env.VITE_PORT) || 5173;
  const backendUrl = env.VITE_BACKEND_URL || "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port,
      allowedHosts: ["unlustred-mattie-intertergal.ngrok-free.dev"],
      proxy: {
        "/api": backendUrl,
        "/auth/signup": backendUrl,
        "/auth/login": backendUrl,
        "/auth/meta": backendUrl,
        "/auth/google": backendUrl,
        "/me": backendUrl,
        "/images": backendUrl,
        "/ad-images": backendUrl,
      },
      headers: {
        "ngrok-skip-browser-warning": "true",
      },
    },
  };
});
