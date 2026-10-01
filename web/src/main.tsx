import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';

import App from '@/App';
import { watchSystemTheme } from '@/store/theme';
import '@/index.css';

watchSystemTheme();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster
        position="bottom-right"
        toastOptions={{
          style: {
            background: 'rgb(var(--overlay))',
            color: 'rgb(var(--text))',
            border: '1px solid rgb(var(--border))',
            boxShadow: 'var(--shadow-lg)',
            borderRadius: 'var(--radius)',
            fontSize: '0.875rem',
          },
        }}
      />
    </QueryClientProvider>
  </StrictMode>,
);
