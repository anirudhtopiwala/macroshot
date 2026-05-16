import { describe, it, expect } from 'vitest';
import { _ALLOWED_PENDING_TOOLS } from './chatPendingTools';

// The frontend parser silently drops pending_action payloads whose `tool`
// isn't in this Set (Chat.tsx _tryParsePendingAt). The backend has a
// matching allowlist at src/web/routes/chat.py::_ALLOWED_CONFIRM_TOOLS.
// A drift between the two would mean the model's tool calls never reach
// the user as confirmation cards - a silent regression. This test locks
// the expected set so a refactor that omits a tool name fails loudly here.
describe('Chat _ALLOWED_PENDING_TOOLS', () => {
  it('contains every confirmation-gated write tool', () => {
    expect(_ALLOWED_PENDING_TOOLS.has('log_weight')).toBe(true);
    expect(_ALLOWED_PENDING_TOOLS.has('set_targets')).toBe(true);
    expect(_ALLOWED_PENDING_TOOLS.has('log_alias')).toBe(true);
    expect(_ALLOWED_PENDING_TOOLS.has('update_profile')).toBe(true);
    expect(_ALLOWED_PENDING_TOOLS.has('remember_fact')).toBe(true);
    expect(_ALLOWED_PENDING_TOOLS.has('forget_fact')).toBe(true);
  });

  it('rejects unknown tool names', () => {
    expect(_ALLOWED_PENDING_TOOLS.has('drop_database')).toBe(false);
    expect(_ALLOWED_PENDING_TOOLS.has('')).toBe(false);
  });
});
