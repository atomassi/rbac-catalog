/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './rbaccatalog/web/templates/**/*.html',
    './rbaccatalog/web/static/js/**/*.js',
  ],
  // Some utility classes only appear inside Python string literals
  // (e.g. the decommission banner HTML in ``rbaccatalog/web/constants.py``)
  // and the JIT can't see them through the ``content`` globs. Scanning
  // the whole Python file would also emit utilities for unrelated tokens
  // (URLs, CSP domains, etc.) and bloat the bundle, so we list the
  // referenced classes explicitly here.
  safelist: [
    'underline',
    'font-medium',
    'hover:no-underline',
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
