import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    // 5173 (Vite default) is inside a Windows winnat/Hyper-V reserved TCP range
    // on some machines (EACCES on bind). 5050 sits in a free gap. Override per
    // run with `npm run dev -- --port <n>`, or set VITE_DEV_PORT, if 5050 is
    // ever reserved too (e.g. another local project already owns it).
    port: Number(process.env.VITE_DEV_PORT) || 5050,
    proxy: {
      "/api": {
        // Proxy target follows the backend. Override with VITE_API_TARGET when
        // the backend runs on a non-default port to avoid local port clashes.
        target: process.env.VITE_API_TARGET || "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  }
});
