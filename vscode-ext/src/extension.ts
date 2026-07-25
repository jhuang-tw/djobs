import * as vscode from 'vscode';
import { runDjobsCommand } from './commands';
import { DjobsClient } from './djobsClient';
import { DjobsDoctorReport } from './types';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    return;
  }

  const client = new DjobsClient(workspaceRoot);
  const output = vscode.window.createOutputChannel('djobs');
  context.subscriptions.push(output);

  const nativeMcp = typeof vscode.lm.registerMcpServerDefinitionProvider === 'function';
  const mcpDidChange = new vscode.EventEmitter<void>();
  context.subscriptions.push(mcpDidChange);

  const runTrusted = async (action: () => Promise<void>): Promise<void> => {
    if (!vscode.workspace.isTrusted) {
      await vscode.window.showWarningMessage(
        'Trust this workspace before djobs launches Python or changes local agent configuration.',
      );
      return;
    }
    await action();
  };

  if (nativeMcp) {
    context.subscriptions.push(
      vscode.lm.registerMcpServerDefinitionProvider('djobsServerProvider', {
        onDidChangeMcpServerDefinitions: mcpDidChange.event,
        provideMcpServerDefinitions: async () => {
          if (!vscode.workspace.isTrusted) {
            return [];
          }
          if (client.hasMcpJsonDjobsServer() && !client.detectDeadMcpInterpreter()) {
            return [];
          }
          if (!(await client.isPackageInstalled())) {
            return [];
          }
          const launch = client.mcpServerLaunch();
          const version = await client.installedVersion();
          const server = new vscode.McpStdioServerDefinition(
            'djobs',
            launch.command,
            launch.args,
            launch.env,
            version,
          );
          server.cwd = vscode.Uri.file(launch.cwd);
          return [server];
        },
      }),
    );
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('djobs.setup', async () => {
      await runTrusted(async () => runDjobsSetup(context, client, nativeMcp, mcpDidChange));
    }),
    vscode.commands.registerCommand('djobs.diagnose', async () => {
      await runTrusted(async () => runDiagnostics(client, output, nativeMcp));
    }),
    vscode.commands.registerCommand('djobs.memory', async () => {
      await runTrusted(async () => runValueCommand(
        client,
        output,
        ['memory', 'list'],
        'Repository Memory',
      ));
    }),
    vscode.commands.registerCommand('djobs.gain', async () => {
      await runTrusted(async () => runValueCommand(client, output, ['gain'], 'Memory Gain'));
    }),
    vscode.commands.registerCommand('djobs.receipt', async () => {
      await runTrusted(async () => runValueCommand(client, output, ['receipt'], 'Work Receipt'));
    }),
    vscode.commands.registerCommand('djobs.pause', async () => {
      await runTrusted(async () => runPauseCommand(client, true));
    }),
    vscode.commands.registerCommand('djobs.unpause', async () => {
      await runTrusted(async () => runPauseCommand(client, false));
    }),
    vscode.workspace.onDidGrantWorkspaceTrust(() => {
      mcpDidChange.fire();
    }),
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (
        event.affectsConfiguration('djobs.pythonPath')
        || event.affectsConfiguration('djobs.queueLocation')
        || event.affectsConfiguration('djobs.globalDbPath')
        || event.affectsConfiguration('djobs.dbPath')
      ) {
        client.resetLauncher();
        mcpDidChange.fire();
      }
    }),
  );
}

export function deactivate(): void {}

async function runValueCommand(
  client: DjobsClient,
  output: vscode.OutputChannel,
  args: string[],
  title: string,
): Promise<void> {
  output.clear();
  output.show(true);
  output.appendLine(`${title}\n`);
  try {
    const result = await runDjobsCommand(client, args);
    output.append(result.trimEnd());
    output.appendLine('');
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    output.appendLine(`Could not run djobs: ${detail}`);
    const choice = await vscode.window.showErrorMessage(
      `djobs ${title.toLowerCase()} could not be shown. Set up or repair djobs now?`,
      'Set up djobs',
    );
    if (choice === 'Set up djobs') {
      await vscode.commands.executeCommand('djobs.setup');
    }
  }
}

