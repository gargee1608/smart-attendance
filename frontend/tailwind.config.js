/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1c1915",
        paper: "#f6f1e6",
        card: "#fffaf2",
        line: "#e4d8c4",
        pine: "#243028",
        clay: "#9a3b1a",
        moss: "#1f6b4a",
        amber: "#8a5a12",
      },
      fontFamily: {
        serif: ["Fraunces", "Georgia", "serif"],
        sans: ["Outfit", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
