/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#060609',
        carbon: '#060609',
        panel: '#0a0f1e',
        accent: '#38e1ff',
        gold: '#f59e0b',
        crimson: '#e62429',
        hud: '#7de9ff',
      },
    },
  },
  plugins: [],
};
