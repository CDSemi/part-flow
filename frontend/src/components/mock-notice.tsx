import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Transient notice for development-mock interactions. Every message makes
 * clear that only local presentation state changed — no production write.
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
