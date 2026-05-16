import { Component, type ReactNode, type ErrorInfo } from 'react';
import * as Sentry from '@sentry/react';
import Button from './Button';
import { nuclearReset } from '../utils/swManager';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  isChunkError: boolean;
}

function detectChunkError(error: Error | null): boolean {
  if (!error) return false;
  const msg = error.message || '';
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    msg.includes('Importing a module script failed') ||
    msg.includes('Loading chunk') ||
    msg.includes('error loading dynamically imported module') ||
    error.name === 'ChunkLoadError'
  );
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, isChunkError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return {
      hasError: true,
      error,
      isChunkError: detectChunkError(error),
    };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo);
    if (Sentry.isInitialized()) {
      Sentry.captureException(error, { extra: { componentStack: errorInfo.componentStack } });
    }
  }

  render() {
    if (this.state.hasError) {
      // Chunk errors get a specialized recovery UI
      if (this.state.isChunkError) {
        return (
          <div className="flex flex-col items-center justify-center min-h-[60vh] px-6 text-center">
            <div
              className="w-16 h-16 rounded-full flex items-center justify-center mb-4"
              style={{
                background: 'rgba(245, 158, 11, 0.15)',
                border: '1px solid rgba(245, 158, 11, 0.3)',
              }}
            >
              <span className="text-2xl" style={{ color: '#f59e0b' }}>&#x21bb;</span>
            </div>
            <h2 className="text-lg font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
              App update needed
            </h2>
            <p className="text-sm mb-6 max-w-xs" style={{ color: 'var(--text-muted)' }}>
              A new version was deployed. Tap below to load the latest version.
            </p>
            <div className="flex flex-col gap-3 w-full max-w-xs">
              <Button variant="primary" onClick={() => window.location.reload()}>
                Reload Page
              </Button>
              <button
                onClick={() => nuclearReset()}
                className="text-xs py-2 transition-colors"
                style={{ color: 'var(--text-muted)' }}
              >
                Not working? Clear cache & reload
              </button>
            </div>
          </div>
        );
      }

      // Generic error UI
      return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] px-6 text-center">
          <div
            className="w-16 h-16 rounded-full flex items-center justify-center mb-4"
            style={{
              background: 'rgba(239, 68, 68, 0.15)',
              border: '1px solid rgba(239, 68, 68, 0.3)',
            }}
          >
            <span className="text-2xl">!</span>
          </div>
          <h2 className="text-lg font-bold mb-2" style={{ color: 'var(--text-primary)' }}>
            Something went wrong
          </h2>
          <p className="text-sm mb-6 max-w-xs" style={{ color: 'var(--text-muted)' }}>
            An unexpected error occurred. Please reload the page to try again.
          </p>
          <div className="flex flex-col gap-3 w-full max-w-xs">
            <Button variant="primary" onClick={() => window.location.reload()}>
              Reload Page
            </Button>
            <button
              onClick={() => nuclearReset()}
              className="text-xs py-2 transition-colors"
              style={{ color: 'var(--text-muted)' }}
            >
              Still broken? Clear all data & reload
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
