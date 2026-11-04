import React from 'react';
import { createRoot } from 'react-dom/client';
import { AdminApp } from './AdminApp';
import './admin.css';
import { applyAdminTheme, loadTheme } from './adminTheme';

applyAdminTheme(loadTheme());  // the saved look is applied before the first paint

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AdminApp />
  </React.StrictMode>,
);
