// Allowlist of tool names the chat UI accepts as pending_action payloads.
// Mirrors the backend allowlist at src/web/routes/chat.py::_ALLOWED_CONFIRM_TOOLS.
// A drift between the two means the model can call a write tool but the
// frontend silently drops the confirmation card - the user never sees it.
// The Chat.test.ts spec locks this set so a refactor that omits a tool
// fails loudly there.
//
// Lives in its own file (not Chat.tsx) so React Fast Refresh stays happy:
// Vite's react-refresh plugin requires component files to export only
// components.

export const _ALLOWED_PENDING_TOOLS = new Set([
  'log_weight', 'set_targets', 'log_alias', 'update_profile',
  'remember_fact', 'forget_fact',
]);

export type PendingActionTool =
  | 'log_weight'
  | 'set_targets'
  | 'log_alias'
  | 'update_profile'
  | 'remember_fact'
  | 'forget_fact';
