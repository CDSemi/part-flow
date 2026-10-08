/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** The release this bundle was built for (production build argument). */
  readonly VITE_PARTFLOW_RELEASE?: string;
}
