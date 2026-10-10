// Plain (server-safe) module: layout.tsx, a server component, inlines THEME_BOOT_SCRIPT into <head>. Kept apart from
// lib/theme.ts, which is a client module — a server component importing a constant from it would get a client
// reference instead of the string.
export const THEME_STORAGE_KEY = 'theme';
export const DARK_QUERY = '(prefers-color-scheme: dark)';

/**
 * Runs inline in <head> (layout.tsx) before first paint, so a dark-mode viewer never sees a light flash. Must stay
 * self-contained: it is serialised into the page as a string. Default is light until someone picks otherwise.
 */
export const THEME_BOOT_SCRIPT = `(function(){try{var p=localStorage.getItem('${THEME_STORAGE_KEY}')||'light';var d=p==='dark'||(p==='system'&&window.matchMedia('${DARK_QUERY}').matches);document.documentElement.dataset.theme=d?'dark':'light';document.documentElement.style.colorScheme=d?'dark':'light';}catch(e){}})();`;

