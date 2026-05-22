/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"Helvetica Neue"',
          '"Hiragino Sans"',
          '"Yu Gothic"',
          'system-ui',
          'sans-serif',
        ],
      },
    },
  },
  plugins: [],
}
