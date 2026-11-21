/* The one place to point the pages at an API.
   apiBaseUrl: the deployed API's origin with no trailing slash, e.g. "https://verity-api.vercel.app".
   Empty = offline: the pages use the saved engine snapshot (js/snapshot.js) and say so.
   A ?api=<url> in the page address still overrides this, for local runs (?api=http://127.0.0.1:8000).
   The API must list this site's origin in VERITY_CORS_ORIGINS. */
window.VERITY_CONFIG = {
  apiBaseUrl: ""
};
