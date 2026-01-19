import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/getToken": "http://localhost:8080",
      "/dg": {
        target: "http://localhost:8080",
        ws: true,
        changeOrigin: true,
      },
      "/llm": "http://localhost:8080",
      "/tts": "http://localhost:8080",
    },
  },
});
