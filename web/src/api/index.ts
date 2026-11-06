// Public entry for the API layer. Screens import from here:
//   import { api, ApiError, type CaseDetail } from "../../api";
export * as api from "./client";
export { ApiError, isApiError, isGuardProblem, toProblem } from "./errors";
export { onApiChange } from "./events";
export { OPERATIONS, CONTRACT_VERSION } from "./operations";
export type * from "./types";
