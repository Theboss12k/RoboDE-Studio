// The studio API for the web app: core's client bound to the browser's fetch and the versioned base path.
import { createApi } from "@robode/studio-core";

export { ApiError } from "@robode/studio-core";
export type { Job } from "@robode/studio-core";

const client = createApi({ baseUrl: "/api/v1", fetch: (input, init) => window.fetch(input, init as RequestInit) });
export const api = client;
export const runJob = client.runJob;
