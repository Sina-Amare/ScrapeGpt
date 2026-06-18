import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        porcelain: "var(--c-porcelain)",
        surface: "var(--c-surface)",
        ink: "var(--c-ink)",
        muted: "var(--c-muted)",
        line: "var(--c-line)",
        body: "var(--c-body)",
        // Brand accent — electric blue. Components use "teal" class names (historical alias).
        teal: {
          DEFAULT: "#2272FF",
          dark:    "#1A5FE8",
          soft:    "var(--c-teal-soft)",
          subtle:  "#BFDBFE",
        },
        accent: "#2272FF",
        success: "#15803D",
        warning: "#B45309",
        danger: "#B91C1C",
      },
      boxShadow: {
        panel: "var(--shadow-panel)",
      },
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "sans-serif"],
        mono: ["Geist Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      animation: {
        "float-slow":   "float-slow 7s ease-in-out infinite",
        "float-medium": "float-medium 5s ease-in-out infinite",
      },
      keyframes: {
        "float-slow": {
          "0%, 100%": { transform: "translateY(0px) scale(1)" },
          "50%":       { transform: "translateY(-22px) scale(1.04)" },
        },
        "float-medium": {
          "0%, 100%": { transform: "translateY(0px) rotate(0deg)" },
          "50%":       { transform: "translateY(-14px) rotate(5deg)" },
        },
      },
    },
  },
  plugins: [],
} satisfies Config;
