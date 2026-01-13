/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './azurerbac/web/templates/**/*.html',
    './azurerbac/web/static/js/**/*.js',
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
