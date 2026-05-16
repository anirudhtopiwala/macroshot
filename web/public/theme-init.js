// Synchronously set the theme attribute from localStorage before the React
// tree mounts so the first paint matches the user's preference (no
// dark/light flash on cold load). Externalized from index.html so CSP
// 'script-src' no longer needs an inline-script sha256 hash (B27).
document.documentElement.setAttribute('data-theme', localStorage.getItem('theme') || 'dark');
