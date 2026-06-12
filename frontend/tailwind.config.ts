import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        porcelain: "#F8F9FB",
        surface: "#FFFFFF",
        ink: "#0F172A",
        muted: "#64748B",
        line: "#E2E8F0",
        // Brand color is indigo — all components use "teal" class names so we
        // swap the values here rather than renaming across every file.
        teal: {
          DEFAULT: "#6366F1",
          dark: "#4F46E5",
          soft: "#EEF2FF",
          subtle: "#C7D2FE",
        },
        accent: "#6366F1",
        success: "#15803D",
        warning: "#B45309",
        danger: "#B91C1C",
      },
      boxShadow: {
        panel: "0 4px 24px -1px rgba(15, 23, 42, 0.07), 0 2px 6px -1px rgba(15, 23, 42, 0.04)",
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
