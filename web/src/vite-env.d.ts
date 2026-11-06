/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the server. Defaults to the same origin. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
