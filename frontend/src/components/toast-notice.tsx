import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Transient toast for confirmed view interactions. Messages use normal
 * operator wording — one explanation lives in one place, so a toast
 * never restates a rule the view already states.
 */
export function useToastNotice(): {
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
