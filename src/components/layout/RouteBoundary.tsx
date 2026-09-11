import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Home, RotateCcw, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface Props {
  children: ReactNode;
  /** Rendered instead of the default panel — pass `null` to fail silently (e.g. an overlay). */
  fallback?: ReactNode;
}
interface State { error: Error | null }

/* A deploy replaces every chunk hash, so a tab opened before it asks for files that no longer exist.
   React.lazy caches that rejection and rethrows it on every render — only a reload can recover. */
const CHUNK_ERROR = /dynamically imported module|Importing a module script failed|error loading dynamically imported module|Unable to preload CSS/i;
const isChunkError = (e: Error | null) => !!e && CHUNK_ERROR.test(e.message);

/**
 * Contains a crash to the screen that caused it. Without this, one component throwing during
 * render unmounts the whole React root — sidebar, topbar and all — and leaves a blank window.
 * Keyed by route in the shell, so navigating away always recovers.
 */
export class RouteBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[NeuroCode] screen crashed:', error, info.componentStack);
  }

  retry = () => {
    if (isChunkError(this.state.error)) window.location.reload();
    else this.setState({ error: null });
  };

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback !== undefined) return this.props.fallback;
    const stale = isChunkError(error);

    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="panel w-full max-w-lg rounded-md border border-danger/35 bg-surface p-5">
          <div className="flex items-center gap-2">
            <TriangleAlert className="size-4 text-danger" />
            <h2 className="text-[14px] font-semibold text-ink">{stale ? 'A newer version is available' : 'This screen crashed'}</h2>
          </div>
          <p className="mt-2 text-[13.5px] leading-relaxed text-soft">
            {stale
              ? 'This screen’s code changed after the tab was opened — a new build was deployed. Reload to pick it up; nothing you did caused this.'
              : 'The rest of the app is fine — the failure was contained to this route. The error below is also in the console.'}
          </p>
          <pre className="ascii mt-3 max-h-40 overflow-auto rounded-sm border border-line bg-base p-3 whitespace-pre-wrap text-danger">
            {error.message}
          </pre>
          <div className="mt-4 flex gap-2">
            <Button size="sm" onClick={this.retry}><RotateCcw className="size-3.5" />{stale ? 'Reload' : 'Try again'}</Button>
            <Button size="sm" variant="outline" onClick={() => window.location.assign('/')}><Home className="size-3.5" />Command Center</Button>
          </div>
        </div>
      </div>
    );
  }
}

/** Shown while a lazily loaded screen's chunk arrives. */
export function PageSkeleton() {
  return (
    <div className="flex h-full flex-col" aria-busy="true" aria-label="Loading screen">
      <div className="border-b border-line px-6 pt-5 pb-5">
        <div className="animate-shimmer h-5 w-48 rounded-sm" />
        <div className="animate-shimmer mt-2.5 h-3 w-[28rem] max-w-full rounded-sm" />
      </div>
      <div className="grid grid-cols-2 gap-3 px-6 py-5 lg:grid-cols-4">
        {[0, 1, 2, 3].map((n) => <div key={n} className="animate-shimmer h-20 rounded-md" />)}
      </div>
      <div className="animate-shimmer mx-6 h-72 rounded-md" />
    </div>
  );
}
