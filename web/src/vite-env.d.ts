/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL of the server. Defaults to the same origin. */
  readonly VITE_API_BASE?: string;
  /** How often open views ask the server again, in milliseconds. 0: never (use the Refresh button). Default 2000. */
  readonly VITE_AUTO_REFRESH_MS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
