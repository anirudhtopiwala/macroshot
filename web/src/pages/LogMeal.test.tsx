import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Mock all heavy dependencies to keep tests fast and focused
vi.mock('../api/meals', () => ({
  mealsApi: {
    analyze: vi.fn(),
    correct: vi.fn(),
    accept: vi.fn().mockResolvedValue({ meal_id: 1, error: null }),
    cancel: vi.fn().mockResolvedValue({ ok: true }),
    barcodeLookup: vi.fn(),
    recentUnique: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock('../api/aliases', () => ({
  aliasesApi: {
    list: vi.fn().mockResolvedValue([]),
    log: vi.fn().mockResolvedValue({ ok: true, meal_id: 1, progress: {} }),
    create: vi.fn(),
    delete: vi.fn(),
    reorder: vi.fn(),
  },
}));

vi.mock('../context/SubscriptionContext', () => ({
  useSubscription: () => ({
    isPremium: true,
    imageUsage: { used: 0, limit: 5 },
    textMealUsage: { used: 0, limit: 5 },
    chatUsage: { used: 0, limit: 5 },
    savedMealsUsage: { used: 0, limit: 5 },
  }),
}));

vi.mock('../context/PullToRefreshContext', () => ({
  useRegisterRefresh: vi.fn(),
}));

vi.mock('../context/OfflineQueueContext', () => ({
  useOfflineQueue: () => ({ addToQueue: vi.fn() }),
}));

vi.mock('../context/AuthContext', () => ({
  // LogMeal reads isGuest from the auth context. Tests run as a real
  // user — false ensures the existing test bodies still hit the
  // /meals/analyze + aliases code paths.
  useAuth: () => ({ isGuest: false, user: { user_id: 1 } }),
}));

vi.mock('../components/Toast', () => ({
  useToast: () => ({ toast: vi.fn() }),
}));

vi.mock('../utils/haptics', () => ({
  hapticLight: vi.fn(),
  hapticSuccess: vi.fn(),
}));

vi.mock('../utils/capturedImage', () => ({
  getCapturedFiles: () => [],
  clearCapturedFiles: vi.fn(),
}));

vi.mock('../utils/apiCache', () => ({
  getCached: () => null,
  setCache: vi.fn(),
  clearCache: vi.fn(),
}));

// Minimal mock for ImageCapture (heavy component with file APIs)
vi.mock('../components/ImageCapture', () => ({
  default: () => <div data-testid="image-capture">ImageCapture</div>,
}));

import LogMeal from './LogMeal';
import { aliasesApi } from '../api/aliases';

function renderLogMeal(initialEntry = '/log') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LogMeal />
    </MemoryRouter>,
  );
}

// Freeze `new Date()` at noon on 2026-03-20 so the backdate time picker
// defaults to "12:00" / "12:00 PM" deterministically across test runs.
// The component seeds `backdateTime` from the current hour (LogMeal.tsx:114).
beforeEach(() => {
  vi.clearAllMocks();
  // Freeze `new Date()` at noon 2026-03-20 so the backdate time picker
  // defaults to "12:00" / "12:00 PM" deterministically. We only fake
  // `Date` - faking setTimeout breaks @testing-library's waitFor poll.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-03-20T12:00:00'));
});

afterEach(() => {
  vi.useRealTimers();
});

describe('LogMeal - backdate UI', () => {
  it('does not show backdate chip when no ?date= param', () => {
    renderLogMeal('/log');
    expect(screen.queryByText(/Logging for:/)).not.toBeInTheDocument();
  });

  it('shows backdate chip when ?date= param is present', () => {
    renderLogMeal('/log?date=2026-03-20');
    expect(screen.getByText(/Logging for:/)).toBeInTheDocument();
  });

  it('shows time picker defaulting to 12:00 when backdating', () => {
    renderLogMeal('/log?date=2026-03-20');
    const select = screen.getByDisplayValue('12:00 PM');
    expect(select).toBeInTheDocument();
  });

  it('time picker has 48 options (30-min intervals)', () => {
    renderLogMeal('/log?date=2026-03-20');
    const select = screen.getByDisplayValue('12:00 PM');
    const options = select.querySelectorAll('option');
    expect(options.length).toBe(48);
  });

  it('shows Clear button to remove backdate', () => {
    renderLogMeal('/log?date=2026-03-20');
    expect(screen.getByText('Clear')).toBeInTheDocument();
  });
});

describe('LogMeal - meal type selector', () => {
  it('renders all four meal types', () => {
    renderLogMeal('/log');
    expect(screen.getByText('breakfast')).toBeInTheDocument();
    expect(screen.getByText('lunch')).toBeInTheDocument();
    expect(screen.getByText('snack')).toBeInTheDocument();
    expect(screen.getByText('dinner')).toBeInTheDocument();
  });
});

describe('LogMeal - quick log with backdate', () => {
  const mockAlias = {
    name: 'Chicken Rice',
    item_name: 'Chicken Rice',
    meal_description: 'A serving',
    calories: 500,
    protein: 40,
    carbs: 60,
    fat: 12,
    items_json: '[]',
    sort_order: 0,
  };

  it('calls aliasesApi.log without loggedAt when no backdate', async () => {
    vi.mocked(aliasesApi.list).mockResolvedValue([mockAlias] as any);

    renderLogMeal('/log');

    // Wait for aliases to load
    await waitFor(() => {
      expect(screen.getByText('Chicken Rice')).toBeInTheDocument();
    });

    // Tap the alias card
    fireEvent.click(screen.getByText('Chicken Rice'));

    await waitFor(() => {
      expect(aliasesApi.log).toHaveBeenCalledWith('Chicken Rice', undefined);
    });
  });

  it('calls aliasesApi.log with loggedAt when backdating', async () => {
    vi.mocked(aliasesApi.list).mockResolvedValue([mockAlias] as any);

    renderLogMeal('/log?date=2026-03-20');

    await waitFor(() => {
      expect(screen.getByText('Chicken Rice')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Chicken Rice'));

    await waitFor(() => {
      expect(aliasesApi.log).toHaveBeenCalledWith('Chicken Rice', '2026-03-20 12:00');
    });
  });

  it('uses selected time in loggedAt when time picker is changed', async () => {
    vi.mocked(aliasesApi.list).mockResolvedValue([mockAlias] as any);

    renderLogMeal('/log?date=2026-03-20');

    await waitFor(() => {
      expect(screen.getByText('Chicken Rice')).toBeInTheDocument();
    });

    // Change time picker to 14:30
    const select = screen.getByDisplayValue('12:00 PM');
    fireEvent.change(select, { target: { value: '14:30' } });

    fireEvent.click(screen.getByText('Chicken Rice'));

    await waitFor(() => {
      expect(aliasesApi.log).toHaveBeenCalledWith('Chicken Rice', '2026-03-20 14:30');
    });
  });
});

describe('LogMeal - barcode mode', () => {
  it('hides image capture and text input in barcode mode', () => {
    renderLogMeal('/log?mode=barcode');
    expect(screen.queryByTestId('image-capture')).not.toBeInTheDocument();
  });

  it('shows image capture in default mode', () => {
    renderLogMeal('/log');
    expect(screen.getByTestId('image-capture')).toBeInTheDocument();
  });
});
