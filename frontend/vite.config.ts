import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const devServerPort = Number(process.env.VITE_PORT ?? 5173);
const backendUrl = process.env.VITE_BACKEND_URL ?? "http://localhost:8000";

export default defineConfig({
  root: "frontend",
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    minify: false,
    sourcemap: true,
  },
  server: {
    port: devServerPort,
    strictPort: true,
    proxy: {
      "/api": backendUrl,
      "/mcp": backendUrl,
    },
  },
  test: {
    exclude: ["e2e/**", "**/node_modules/**", "**/.git/**"],
  },
});
