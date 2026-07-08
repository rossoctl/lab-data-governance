import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { BrowserRouter } from 'react-router-dom';

import App from './App';

// PatternFly base styles + dark theme (data-governance keeps the dark palette
// the vanilla shell used). See src/styles/global.css for the theme toggle.
import '@patternfly/react-core/dist/styles/base.css';
import './styles/global.css';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

// basename="/ui" — the SPA is served under /ui/ by the Python backend
// (ADR-0019), so React Router treats /ui as the app root and deep links like
// /ui/traces/{tid} resolve to the in-app route /traces/{tid}.
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename="/ui">
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
