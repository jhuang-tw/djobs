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
}

export interface DjobsStatus {
  timestamp: string;
  health: DjobsHealth;
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
  pythonPath: string;
  dbPath: string;
  scope: DjobsScope;
  showCompleted: boolean;
}
