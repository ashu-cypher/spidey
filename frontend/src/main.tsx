import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { App } from './App';
import { applyAppearance } from './components/SettingsSections';

// Apply persisted appearance prefs (reduce motion / compact chat) before
// the first paint so the app never flashes the wrong mode.
applyAppearance();

const root = document.getElementById('root');
if (!root) throw new Error('Root element #root not found');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
