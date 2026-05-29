import * as childProcess from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { DjobsCommandOptions, DjobsScope, DjobsStatus, DjobsTask } from './types';

export class DjobsClient {
  constructor(private readonly workspaceRoot: string) {}

  async status(): Promise<DjobsStatus> {
    const options = this.getOptions();
    const args = [
      '-m',
      'djobs.cli',
      'status',
      '--db',
      options.dbPath,
    ];

    if (options.scope === 'currentWorkspace') {
      args.push('--correlation-id', options.workspaceRoot);
    }

    const output = await this.execPython(options.pythonPath, args, options.workspaceRoot);
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
    const args = ['-m', 'djobs.cli', 'skip', task.id, '--db', options.dbPath];
    if (evidence?.trim()) {
      args.push('--evidence', evidence.trim());
    }
    await this.execPython(options.pythonPath, args, options.workspaceRoot);
  }

  async acceptBefore(task: DjobsTask, evidence?: string): Promise<number> {
    const options = this.getOptions();
    const args = ['-m', 'djobs.cli', 'accept-before', task.id, '--db', options.dbPath];
    if (evidence?.trim()) {
      args.push('--evidence', evidence.trim());
    }
    const output = await this.execPython(options.pythonPath, args, options.workspaceRoot);
    const result = JSON.parse(output) as { count?: number };
    return result.count ?? 0;
  }

  async archiveCurrentWorkflow(reason?: string): Promise<number> {
    return this.archiveByCorrelation(this.workspaceRoot, reason);
  }

  async archiveByCorrelation(correlationId: string, reason?: string): Promise<number> {
    const options = this.getOptions();
    const args = [
      '-m', 'djobs.cli', 'archive-workflow', '--db', options.dbPath,
      '--correlation-id', correlationId,
    ];
    if (reason?.trim()) {
      args.push('--reason', reason.trim());
    }
    const output = await this.execPython(options.pythonPath, args, options.workspaceRoot);
    const result = JSON.parse(output) as { count?: number };
    return result.count ?? 0;
  }

  private getOptions(): DjobsCommandOptions {
    const config = vscode.workspace.getConfiguration('djobs');
    const configuredPython = config.get<string>('pythonPath')?.trim();
    const configuredDb = config.get<string>('dbPath')?.trim() || 'djobs_mcp.db';
    const configuredScope = config.get<DjobsScope>('scope') ?? 'allWorkspaces';
    const showCompleted = config.get<boolean>('showCompleted') ?? false;

    return {
      workspaceRoot: this.workspaceRoot,
      pythonPath: configuredPython || this.detectPython(),
      dbPath: path.isAbsolute(configuredDb)
        ? configuredDb
        : path.join(this.workspaceRoot, configuredDb),
      scope: configuredScope,
      showCompleted,
    };
  }

  private detectPython(): string {
    const candidates = process.platform === 'win32'
      ? [
        path.join(this.workspaceRoot, '.venv', 'Scripts', 'python.exe'),
        'python',
        'py',
      ]
      : [
        path.join(this.workspaceRoot, '.venv', 'bin', 'python'),
        'python3',
        'python',
      ];

    return candidates.find((candidate) => path.isAbsolute(candidate) && fs.existsSync(candidate))
      ?? candidates[candidates.length - 1];
  }

  private execPython(pythonPath: string, args: string[], cwd: string): Promise<string> {
    return new Promise((resolve, reject) => {
      childProcess.execFile(
        pythonPath,
        args,
        { cwd, timeout: 15000, windowsHide: true },
        (error, stdout, stderr) => {
          if (error) {
            const detail = stderr.trim() || stdout.trim() || error.message;
            reject(new Error(`djobs command failed: ${detail}`));
            return;
          }
          resolve(stdout);
        },
      );
    });
  }
}
