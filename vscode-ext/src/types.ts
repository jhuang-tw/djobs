export interface DjobsDoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  /** Advisory checks do not make the overall diagnosis fail. */
  level?: 'check' | 'info' | 'warning';
  next_step?: string | null;
}

export interface DjobsDoctorReport {
  version?: string | null;
  ok?: boolean;
  ready_hosts?: string[];
  checks: DjobsDoctorCheck[];
  next_step?: string | null;
}
