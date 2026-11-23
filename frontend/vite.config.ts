import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

declare const process: {
  platform: string;
};

const target = process.platform === "darwin" ? "safari16" : "chrome105";

export default defineConfig({
  plugins: [react()],
  build: {
    target,
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
  },
});
