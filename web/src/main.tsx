import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { bootstrapPreferences } from './features/preferences/settings';
import './styles.css';

// 首绘前同步引导主题/密度，避免深色模式首绘闪烁。
bootstrapPreferences();

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
