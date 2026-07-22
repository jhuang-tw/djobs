import * as vscode from 'vscode';
import { DjobsClient } from './djobsClient';

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

  if (nativeMcp) {
    context.subscriptions.push(
      vscode.lm.registerMcpServerDefinitionProvider('djobsServerProvider', {
        onDidChangeMcpServerDefinitions: mcpDidChange.event,
        provideMcpServerDefinitions: async () => {
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
      await runDjobsSetup(context, client, nativeMcp, mcpDidChange);
    }),
    vscode.commands.registerCommand('djobs.diagnose', async () => {
      await runDiagnostics(client, output, nativeMcp);
    }),
    vscode.commands.registerCommand('djobs.pause', async () => {
      await runPauseCommand(client, true);
    }),
    vscode.commands.registerCommand('djobs.unpause', async () => {
      await runPauseCommand(client, false);
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

async function runDiagnostics(
  client: DjobsClient,
  output: vscode.OutputChannel,
  nativeMcp: boolean,
): Promise<void> {
  output.clear();
  output.show(true);
  output.appendLine('Running djobs diagnostics...\n');

  try {
    const report = await client.doctor();
    let allOk = true;
    for (const check of report.checks) {
      if (nativeMcp && check.name === 'mcp.json wiring') {
        output.appendLine('  [INFO] VS Code native MCP: registered by the extension; mcp.json is optional');
        continue;
      }
      const info = check.level === 'info';
      if (!check.ok && !info) {
        allOk = false;
      }
      const mark = check.ok ? 'OK  ' : info ? 'INFO' : 'FAIL';
      output.appendLine(`  [${mark}] ${check.name}: ${check.detail}`);
    }

    const hooksReady = await client.hooksInstalled();
    output.appendLine(
      hooksReady
        ? '  [OK  ] automatic coding hooks: installed'
        : '  [FAIL] automatic coding hooks: missing; run "djobs: Set up / Repair djobs"',
    );
    allOk = allOk && hooksReady;

    output.appendLine('');
    output.appendLine(allOk ? 'All checks passed.' : 'Some checks need attention.');
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
          progress.report({ message: 'Installing the coding checkpoint engine...' });
          await client.installPackage();
        } else {
          const installedVersion = await client.installedVersion();
          const extensionVersion = String(context.extension.packageJSON.version ?? '');
          if (installedVersion && extensionVersion && installedVersion !== extensionVersion) {
            progress.report({ message: 'Updating the coding checkpoint engine...' });
            await client.updatePackage();
          }
        }

        progress.report({ message: 'Installing smart coding hooks...' });
        await client.installHooks();

        if (nativeMcp) {
          if (client.detectDeadMcpInterpreter()) {
            progress.report({ message: 'Repairing an old MCP launch path...' });
            await client.reWireMcp();
          }
        } else if (client.isGlobalQueue() && !client.isGlobalMcpWired()) {
          progress.report({ message: 'Wiring the MCP server...' });
          await client.wireGlobalMcp();
        } else if (client.detectDeadMcpInterpreter()) {
          progress.report({ message: 'Repairing the MCP launch path...' });
          await client.reWireMcp();
        }
      },
    );

    mcpDidChange.fire();
    vscode.window.showInformationMessage(
      'djobs is ready. Smart command checkpoints and session recovery are active; no sidebar is added.',
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    if (detail === 'NO_PYTHON_RUNTIME') {
      const choice = await vscode.window.showErrorMessage(
        'djobs needs Python 3.11+ or uv. Install one, then run setup again.',
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
        'djobs requires Python 3.11 or newer. Install uv or a newer Python, then run setup again.',
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
    if (pause) {
      await client.pause();
      vscode.window.showInformationMessage(
        'djobs paused. Automatic checkpoint rewriting and recovery are disabled; no state was deleted.',
      );
    } else {
      await client.unpause();
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
