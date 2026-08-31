import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// Built into the dashboard package, which is what serves it: one process, one port, no CDN.
// `/assets` is mounted by FastAPI; every other path falls through to index.html so the
// client router owns the URLs the Jinja pages used to.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": new URL("./src", import.meta.url).pathname } },
  build: { outDir: "../dashboard/static", emptyOutDir: true },
  server: {
    // Dev only. `npm run dev` proxies the API to a dashboard running the usual way.
    proxy: {
      "/api": "http://127.0.0.1:8080",
      "/export.csv": "http://127.0.0.1:8080",
    },
  },
})
