import type { WorkerIdentificationMode } from '../../api/environment';

// Display labels of the Area Worker ID modes (GUI_DESIGN §9), shared by
// Administration → Areas and Administration → Worker sessions.

export const WORKER_ID_MODE_LABELS: Record<WorkerIdentificationMode, string> = {
  DISABLED: 'Disabled',
  FIXED: 'Fixed Worker',
  SCANNED: 'Scanned session',
};
