import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build goes into the Python package, which serves it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "../src/talkviz/web", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8321" } },
});
