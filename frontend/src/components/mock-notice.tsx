import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Transient toast for confirmed view interactions. Messages use normal
 * operator wording; the development-only data boundary is stated once
 * per view by DevNotice, never repeated in every toast.
 */
export function useMockNotice(): {
  notice: string | null;
  showNotice: (message: string) => void;
  noticeElement: React.ReactNode;
} {
  const [notice, setNotice] = useState<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout>>();

  const showNotice = useCallback((message: string) => {
    setNotice(message);
    clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setNotice(null), 3600);
  }, []);

  useEffect(() => () => clearTimeout(timeoutRef.current), []);

  const noticeElement = notice ? (
    <div className="toast" role="status">
      {notice}
    </div>
  ) : null;

  return { notice, showNotice, noticeElement };
}
