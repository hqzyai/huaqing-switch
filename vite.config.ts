import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { codeInspectorPlugin } from "code-inspector-plugin";

export default defineConfig(({ command, mode }) => ({
  root: mode === "server" ? "src/server" : "src",
  plugins: [
    command === "serve" &&
      mode !== "server" &&
      codeInspectorPlugin({
        bundler: "vite",
      }),
    react(),
  ].filter(Boolean),
  base: mode === "server" ? "/" : "./",
  define: {
    "import.meta.env.VITE_HQ_SERVER": JSON.stringify(mode === "server"),
  },
  build: {
    outDir: mode === "server" ? "../../dist-server" : "../dist",
    emptyOutDir: true,
    rollupOptions: {
      // 托盘面板是单独的入口，不加载主界面
      input:
        mode === "server"
          ? path.resolve(__dirname, "src/server/index.html")
          : {
              main: path.resolve(__dirname, "src/index.html"),
              "tray-panel": path.resolve(__dirname, "src/tray-panel.html"),
            },
    },
  },
  server: {
    port: 3000,
    strictPort: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  clearScreen: false,
  envPrefix: ["VITE_", "TAURI_"],
}));
