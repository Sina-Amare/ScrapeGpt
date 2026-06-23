import type { Config } from "tailwindcss";

export default {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        porcelain: "var(--c-porcelain)",
        surface: "var(--c-surface)",
        surface2: "var(--c-surface-2)",
        ink: "var(--c-ink)",
        muted: "var(--c-muted)",
        line: "var(--c-line)",
        body: "var(--c-body)",
        glass: "var(--c-glass)",
        glassline: "var(--c-glass-border)",
        // Signal accent — amber (dark) / copper (light). Components use the
        // historical "teal" alias; both now resolve to the accent variable.
        teal: {
          DEFAULT: "var(--c-accent)",
          dark:    "var(--c-accent-strong)",
          soft:    "var(--c-accent-soft)",
          subtle:  "var(--c-accent)",
        },
        accent: "var(--c-accent)",
        onaccent: "var(--c-accent-contrast)",
        // Solid "strong" surface — ink (light) / amber (dark). High-contrast in
        // both modes; used for primary buttons and selected chips.
        primary: "var(--c-primary-bg)",
        onprimary: "var(--c-primary-fg)",
        success: "var(--c-success)",
        warning: "var(--c-warning)",
        danger: "var(--c-danger)",
      },
      boxShadow: {
        panel: "var(--shadow-panel)",
        glass: "var(--shadow-glass)",
        glow: "var(--shadow-glow)",
      },
      backdropBlur: {
        xs: "2px",
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
