/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    base: "/admin-ui/",
    plugins: [react()],
    server: {
        port: 5173,
        proxy: {
            "/admin": "http://127.0.0.1:10123",
            "/health": "http://127.0.0.1:10123"
        }
    },
    test: {
        environment: "node",
        include: ["src/**/*.test.ts", "src/**/*.test.tsx"]
    }
});
