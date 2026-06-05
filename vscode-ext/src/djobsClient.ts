import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import { DjobsCommandOptions, DjobsDoctorReport, DjobsScope, DjobsStatus, DjobsTask } from './types';

export class DjobsClient {
  constructor(private readonly workspaceRoot: string) {}

  async status(): Promise<DjobsStatus> {
    const options = this.getOptions();
    const args = ['status', '--db', options.dbPath];

    if (options.scope === 'currentWorkspace') {
      args.push('--correlation-id', this.workspaceRoot);
    }

    const output = await this.run(args);
    return JSON.parse(output) as DjobsStatus;
  }

  buildResumePrompt(): string {
    return [
      'Resume unfinished djobs tasks for this workspace.',
      '',
      `Workspace correlation_id: ${this.workspaceRoot}`,
      '',
      'Call djobs resume_session with that correlation_id, then continue incomplete tasks.',
      'Do not ask whether to resume unless there are conflicting instructions.',
    ].join('\n');
  }

  buildResumePromptForCorrelation(correlationId: string): string {
    return [
      'Resume unfinished djobs tasks for this workflow.',
      '',
      `Workspace correlation_id: ${correlationId}`,
      '',
      'Call djobs resume_session with that correlation_id, then continue incomplete tasks.',
      'Do not ask whether to resume unless there are conflicting instructions.',
    ].join('\n');
  }

  buildResumeFromPrompt(task: DjobsTask, index: number): string {
    const payload = task.payload_json ? JSON.parse(task.payload_json) : {};
    const file = payload.file ?? payload.summary ?? payload.title ?? task.id;
    return [
      `Resume djobs tasks starting from task #${index + 1}: ${file}`,
      '',
      `Workspace correlation_id: ${task.correlation_id ?? this.workspaceRoot}`,
      `Start task ID: ${task.id}`,
      `Task type: ${task.type}`,
      '',
      'Call djobs resume_session, then skip all tasks before this one',
      '(mark them complete without editing), and start processing from this task onward.',
    ].join('\n');
  }

  buildSkipPrompt(task: DjobsTask): string {
    const payload = task.payload_json ? JSON.parse(task.payload_json) : {};
    const file = payload.file ?? payload.summary ?? payload.title ?? task.id;
    return [
      `Skip djobs task: ${file}`,
      '',
      `Task ID: ${task.id}`,
      '',
      'Call djobs complete_task for this task ID without making any edits.',
      'This marks it done so it will not appear in future resume_session calls.',
    ].join('\n');
  }

  async skipTask(task: DjobsTask, evidence?: string): Promise<void> {
    const options = this.getOptions();
    const args = ['skip', task.id, '--db', options.dbPath];
    if (evidence?.trim()) {
      args.push('--evidence', evidence.trim());
    }
    await this.run(args);
  }

  async acceptBefore(task: DjobsTask, evidence?: string): Promise<number> {
    const options = this.getOptions();
    const args = ['accept-before', task.id, '--db', options.dbPath];
    if (evidence?.trim()) {
      args.push('--evidence', evidence.trim());
    }
    const output = await this.run(args);
    const result = JSON.parse(output) as { count?: number };
    return result.count ?? 0;
  }

  async archiveCurrentWorkflow(reason?: string): Promise<number> {
    return this.archiveByCorrelation(this.workspaceRoot, reason);
  }

  async archiveByCorrelation(correlationId: string, reason?: string): Promise<number> {
    const options = this.getOptions();
    const args = [
      'archive-workflow', '--db', options.dbPath,
      '--correlation-id', correlationId,
    ];
    if (reason?.trim()) {
      args.push('--reason', reason.trim());
    }
    const output = await this.run(args);
    const result = JSON.parse(output) as { count?: number };
    return result.count ?? 0;
  }

  /** True when the user has selected the shared global queue. */
  isGlobalQueue(): boolean {
    return (vscode.workspace.getConfiguration('djobs').get<string>('queueLocation') ?? 'global') === 'global';
  }

  /**
   * True when this workspace's .vscode/mcp.json already points the djobs MCP
   * server at the shared global database (i.e. the agent's write side is wired).
   */
  isGlobalMcpWired(): boolean {
    try {
      const mcpPath = path.join(this.workspaceRoot, '.vscode', 'mcp.json');
      if (!fs.existsSync(mcpPath)) {
        return false;
      }
      const parsed = JSON.parse(fs.readFileSync(mcpPath, 'utf8')) as {
        servers?: Record<string, { env?: Record<string, string> }>;
      };
      const env = parsed.servers?.djobs?.env;
      return Boolean(env && typeof env.DJOBS_DB === 'string' && env.DJOBS_DB.length > 0);
    } catch {
      return false;
    }
  }

