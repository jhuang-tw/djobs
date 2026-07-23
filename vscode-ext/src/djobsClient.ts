import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import { DjobsDoctorReport } from './types';

type DjobsInstaller =
  | { kind: 'pipx'; exe: string }
  | { kind: 'uv'; exe: string }
  | { kind: 'pip'; exe: string; pyArgs: string[]; isVenv: boolean };

export class DjobsClient {
  constructor(private readonly workspaceRoot: string) {}

  /** Pause djobs operations without deleting local state (reversible). */
  async pause(): Promise<void> {
    await this.run(['pause', '--db', this.resolvedDbPath()]);
  }

  /** Resume normal djobs behavior after a pause. */
  async unpause(): Promise<void> {
    await this.run(['unpause', '--db', this.resolvedDbPath()]);
  }

  /** Install the passive local Copilot lifecycle adapter. */
  async installHooks(): Promise<void> {
    await this.run(['setup', 'copilot']);
  }

  /** Check whether the passive Copilot hook document is installed and valid. */
  async hooksInstalled(): Promise<boolean> {
    try {
      const hookPath = path.join(os.homedir(), '.copilot', 'hooks', 'djobs.json');
      if (!fs.existsSync(hookPath)) {
        return false;
      }
      const parsed = JSON.parse(fs.readFileSync(hookPath, 'utf8')) as {
        version?: number;
        hooks?: Record<string, unknown>;
      };
      const hooks = parsed.hooks;
      if (parsed.version !== 1 || !hooks) {
        return false;
      }
      const required = [
        'SessionStart',
        'PostToolUse',
        'PostToolUseFailure',
        'PreCompact',
        'SessionEnd',
      ];
      return required.every((event) => Object.prototype.hasOwnProperty.call(hooks, event))
        && JSON.stringify(parsed).includes('djobs.hook_entrypoint');
    } catch {
      return false;
    }
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
   * Resolve how to launch the djobs **MCP server** (not the CLI) for VS Code's
   * native MCP registration. Mirrors `_resolve_mcp_command` in the CLI so the
   * programmatic registration and the `install-mcp` JSON fallback start the same
   * server: prefer an explicit interpreter, then a project `.venv`, then the
   * `djobs-mcp` console script on PATH, then a bare `python`. `DJOBS_DB` is
   * always pinned to the absolute queue path used by hooks, so the agent's
   * hooks and MCP reads share one database regardless of cwd.
   */
  mcpServerLaunch(): { command: string; args: string[]; env: Record<string, string>; cwd: string } {
    const configured = vscode.workspace.getConfiguration('djobs').get<string>('pythonPath')?.trim();
    let command: string;
    let args: string[];
    if (configured) {
      command = configured;
      args = ['-m', 'djobs.coding_mcp'];
    } else {
      const venvPython = process.platform === 'win32'
        ? path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe')
        : path.join(this.workspaceRoot, '.venv', 'bin', 'python');
      if (fs.existsSync(venvPython)) {
        command = venvPython;
        args = ['-m', 'djobs.coding_mcp'];
      } else {
        const consoleScript = this.which('djobs-mcp');
        if (consoleScript) {
          command = consoleScript;
          args = [];
        } else {
          command = process.platform === 'win32' ? 'python' : 'python3';
          args = ['-m', 'djobs.coding_mcp'];
        }
      }
    }
    return {
      command,
      args,
      env: { DJOBS_DB: this.resolvedDbPath(), DJOBS_AGENT_TYPE: 'copilot' },
      cwd: this.workspaceRoot,
    };
  }

  /** Absolute queue path used by hooks and the MCP server. */
  resolvedDbPath(): string {
    const config = vscode.workspace.getConfiguration('djobs');
    const queueLocation = config.get<string>('queueLocation') ?? 'global';
    if (queueLocation === 'global') {
      const configuredGlobal = config.get<string>('globalDbPath')?.trim();
      return configuredGlobal && configuredGlobal.length > 0
        ? configuredGlobal
        : path.join(os.homedir(), '.djobs', 'global.db');
    }
    const configuredDb = config.get<string>('dbPath')?.trim() || 'djobs_mcp.db';
    return path.isAbsolute(configuredDb)
      ? configuredDb
      : path.join(this.workspaceRoot, configuredDb);
  }

  /**
   * True when this workspace already has a `.vscode/mcp.json` djobs server
   * entry. When present, native MCP registration defers to it so the agent
   * never sees two "djobs" servers (which would duplicate its tool list); an
   * absent file lets the extension register the server natively, with no JSON.
   */
  hasMcpJsonDjobsServer(): boolean {
    try {
      const mcpPath = path.join(this.workspaceRoot, '.vscode', 'mcp.json');
      if (!fs.existsSync(mcpPath)) {
        return false;
      }
      const parsed = JSON.parse(fs.readFileSync(mcpPath, 'utf8')) as {
        servers?: Record<string, unknown>;
      };
      return Boolean(parsed.servers && Object.prototype.hasOwnProperty.call(parsed.servers, 'djobs'));
    } catch {
      return false;
    }
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
    * Install djobs as a standalone tool, trying every reasonable strategy so the
    * user never has to touch a terminal. pipx is convenient, but it can itself be
    * installed with an older Python; if that Python cannot satisfy djobs'
    * Requires-Python metadata, fall back to uv / py -3.10+ / pip instead of
    * surfacing pip's wall of resolver output.
   *
    * Throws `Error('NO_PYTHON_RUNTIME')` when no runtime is found (the extension
    * cannot silently install Python; that is a system change the user must make),
    * `Error('PYTHON_TOO_OLD: ...')` when all available runtimes are below djobs'
    * minimum, or `Error('INSTALL_FAILED: ...')` with compact attempt summaries.
   */
  async installPackage(): Promise<void> {
    const installers = this.findInstallers();
    if (installers.length === 0) {
      throw new Error('NO_PYTHON_RUNTIME');
    }

    const errors: string[] = [];
    for (const installer of installers) {
      try {
        await this.installWith(installer);
        this.resetLauncher();
        if (await this.isPackageInstalled()) {
          return;
        }
        errors.push(`${this.describeInstaller(installer)}: install completed but djobs did not launch`);
      } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        errors.push(`${this.describeInstaller(installer)}: ${this.summarizeInstallError(detail)}`);
      }
    }

    const detail = errors.join(' | ');
    if (errors.some((error) => this.isRequiresPythonError(error))) {
      throw new Error(
        'PYTHON_TOO_OLD: djobs requires Python 3.10 or newer, but the available '
        + 'installer runtimes could not satisfy that requirement. Install uv '
        + '(recommended, no pre-existing Python needed) or Python 3.10+, then run setup again. '
        + `Attempts: ${detail}`,
      );
    }
    throw new Error(`INSTALL_FAILED: ${detail}`);
  }

  private async installWith(installer: DjobsInstaller): Promise<void> {
    if (installer.kind === 'pipx') {
      await this.execFile(installer.exe, ['install', 'djobs'], this.workspaceRoot, 180000);
    } else if (installer.kind === 'uv') {
      // `uv tool install` is isolated like pipx and manages its own tool bin on
      // PATH; uv can also provision a Python, so this succeeds with no system
      // Python at all.
      await this.execFile(installer.exe, ['tool', 'install', 'djobs'], this.workspaceRoot, 180000);
    } else {
      const pipArgs = [...installer.pyArgs, '-m', 'pip', 'install', '--upgrade'];
      if (!installer.isVenv) {
        pipArgs.push('--user');
      }
      pipArgs.push('djobs');
      await this.execFile(installer.exe, pipArgs, this.workspaceRoot, 180000);
      // A bare `pip --user` install often lands the `djobs` console script in a
      // per-user Scripts dir that is not on PATH, so pin the concrete
      // interpreter we installed into. The venv case needs no pin (the launcher
      // already finds `.venv`).
      if (!installer.isVenv) {
        await this.pinInterpreter(installer.exe, installer.pyArgs);
      }
    }
  }

  /**
  * Locate available ways to install djobs, in preferred order. An empty list
  * means no installer/runtime was found.
   */
  private findInstallers(): DjobsInstaller[] {
    const installers: DjobsInstaller[] = [];
    const pipx = this.which('pipx');
    if (pipx) {
      installers.push({ kind: 'pipx', exe: pipx });
    }
    // After pipx, prefer uv when present: `uv tool install` is isolated and uv
    // can provision its own Python, so it is the only installer that works when
    // there is no Python on the machine at all.
    const uv = this.which('uv');
    if (uv) {
      installers.push({ kind: 'uv', exe: uv });
    }
    const venvPython = process.platform === 'win32'
      ? path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe')
      : path.join(this.workspaceRoot, '.venv', 'bin', 'python');
    if (fs.existsSync(venvPython)) {
      installers.push({ kind: 'pip', exe: venvPython, pyArgs: [], isVenv: true });
    }
    // Windows `py -3` launcher: common when python.exe is not on PATH.
    if (process.platform === 'win32') {
      const py = this.which('py');
      if (py) {
        installers.push(
          { kind: 'pip', exe: py, pyArgs: ['-3.13'], isVenv: false },
          { kind: 'pip', exe: py, pyArgs: ['-3.12'], isVenv: false },
          { kind: 'pip', exe: py, pyArgs: ['-3.11'], isVenv: false },
          { kind: 'pip', exe: py, pyArgs: ['-3.10'], isVenv: false },
          { kind: 'pip', exe: py, pyArgs: ['-3'], isVenv: false },
        );
      }
    }
    const python = this.which(process.platform === 'win32' ? 'python' : 'python3')
      ?? this.which('python');
    if (python) {
      installers.push({ kind: 'pip', exe: python, pyArgs: [], isVenv: false });
    }
    return installers;
  }

  private describeInstaller(installer: DjobsInstaller): string {
    if (installer.kind === 'pipx') { return 'pipx'; }
    if (installer.kind === 'uv') { return 'uv'; }
    return [installer.exe, ...installer.pyArgs].join(' ');
  }

  private summarizeInstallError(detail: string): string {
    const compact = detail.replace(/\s+/g, ' ').trim();
    if (this.isRequiresPythonError(compact)) {
      return 'Python runtime is too old for djobs (requires Python >=3.10)';
    }
    return compact.length > 320 ? `${compact.slice(0, 320)}...` : compact;
  }

  private isRequiresPythonError(detail: string): boolean {
    return /Requires-Python\s*>=\s*3\.10/i.test(detail)
      || /requires Python\s*>=\s*3\.10/i.test(detail)
      || /too old for djobs/i.test(detail)
      || /requires a different python/i.test(detail);
  }

  /**
   * Pin `djobs.pythonPath` to the concrete interpreter we installed into, so the
   * launcher and `install-mcp` resolve to it regardless of PATH. Best-effort.
   */
  private async pinInterpreter(exe: string, pyArgs: string[]): Promise<void> {
    try {
      const out = await this.execFile(
        exe,
        [...pyArgs, '-c', 'import sys; sys.stdout.write(sys.executable)'],
        this.workspaceRoot,
      );
      const concrete = out.trim();
      if (concrete && fs.existsSync(concrete)) {
        await vscode.workspace
          .getConfiguration('djobs')
          .update('pythonPath', concrete, vscode.ConfigurationTarget.Global);
        this.resetLauncher();
      }
    } catch {
      // Non-fatal: the launcher falls back to its normal resolution order.
    }
  }

  /** Run `djobs doctor --json` and return the parsed setup report. */
  async doctor(): Promise<DjobsDoctorReport> {
    const output = await this.run(['doctor', '--json']);
    return JSON.parse(output) as DjobsDoctorReport;
  }

  /**
   * The installed djobs Python package version, or undefined when unavailable.
   *
   * Probes ``djobs.__version__`` through the launcher's interpreter, which works
   * on EVERY djobs version (older ones lack the ``doctor`` command, so we must
   * not rely on it here). Falls back to ``doctor --json`` only for console-script
   * launchers where no Python ``-c`` is available.
   */
  async installedVersion(): Promise<string | undefined> {
    const launcher = this.resolveLauncher();
    if (launcher.prefix.includes('-m')) {
      try {
        const out = await this.execFile(
          launcher.exe,
          ['-c', 'import djobs, sys; sys.stdout.write(getattr(djobs, "__version__", ""))'],
          this.workspaceRoot,
        );
        return out.trim() || undefined;
      } catch {
        return undefined;
      }
    }
    try {
      const report = await this.doctor();
      return report.version ?? undefined;
    } catch {
      return undefined;
    }
  }

  /**
   * Upgrade djobs in the SAME interpreter the launcher uses, so a project that
   * runs its own ``.venv`` djobs is actually updated (not some other global
   * install). For console-script launchers, prefer ``pipx upgrade``.
   */
  async updatePackage(): Promise<void> {
    const launcher = this.resolveLauncher();
    if (launcher.prefix.includes('-m')) {
      await this.execFile(
        launcher.exe,
        ['-m', 'pip', 'install', '--upgrade', 'djobs'],
        this.workspaceRoot,
        180000,
      );
      this.resetLauncher();
      return;
    }
    const pipx = this.which('pipx');
    if (pipx) {
      await this.execFile(pipx, ['upgrade', 'djobs'], this.workspaceRoot, 180000);
    } else {
      // Match installPackage's pipx-then-uv preference so we upgrade with the
      // same tool that installed the console script.
      const uv = this.which('uv');
      if (uv) {
        await this.execFile(uv, ['tool', 'upgrade', 'djobs'], this.workspaceRoot, 180000);
      } else {
        const fallback = process.platform === 'win32' ? 'python' : 'python3';
        await this.execFile(
          fallback,
          ['-m', 'pip', 'install', '--upgrade', 'djobs'],
          this.workspaceRoot,
          180000,
        );
      }
    }
    this.resetLauncher();
  }

  private launcher?: { exe: string; prefix: string[] };

  /**
   * Resolve how to launch the djobs CLI. djobs is a standalone tool, so it may
   * live in (in priority order): an explicit interpreter (djobs.pythonPath), a
   * project-local .venv, or — most commonly for cross-project use — a global
   * install whose `djobs` console script is on PATH (pipx / pip --user). The
   * result is cached so we do not rescan PATH on repeated command invocations.
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
