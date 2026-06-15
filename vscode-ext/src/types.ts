export type JobStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'retry_scheduled'
  | 'dead_lettered'
  | string;

export interface DjobsTask {
  id: string;
  type: string;
  status: JobStatus;
  payload_json?: string;
  evidence?: string | null;
  correlation_id?: string | null;
  created_at?: string;
  updated_at?: string;
  attempt?: number;
  max_attempts?: number;
  last_error?: string | null;
  depends_on?: string[];
}

export interface DjobsStatus {
  timestamp: string;
  health: DjobsHealth;
  paused?: boolean;
  tasks: DjobsTask[];
}

export interface DjobsHealth {
  status?: string;
  queue_depth?: Record<string, number>;
  total_jobs?: number;
  [key: string]: unknown;
}

export type DjobsScope = 'currentWorkspace' | 'allWorkspaces';

export interface DjobsCommandOptions {
  workspaceRoot: string;
  dbPath: string;
  scope: DjobsScope;
  showCompleted: boolean;
}

export interface DjobsDoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  /** "info" checks are advisory: a false `ok` is not a failure. Defaults to "check". */
  level?: 'check' | 'info';
}

export interface DjobsDoctorReport {
  /** The installed djobs Python package version, or null when not importable. */
  version?: string | null;
  checks: DjobsDoctorCheck[];
}
