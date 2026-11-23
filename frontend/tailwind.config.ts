import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#17221f",
        paper: "#f5f2eb",
        teal: "#177c72",
        coral: "#d76a4a",
      },
      boxShadow: {
        panel: "0 18px 50px rgba(24, 38, 34, 0.10)",
      },
    },
  },
  plugins: [],
} satisfies Config;
