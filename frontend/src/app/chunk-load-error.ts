// A view whose code chunk cannot be loaded — typically a page opened
// before a release asks for a chunk the new release no longer serves
// (the web tier answers a missing asset with 404). `React.lazy` caches
// the rejected import, so only a page reload can recover it.

const CHUNK_LOAD_MESSAGE =
  /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|Unable to preload CSS/i;

/** Whether `error` is a failed load of a lazy view's code. */
export function isChunkLoadError(error: unknown): boolean {
  return (
    error instanceof Error &&
    (error.name === 'ChunkLoadError' || CHUNK_LOAD_MESSAGE.test(error.message))
  );
}