  /** Wire the agent's write side to the shared global queue via the CLI. */
  async wireGlobalMcp(): Promise<void> {
    await this.run(['install-mcp', '--global', '--force']);
  }

  /**
   * Re-generate .vscode/mcp.json so the agent launches via the currently
   * working djobs runtime. Respects the configured queue location so a
   * workspace-queue user is not silently switched to the global queue.
   */
  async reWireMcp(): Promise<void> {
    const args = ['install-mcp', '--force'];
    if (this.isGlobalQueue()) {
      args.push('--global');
    }
    await this.run(args);
  }

  /**
   * Inspect this workspace's .vscode/mcp.json and report the djobs MCP server's
   * launch command when it can no longer be resolved (e.g. it points at a
   * project `.venv` that was deleted, or a console script no longer on PATH).
   * Returns the broken command string, or undefined when wiring is healthy or
   * absent.
   */
  detectDeadMcpInterpreter(): string | undefined {
    try {
      const mcpPath = path.join(this.workspaceRoot, '.vscode', 'mcp.json');
      if (!fs.existsSync(mcpPath)) {
        return undefined;
      }
      const parsed = JSON.parse(fs.readFileSync(mcpPath, 'utf8')) as {
        servers?: Record<string, { command?: string }>;
      };
      const command = parsed.servers?.djobs?.command;
      if (!command) {
        return undefined;
      }
      const resolved = command.replace('${workspaceFolder}', this.workspaceRoot);
      // An absolute interpreter/script path must exist on disk.
      if (path.isAbsolute(resolved)) {
        return fs.existsSync(resolved) ? undefined : command;
      }
      // Otherwise it is a bare command name resolved via PATH.
      return this.which(resolved) ? undefined : command;
    } catch {
      // A parse/read error is not a "dead interpreter"; leave it to other flows.
      return undefined;
    }
  }

  /** True when the djobs CLI can be launched (package importable or on PATH). */
  async isPackageInstalled(): Promise<boolean> {
    try {
      await this.run(['--help']);
      return true;
    } catch {
      return false;
    }
  }

  /**
   * Install djobs as a standalone tool. Prefers pipx for an isolated global
   * install that works across every project; falls back to `pip install` into
   * the project venv or a Python on PATH when pipx is unavailable.
   */
  async installPackage(): Promise<void> {
    const pipx = this.which('pipx');
    if (pipx) {
      await this.execFile(pipx, ['install', 'djobs'], this.workspaceRoot, 180000);
      this.resetLauncher();
      return;
    }
    const venvPython = process.platform === 'win32'
      ? path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(this.workspaceRoot, '.venv', 'bin', 'python');
    const fallback = process.platform === 'win32' ? 'python' : 'python3';
    const exe = fs.existsSync(venvPython) ? venvPython : fallback;
    await this.execFile(
      exe,
      ['-m', 'pip', 'install', '--upgrade', 'djobs'],
      this.workspaceRoot,
      180000,
    );
    this.resetLauncher();
  }

  /** Run `djobs doctor --json` and return the parsed setup report. */
  async doctor(): Promise<DjobsDoctorReport> {
    const output = await this.run(['doctor', '--json']);
    return JSON.parse(output) as DjobsDoctorReport;
  }

  /** The installed djobs Python package version, or undefined when unavailable. */
  async installedVersion(): Promise<string | undefined> {
    try {
      const report = await this.doctor();
      return report.version ?? undefined;
    } catch {
      return undefined;
    }
  }

  /**
   * Upgrade the djobs package to the latest release. Mirrors installPackage:
   * prefers pipx, otherwise pip --upgrade into the project venv or a Python on
   * PATH.
   */
  async updatePackage(): Promise<void> {
    const pipx = this.which('pipx');
    if (pipx) {
      await this.execFile(pipx, ['upgrade', 'djobs'], this.workspaceRoot, 180000);
      this.resetLauncher();
      return;
    }
    const venvPython = process.platform === 'win32'
      ? path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(this.workspaceRoot, '.venv', 'bin', 'python');
    const fallback = process.platform === 'win32' ? 'python' : 'python3';
    const exe = fs.existsSync(venvPython) ? venvPython : fallback;
    await this.execFile(
      exe,
      ['-m', 'pip', 'install', '--upgrade', 'djobs'],
      this.workspaceRoot,
      180000,
    );
    this.resetLauncher();
  }

