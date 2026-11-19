/* Keep the chosen engine when moving between Verity pages, including file:// demos. */
(() => {
  'use strict';

  const settings = new URLSearchParams(location.search);
  const carried = ['api', 'source'].filter(key => settings.has(key));
  if (!carried.length) return;

  function configure(link) {
    if (!link) return;
    const target = new URL(link.href, location.href);
    if (target.protocol !== location.protocol || target.origin !== location.origin) return;
    for (const key of carried) {
      if (!target.searchParams.has(key)) target.searchParams.set(key, settings.get(key));
    }
    link.href = target.href;
  }

  document.querySelectorAll('a[data-app-link]').forEach(configure);
  // Decision cards and driver controls can create additional links after a run.
  for (const event of ['click', 'focusin', 'contextmenu', 'auxclick']) {
    document.addEventListener(event, e => configure(e.target.closest?.('a[data-app-link]')), true);
  }
})();
