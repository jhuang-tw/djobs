export interface DjobsDoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  /** Advisory checks do not make the overall diagnosis fail. */
  level?: 'check' | 'info';
}

export interface DjobsDoctorReport {
  version?: string | null;
  checks: DjobsDoctorCheck[];
}
