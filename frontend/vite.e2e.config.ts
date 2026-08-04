import react from "@vitejs/plugin-react";
import {defineConfig} from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5174,
    proxy: {
      "/api": "http://127.0.0.1:8001",
      "/healthz": "http://127.0.0.1:8001",
      "/readyz": "http://127.0.0.1:8001"
    }
  }
});