async function runDiagnostics(
  client: DjobsClient,
  output: vscode.OutputChannel,
  nativeMcp: boolean,
): Promise<void> {
  output.clear();
  output.show(true);
  output.appendLine('Running djobs diagnostics...\n');

  try {
    const report = JSON.parse(
      await runDjobsCommand(client, ['doctor', '--json']),
    ) as DjobsDoctorReport;
    let allOk = report.ok !== false;
    for (const check of report.checks) {
      if (nativeMcp && check.name === 'project MCP override' && check.ok) {
        output.appendLine(
          '  [INFO] VS Code native MCP: registered by the extension; project mcp.json is optional',
        );
        continue;
      }
      const advisory = check.level === 'info' || check.level === 'warning';
      if (!check.ok && !advisory) {
        allOk = false;
      }
      const mark = check.ok ? (check.level === 'check' ? 'OK  ' : 'INFO')
        : check.level === 'warning' ? 'WARN' : 'FAIL';
      output.appendLine(`  [${mark}] ${check.name}: ${check.detail}`);
      if (!check.ok && check.next_step) {
        output.appendLine(`         Next: ${check.next_step}`);
      }
    }

    const hooksReady = await client.hooksInstalled();
    output.appendLine(
      hooksReady
        ? '  [OK  ] passive Copilot hooks: installed'
        : '  [INFO] passive Copilot hooks: not installed; use setup when Copilot capture is needed',
    );

    output.appendLine('');
    if (report.next_step) {
      output.appendLine(`Next: ${report.next_step}`);
    }
    output.appendLine(allOk ? 'Critical checks passed.' : 'Some critical checks need attention.');
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    output.appendLine(`Could not run diagnostics: ${detail}`);
    const choice = await vscode.window.showErrorMessage(
      'djobs diagnostics could not run. Set up or repair djobs now?',
      'Set up djobs',
    );
    if (choice === 'Set up djobs') {
      await vscode.commands.executeCommand('djobs.setup');
    }
  }
}

async function runDjobsSetup(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  nativeMcp: boolean,
  mcpDidChange: vscode.EventEmitter<void>,
): Promise<void> {
  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Setting up djobs...' },
      async (progress) => {
        const installed = await client.isPackageInstalled();
        if (!installed) {
          progress.report({ message: 'Installing the local agent memory engine...' });
          await client.installPackage();
        } else {
          const installedVersion = await client.installedVersion();
          const extensionVersion = String(context.extension.packageJSON.version ?? '');
          if (installedVersion && extensionVersion && installedVersion !== extensionVersion) {
            progress.report({ message: 'Updating the local agent memory engine...' });
            await client.updatePackage();
          }
        }

        progress.report({ message: 'Configuring passive Copilot memory...' });
        await runDjobsCommand(client, ['setup', 'copilot']);

        if (nativeMcp) {
          if (client.detectDeadMcpInterpreter()) {
            progress.report({ message: 'Removing an obsolete project MCP override...' });
            await runDjobsCommand(
              client,
              ['legacy', 'install-mcp', '--force', ...(client.isGlobalQueue() ? ['--global'] : [])],
            );
          }
        } else if (client.isGlobalQueue() && !client.isGlobalMcpWired()) {
          progress.report({ message: 'Wiring the MCP server...' });
          await runDjobsCommand(client, ['legacy', 'install-mcp', '--global', '--force']);
        } else if (client.detectDeadMcpInterpreter()) {
          progress.report({ message: 'Repairing the MCP launch path...' });
          await runDjobsCommand(
            client,
            ['legacy', 'install-mcp', '--force', ...(client.isGlobalQueue() ? ['--global'] : [])],
          );
        }
      },
    );

    mcpDidChange.fire();
    vscode.window.showInformationMessage(
      'djobs is ready. Use “Show Repository Memory” to see what the current project remembers.',
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (detail === 'NO_PYTHON_RUNTIME') {
      const choice = await vscode.window.showErrorMessage(
        'djobs needs Python 3.10+ or uv. Install one, then run setup again.',
        'Get uv',
        'Get Python',
      );
      if (choice === 'Get uv') {
        await vscode.env.openExternal(
          vscode.Uri.parse('https://docs.astral.sh/uv/getting-started/installation/'),
        );
      } else if (choice === 'Get Python') {
        await vscode.env.openExternal(vscode.Uri.parse('https://www.python.org/downloads/'));
      }
      return;
    }

    if (detail.startsWith('PYTHON_TOO_OLD')) {
      vscode.window.showErrorMessage(
        'djobs requires Python 3.10 or newer. Install uv or a newer Python, then run setup again.',
      );
      return;
    }

    const choice = await vscode.window.showErrorMessage(
      `djobs setup failed: ${detail}`,
      'Open diagnostics',
    );
    if (choice === 'Open diagnostics') {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}

async function runPauseCommand(client: DjobsClient, pause: boolean): Promise<void> {
  try {
    await runDjobsCommand(client, [pause ? 'pause' : 'unpause', '--db', client.resolvedDbPath()]);
    if (pause) {
      vscode.window.showInformationMessage(
        'djobs paused. Passive observation and recovery are disabled; no state was deleted.',
      );
    } else {
      vscode.window.showInformationMessage('djobs resumed.');
    }
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (/invalid choice: '?(pause|unpause)'?|unrecognized arguments: (pause|unpause)/i.test(detail)) {
      const selected = await vscode.window.showErrorMessage(
        'The installed djobs engine is too old for this command.',
        'Update djobs',
      );
      if (selected === 'Update djobs') {
        await vscode.window.withProgress(
          { location: vscode.ProgressLocation.Notification, title: 'Updating djobs...' },
          async () => client.updatePackage(),
        );
        await runPauseCommand(client, pause);
      }
      return;
    }
    vscode.window.showErrorMessage(`djobs command failed: ${detail}`);
  }
}
