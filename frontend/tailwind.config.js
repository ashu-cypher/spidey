/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        void: '#030712',
        carbon: '#050B14',
        panel: '#0A1220',
        accent: '#00f0ff',
        gold: '#f59e0b',
        crimson: '#ef4444',
        hud: '#67e8f9',
      },
    },
  },
  plugins: [],
};
