/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './azurerbac/web/templates/**/*.html',
    './azurerbac/web/static/js/**/*.js',
    // Tailwind class strings also live in Python constants (e.g. the
    // decommission banner message in ``azurerbac/web/constants.py``).
    // Including the file in ``content`` lets the JIT see those classes
    // and ship the corresponding rules.
    './azurerbac/web/constants.py',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        azure: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#0078d4',
          600: '#0066b8',
          700: '#005a9e',
          800: '#004578',
          900: '#003356',
        },
      },
    },
  },
  plugins: [],
}
