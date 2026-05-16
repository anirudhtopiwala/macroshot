/**
 * Temporary store for files captured from the FAB camera button.
 * Needed because File objects can't survive React Router navigation state.
 */
let _files: File[] = [];

export function setCapturedFiles(files: File[]) { _files = files; }
export function getCapturedFiles(): File[] { return _files; }
export function clearCapturedFiles() { _files = []; }
