// The studio API for the web app: core's client bound to the browser's fetch and the versioned base path.
import { createApi } from "@robode/studio-core";

export { ApiError } from "@robode/studio-core";
export type { Job } from "@robode/studio-core";

/** A deployment behind STUDIO_TOKEN (for example Cloud Run) is opened once as https://host/#token=<token>: the token is kept
 *  in this browser's local storage and in a cookie, so API calls (header) and plain image, mesh and download links (cookie)
 *  both carry it. Without a token nothing changes. */
function studioToken(): string | undefined {
  try {
    const fromLink = /[#&]token=([^&]+)/.exec(window.location.hash)?.[1];
    if (fromLink) {
      window.localStorage.setItem("studio_token", decodeURIComponent(fromLink));
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    const token = window.localStorage.getItem("studio_token") ?? undefined;
    if (token) document.cookie = `studio_token=${encodeURIComponent(token)}; path=/api/v1; SameSite=Strict${window.location.protocol === "https:" ? "; Secure" : ""}`;
    return token;
  } catch {
    return undefined;
  }
}

const client = createApi({ baseUrl: "/api/v1", token: studioToken(), fetch: (input, init) => window.fetch(input, init as RequestInit) });
export const api = client;
export const runJob = client.runJob;
