export const ACTIVE_JOB_STATUSES = new Set<string>([
  'queued',
  'initializing',
  'running',
  'packaging',
  'awaiting_approval',
]);

export const TERMINAL_JOB_STATUSES = new Set<string>([
  'completed',
  'failed',
  'cancelled',
]);

export function isActiveJobStatus(status: string | null | undefined) {
  return Boolean(status && ACTIVE_JOB_STATUSES.has(status));
}

export function isTerminalJobStatus(status: string | null | undefined) {
  return Boolean(status && TERMINAL_JOB_STATUSES.has(status));
}
