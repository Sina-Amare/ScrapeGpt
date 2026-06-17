import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    // 5173 (Vite default) is inside a Windows winnat/Hyper-V reserved TCP range
    // on some machines (EACCES on bind). 5050 sits in a free gap. Override per
    // run with `npm run dev -- --port <n>` if 5050 is ever reserved too.
    port: 5050,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true
      }
    }
  }
});
