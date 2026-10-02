import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from 'react-hot-toast';

import App from '@/App';
import { queryClient } from '@/lib/queryClient';
import { watchSystemTheme } from '@/store/theme';
import '@/index.css';

watchSystemTheme();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      {/* Top centre: where people look first, and clear of the Submit and
          Cancel buttons that sit at the bottom of forms on a phone. */}
      <Toaster
        position="top-center"
        gutter={8}
        containerStyle={{ top: 12 }}
        toastOptions={{
          style: {
            background: 'rgb(var(--overlay))',
            color: 'rgb(var(--text))',
            border: '1px solid rgb(var(--border))',
            boxShadow: 'var(--shadow-lg)',
            borderRadius: 'var(--radius)',
            fontSize: '0.875rem',
            maxWidth: '28rem',
          },
          success: {
            duration: 3500,
            iconTheme: { primary: 'rgb(var(--success))', secondary: 'rgb(var(--overlay))' },
            style: { borderLeft: '4px solid rgb(var(--success))' },
          },
          error: {
            duration: 6000,
            iconTheme: { primary: 'rgb(var(--danger))', secondary: 'rgb(var(--overlay))' },
            style: { borderLeft: '4px solid rgb(var(--danger))' },
            ariaProps: { role: 'alert', 'aria-live': 'assertive' },
          },
        }}
      />
    </QueryClientProvider>
  </StrictMode>,
);
