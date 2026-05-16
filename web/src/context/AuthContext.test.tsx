import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { AuthProvider, useAuth } from './AuthContext';

// Mock the auth API
vi.mock('../api/auth', () => ({
  authApi: {
    me: vi.fn(),
    logout: vi.fn(),
  },
}));

import { authApi } from '../api/auth';

function TestConsumer() {
  const { user, loading } = useAuth();
  return (
    <div>
      <span data-testid="loading">{String(loading)}</span>
      <span data-testid="user">{user?.email ?? 'null'}</span>
    </div>
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.mocked(authApi.me).mockReset();
  vi.mocked(authApi.logout).mockReset();
});

describe('AuthContext', () => {
  it('shows cached user immediately without blocking on /auth/me', async () => {
    // Seed cache
    localStorage.setItem('macro_cached_user', JSON.stringify({
      user_id: 1, email: 'test@example.com', username: 'test',
      first_name: 'Test', last_name: null, avatar_url: null,
      google_linked: false,    }));

    // /auth/me is slow
    vi.mocked(authApi.me).mockImplementation(() => new Promise(() => {}));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    // User is available immediately from cache - no loading spinner
    expect(screen.getByTestId('loading').textContent).toBe('false');
    expect(screen.getByTestId('user').textContent).toBe('test@example.com');
  });

  it('shows loading spinner when no cache exists', async () => {
    // No cache, /auth/me slow
    vi.mocked(authApi.me).mockImplementation(() => new Promise(() => {}));

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    expect(screen.getByTestId('loading').textContent).toBe('true');
    expect(screen.getByTestId('user').textContent).toBe('null');
  });

  it('updates user when /auth/me resolves', async () => {
    vi.mocked(authApi.me).mockResolvedValue({
      user_id: 2, email: 'fresh@example.com', username: 'fresh',
      first_name: 'Fresh', last_name: null, avatar_url: null,
      google_linked: true,    });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    await waitFor(() => {
      expect(screen.getByTestId('user').textContent).toBe('fresh@example.com');
    });
    // `loading` flips to false only after the side-effect awaits in
    // checkAuth settle (timezone PUT + push import), which happens on a
    // later tick than the setUser(me) paint.
    await waitFor(() => {
      expect(screen.getByTestId('loading').textContent).toBe('false');
    });
  });

  it('clears user when /auth/me 401 clears cache (simulating client.ts interceptor)', async () => {
    // Seed cache
    localStorage.setItem('macro_cached_user', JSON.stringify({
      user_id: 1, email: 'old@example.com', username: 'old',
      first_name: null, last_name: null, avatar_url: null,
      google_linked: false,    }));

    Object.defineProperty(navigator, 'onLine', { value: true, writable: true });
    // Simulate what client.ts does on 401: clear localStorage, then throw
    vi.mocked(authApi.me).mockImplementation(async () => {
      localStorage.removeItem('macro_cached_user');
      throw new Error('Unauthorized');
    });

    render(
      <AuthProvider>
        <TestConsumer />
      </AuthProvider>
    );

    // Initially shows cached user
    expect(screen.getByTestId('user').textContent).toBe('old@example.com');

    // After auth check fails (and interceptor clears cache), user is cleared
    await waitFor(() => {
      expect(screen.getByTestId('user').textContent).toBe('null');
    });
    expect(localStorage.getItem('macro_cached_user')).toBeNull();
  });
});
