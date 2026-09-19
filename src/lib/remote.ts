import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ApiError } from '@/lib/api';

const reason = (e: unknown) => (e instanceof ApiError ? e.message : 'The local API did not answer. Is it still running?');

/**
 * Data a screen reads straight from the API, keyed by what it asks for: a new key loads again, and a
 * slow answer for an old key never replaces a newer one. A null key reads nothing (nothing chosen yet,
 * such as no project). `reload` keeps the last answer on screen until the fresh one arrives.
 */
export function useRemote<T>(key: string | null, load: () => Promise<T>) {
  const [state, setState] = useState<{ key: string | null; data: T | null; error: string | null }>({ key: null, data: null, error: null });
  const [version, setVersion] = useState(0);
  const latest = useRef(load);
  useLayoutEffect(() => { latest.current = load; });

  useEffect(() => {
    if (key === null) return;
    let current = true;
    latest.current().then(
      (data) => { if (current) setState({ key, data, error: null }); },
      (e: unknown) => { if (current) setState({ key, data: null, error: reason(e) }); },
    );
    return () => { current = false; };
  }, [key, version]);

  const fresh = key !== null && state.key === key;
  return {
    data: fresh ? state.data : null,
    error: fresh ? state.error : null,
    loading: key !== null && !fresh,
    reload: () => setVersion((v) => v + 1),
  };
}
