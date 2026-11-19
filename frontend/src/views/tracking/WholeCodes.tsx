import { codeRuns } from './tracking-logic';

/**
 * A text whose codes — a flow (`QF-44`), a Scan Station (`ST-PACK`), a
 * Work Order Number or Part Number with a hyphen — stay whole when the
 * line wraps: each is a `wholecode` span (tracking.css), so a narrow
 * panel never breaks one after its hyphen. Shared by Tracking's history
 * rows and the archive viewer of History archival & purge; the text reads
 * and copies unchanged.
 */
export function WholeCodes({ text }: { text: string }) {
  return (
    <>
      {codeRuns(text).map((run, index) =>
        run.code ? (
          <span key={index} className="wholecode">
            {run.text}
          </span>
        ) : (
          run.text
        ),
      )}
    </>
  );
}