  private getOptions(): DjobsCommandOptions {
    const config = vscode.workspace.getConfiguration('djobs');
    const configuredDb = config.get<string>('dbPath')?.trim() || 'djobs_mcp.db';
    const configuredScope = config.get<DjobsScope>('scope') ?? 'allWorkspaces';
    const showCompleted = config.get<boolean>('showCompleted') ?? false;
    const queueLocation = config.get<string>('queueLocation') ?? 'global';

    return {
      workspaceRoot: this.workspaceRoot,
      dbPath: this.resolveDbPath(queueLocation, configuredDb, config),
      scope: configuredScope,
      showCompleted,
    };
  }

  private resolveDbPath(
    queueLocation: string,
    configuredDb: string,
    config: vscode.WorkspaceConfiguration,
  ): string {
    if (queueLocation === 'global') {
      const configuredGlobal = config.get<string>('globalDbPath')?.trim();
      return configuredGlobal && configuredGlobal.length > 0
        ? configuredGlobal
        : path.join(os.homedir(), '.djobs', 'global.db');
    }
    return path.isAbsolute(configuredDb)
      ? configuredDb
      : path.join(this.workspaceRoot, configuredDb);
  }

  private launcher?: { exe: string; prefix: string[] };

  /**
   * Resolve how to launch the djobs CLI. djobs is a standalone tool, so it may
   * live in (in priority order): an explicit interpreter (djobs.pythonPath), a
   * project-local .venv, or — most commonly for cross-project use — a global
   * install whose `djobs` console script is on PATH (pipx / pip --user). The
   * result is cached so we do not rescan PATH on every sidebar refresh.
   */
  private resolveLauncher(): { exe: string; prefix: string[] } {
    if (this.launcher) {
      return this.launcher;
    }
    const configured = vscode.workspace.getConfiguration('djobs').get<string>('pythonPath')?.trim();
    if (configured) {
      this.launcher = { exe: configured, prefix: ['-m', 'djobs.cli'] };
      return this.launcher;
    }
    const venvPython = process.platform === 'win32'
      ? path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(this.workspaceRoot, '.venv', 'bin', 'python');
    if (fs.existsSync(venvPython)) {
      this.launcher = { exe: venvPython, prefix: ['-m', 'djobs.cli'] };
      return this.launcher;
    }
    const consoleScript = this.which('djobs');
    if (consoleScript) {
      this.launcher = { exe: consoleScript, prefix: [] };
      return this.launcher;
    }
    const fallback = process.platform === 'win32' ? 'python' : 'python3';
    this.launcher = { exe: fallback, prefix: ['-m', 'djobs.cli'] };
    return this.launcher;
  }

  /** Forget the cached launcher so the next call re-detects (e.g. after install). */
  resetLauncher(): void {
    this.launcher = undefined;
  }

  /** Minimal cross-platform `which`, scanning PATH (+ PATHEXT on Windows). */
  private which(command: string): string | undefined {
    const envPath = process.env.PATH ?? '';
    const dirs = envPath.split(path.delimiter).filter(Boolean);
    const exts = process.platform === 'win32'
      ? (process.env.PATHEXT ?? '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean)
      : [''];
    for (const dir of dirs) {
      for (const ext of exts) {
        const full = path.join(dir, command + ext);
        try {
          if (fs.statSync(full).isFile()) {
            return full;
          }
        } catch {
          // not here; keep scanning
        }
      }
    }
    return undefined;
  }

  /** Run a djobs subcommand via the resolved launcher and return stdout. */
  private run(subArgs: string[], timeout = 30000): Promise<string> {
    const launcher = this.resolveLauncher();
    return this.execFile(
      launcher.exe,
      [...launcher.prefix, ...subArgs],
      this.workspaceRoot,
      timeout,
    );
  }

  private execFile(exe: string, args: string[], cwd: string, timeout = 30000): Promise<string> {
    return new Promise((resolve, reject) => {
      childProcess.execFile(
        exe,
        args,
        { cwd, timeout, windowsHide: true },
        (error, stdout, stderr) => {
          if (error) {
            const detail = stderr.trim() || stdout.trim() || error.message;
            reject(new Error(detail));
            return;
          }
          resolve(stdout);
        },
      );
    });
  }
}
