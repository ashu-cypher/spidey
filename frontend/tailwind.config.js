/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#05070f',
        panel: '#0b0f1c',
        accent: '#22d3ee',
        accent2: '#a78bfa',
      },
    },
  },
  plugins: [],
};
