/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#e9f0fc',
          100: '#d4e2f7',
          200: '#a9c4ee',
          300: '#7ea6e4',
          400: '#3b74d6',
          500: '#2450a4',
          600: '#1f3b73',
          700: '#14264f',
          800: '#0f1c3a',
          900: '#0a1228',
        },
      },
      fontFamily: {
        sans: ['"Microsoft YaHei UI"', '"Microsoft YaHei"', '"PingFang SC"', '"Segoe UI"', 'sans-serif'],
      },
      borderRadius: {
        xl: '14px',
        '2xl': '16px',
      },
    },
  },
  plugins: [],
}
