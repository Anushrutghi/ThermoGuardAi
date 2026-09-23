import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies API + WebSocket to the FastAPI backend on :8000
export default defineConfig({
  plugins: [react()],
  build: {
    // Keep the heavy charting lib out of the main chunk (S3 §29 performance).
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      // The live-inspection WebSocket is /api/v1/ws/inspect, so the /api rule
      // (which matches it) must enable ws:true — not just the /ws rule.
      // Use 127.0.0.1 (not "localhost") to avoid IPv6/IPv4 mismatch on macOS.
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: true, ws: true },
      "/health": { target: "http://127.0.0.1:8000", changeOrigin: true },
      "/ws": { target: "ws://127.0.0.1:8000", ws: true },
    },
  },
});
