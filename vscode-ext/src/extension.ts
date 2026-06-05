import * as vscode from 'vscode';
import { DjobsClient } from './djobsClient';
import { DjobsTasksProvider, TaskItem, ActionGroup, WorkflowGroup } from './tasksProvider';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) {
    return;
  }

  const client = new DjobsClient(workspaceRoot);
  const provider = new DjobsTasksProvider(client);

  // Status bar badge
  const statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Left, 50,
  );
  statusBarItem.command = 'workbench.view.extension.djobs';
  statusBarItem.tooltip = 'Open djobs sidebar';
  context.subscriptions.push(statusBarItem);

  const doctorChannel = vscode.window.createOutputChannel('djobs');
  context.subscriptions.push(doctorChannel);

  function updateStatusBar(): void {
    const count = provider.getIncompleteCount();
    if (count > 0) {
      statusBarItem.text = `$(debug-restart) djobs: ${count} pending`;
      statusBarItem.show();
    } else {
      statusBarItem.hide();
    }
  }

  // Wrap refresh to also update status bar
  const originalRefresh = provider.refresh.bind(provider);
  provider.refresh = async () => {
    await originalRefresh();
    updateStatusBar();
  };

  // Set initial scope context for conditional icon
  const initialScope = vscode.workspace.getConfiguration('djobs').get<string>('scope', 'allWorkspaces');
  vscode.commands.executeCommand('setContext', 'djobs.scope', initialScope);

  const treeView = vscode.window.createTreeView('djobsTasks', {
    treeDataProvider: provider,
    showCollapseAll: true,
  });

  const refresh = vscode.commands.registerCommand('djobs.refresh', async () => {
    await provider.refresh();
  });

  const resumeAll = vscode.commands.registerCommand('djobs.resumeAll', async () => {
    const prompt = client.buildResumePrompt();
    await vscode.env.clipboard.writeText(prompt);

    const count = provider.getIncompleteCount();
    const label = count > 0 ? `Resume ${count} djobs task(s)` : 'Resume djobs tasks';
    const openChat = 'Open Chat';
    const selected = await vscode.window.showInformationMessage(
      `${label}. Prompt copied to clipboard.`,
      openChat,
    );

    if (selected === openChat) {
      await openChatWithPrompt(prompt);
    }
  });

  const copyTaskId = vscode.commands.registerCommand('djobs.copyTaskId', async (item?: TaskItem) => {
    if (!item) {
      return;
    }
    await vscode.env.clipboard.writeText(item.task.id);
    vscode.window.showInformationMessage(`Copied djobs task ID: ${item.task.id.slice(0, 8)}`);
  });

  const inspectTask = vscode.commands.registerCommand('djobs.inspectTask', async (item?: TaskItem) => {
    if (!item) {
      return;
    }
    const document = await vscode.workspace.openTextDocument({
      content: JSON.stringify(item.task, null, 2),
      language: 'json',
    });
    await vscode.window.showTextDocument(document, { preview: true });
  });

  const resumeFromHere = vscode.commands.registerCommand('djobs.resumeFromHere', async (item?: TaskItem) => {
    if (!item) { return; }
    const acceptedCount = await client.acceptBefore(item.task, `Accepted before ${item.task.id}`);
    const prompt = client.buildResumePromptForCorrelation(item.task.correlation_id ?? workspaceRoot);
    await vscode.env.clipboard.writeText(prompt);
    const selected = await vscode.window.showInformationMessage(
      `Accepted ${acceptedCount} earlier task(s). Resume prompt copied.`,
      'Open Chat',
    );
    if (selected === 'Open Chat') {
      await openChatWithPrompt(prompt);
    }
    await provider.refresh();
  });

  const skipTask = vscode.commands.registerCommand('djobs.skipTask', async (item?: TaskItem) => {
    if (!item) { return; }
    const payload = item.task.payload_json ? JSON.parse(item.task.payload_json) : {};
    const file = payload.file ?? item.task.id.slice(0, 8);
    const choice = await vscode.window.showWarningMessage(
      `Skip task: ${file}? This marks it done without editing.`,
      'Skip', 'Cancel',
    );
    if (choice !== 'Skip') { return; }
    await client.skipTask(item.task, `Skipped from VS Code sidebar: ${item.task.id}`);
    vscode.window.showInformationMessage(`Skipped: ${file}`);
    await provider.refresh();
  });

  const archiveWorkflow = vscode.commands.registerCommand('djobs.archiveWorkflow', async (item?: WorkflowGroup) => {
    const correlationId = item?.correlationId ?? workspaceRoot;
    const label = item ? `"${item.label}"` : 'current workspace';
    const choice = await vscode.window.showWarningMessage(
      `Archive all non-terminal tasks in ${label}?`,
      { modal: true },
      'Archive',
    );
    if (choice !== 'Archive') {
      return;
    }
    const count = await client.archiveByCorrelation(correlationId, 'Archived from VS Code sidebar');
    vscode.window.showInformationMessage(`Archived ${count} task(s).`);
    await provider.refresh();
  });

  const resumeWorkflow = vscode.commands.registerCommand('djobs.resumeWorkflow', async (item?: WorkflowGroup | ActionGroup) => {
    let correlationId = workspaceRoot;
    if (item instanceof WorkflowGroup) {
      correlationId = item.correlationId;
    } else if (item instanceof ActionGroup && item.tasks.length > 0) {
      correlationId = item.tasks[0].correlation_id ?? workspaceRoot;
    }
    const prompt = client.buildResumePromptForCorrelation(correlationId);
    await vscode.env.clipboard.writeText(prompt);
    const selected = await vscode.window.showInformationMessage(
      'Resume prompt copied.',
      'Open Chat',
    );
    if (selected === 'Open Chat') {
      await openChatWithPrompt(prompt);
    }
  });

  const toggleScope = vscode.commands.registerCommand('djobs.toggleScope', async () => {
    const config = vscode.workspace.getConfiguration('djobs');
    const current = config.get<string>('scope', 'allWorkspaces');
    const next = current === 'allWorkspaces' ? 'currentWorkspace' : 'allWorkspaces';
    await config.update('scope', next, vscode.ConfigurationTarget.Workspace);
    await vscode.commands.executeCommand('setContext', 'djobs.scope', next);
    const label = next === 'allWorkspaces' ? 'All workspaces' : 'Current workspace';
    vscode.window.showInformationMessage(`djobs scope: ${label}`);
    await provider.refresh();
  });

  const toggleQueueLocation = vscode.commands.registerCommand('djobs.toggleQueueLocation', async () => {
    const config = vscode.workspace.getConfiguration('djobs');
    const current = config.get<string>('queueLocation', 'workspace');
    const next = current === 'global' ? 'workspace' : 'global';
    await config.update('queueLocation', next, vscode.ConfigurationTarget.Global);

    if (next === 'global') {
      const wireUp = 'Wire up agent';
      const selected = await vscode.window.showInformationMessage(
        'djobs now reads the shared global queue (~/.djobs/global.db). '
          + 'Run "djobs install-mcp --global" in each project so the agent writes there too.',
        wireUp,
      );
      if (selected === wireUp) {
        try {
          await client.wireGlobalMcp();
          vscode.window.showInformationMessage(
            'djobs agent wired to the global queue. Reload the window if the MCP server was already running.',
          );
        } catch (error) {
          const detail = error instanceof Error ? error.message : String(error);
          vscode.window.showErrorMessage(
            `djobs: could not wire the global queue. ${detail} Try "djobs: Diagnose Setup".`,
          );
        }
      }
    } else {
      vscode.window.showInformationMessage('djobs now uses the per-workspace queue (djobs_mcp.db).');
    }
    await provider.refresh();
  });

  const diagnose = vscode.commands.registerCommand('djobs.diagnose', async () => {
    doctorChannel.clear();
    doctorChannel.show(true);
    doctorChannel.appendLine('Running djobs setup diagnostics…\n');
    try {
      const report = await client.doctor();
      let allOk = true;
      for (const check of report.checks) {
        if (!check.ok) {
          allOk = false;
        }
        doctorChannel.appendLine(`  [${check.ok ? 'OK  ' : 'FAIL'}] ${check.name}: ${check.detail}`);
      }
      doctorChannel.appendLine('');
      doctorChannel.appendLine(
        allOk ? 'All checks passed.' : 'Some checks failed — see the FAIL lines above.',
      );
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      doctorChannel.appendLine(`Could not run diagnostics: ${detail}`);
      doctorChannel.appendLine('');
      doctorChannel.appendLine('djobs may not be installed. Install it with:  pipx install djobs');
    }
  });

  const configWatcher = vscode.workspace.onDidChangeConfiguration(async (event) => {
    if (event.affectsConfiguration('djobs')) {
      const newScope = vscode.workspace.getConfiguration('djobs').get<string>('scope', 'allWorkspaces');
      await vscode.commands.executeCommand('setContext', 'djobs.scope', newScope);
      await provider.refresh();
    }
  });

  const intervalSeconds = vscode.workspace
    .getConfiguration('djobs')
    .get<number>('autoRefreshInterval', 5);
  const timer = setInterval(() => provider.refresh(), Math.max(1, intervalSeconds) * 1000);

  context.subscriptions.push(
    treeView,
    refresh,
    resumeAll,
    copyTaskId,
    inspectTask,
    resumeFromHere,
    skipTask,
    archiveWorkflow,
    resumeWorkflow,
    toggleScope,
    toggleQueueLocation,
    diagnose,
    configWatcher,
    { dispose: () => clearInterval(timer) },
  );

  await provider.refresh();
  await maybeOfferSetup(context, client, provider);
  await maybeOfferReWire(context, client, provider);
  await maybeOfferUpdate(context, client);
}

export function deactivate(): void {}

/**
 * On activation, if djobs isn't usable yet (tool missing, or the shared global
 * queue isn't wired for this project), offer a single one-click setup: install
 * djobs (isolated, via pipx when available) and wire the agent's write side.
 * Skips silently once the user opts out per workspace.
 */
async function maybeOfferSetup(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  provider: DjobsTasksProvider,
): Promise<void> {
  const installed = await client.isPackageInstalled();
  const needsWiring = client.isGlobalQueue() && !client.isGlobalMcpWired();
  if (installed && !needsWiring) {
    return;
  }

  const dismissKey = 'djobs.setup.dismissed';
  if (context.workspaceState.get<boolean>(dismissKey)) {
    return;
  }

  const setUp = 'Set up djobs';
  const dontAsk = "Don't ask again";
  const message = !installed
    ? 'djobs isn\'t installed yet. Set it up (isolated global install) so the '
      + 'sidebar and your AI agent get crash-proof task memory?'
    : 'djobs uses a shared global queue. Wire this project\'s agent to it so '
      + 'tasks it creates show up everywhere?';
  const selected = await vscode.window.showInformationMessage(message, setUp, dontAsk);

  if (selected === dontAsk) {
    await context.workspaceState.update(dismissKey, true);
    return;
  }
  if (selected !== setUp) {
    return;
  }

  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Setting up djobs…' },
      async (progress) => {
        if (!installed) {
          progress.report({ message: 'Installing djobs…' });
          await client.installPackage();
        }
        if (client.isGlobalQueue() && !client.isGlobalMcpWired()) {
          progress.report({ message: 'Wiring the agent…' });
          await client.wireGlobalMcp();
        }
      },
    );
    vscode.window.showInformationMessage(
      'djobs is set up. Reload the window if the MCP server was already running.',
    );
    await provider.refresh();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const diagnose = 'Diagnose Setup';
    const choice = await vscode.window.showErrorMessage(
      `djobs setup failed: ${detail}. If pipx is missing, install it first (pip install pipx).`,
      diagnose,
    );
    if (choice === diagnose) {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}

/**
 * When .vscode/mcp.json points the djobs agent at an interpreter that no longer
 * exists (e.g. a project `.venv` was deleted, or djobs is now only installed
 * globally), offer to re-wire it to the working runtime so the agent's tools
 * keep functioning. Skips silently once the user opts out per workspace.
 */
async function maybeOfferReWire(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  provider: DjobsTasksProvider,
): Promise<void> {
  const deadCommand = client.detectDeadMcpInterpreter();
  if (!deadCommand) {
    return;
  }
  // Only re-wire when djobs is actually usable now; otherwise the setup flow
  // (which installs it) is the right prompt, not this one.
  if (!(await client.isPackageInstalled())) {
    return;
  }

  const dismissKey = 'djobs.rewire.dismissed';
  if (context.workspaceState.get<boolean>(dismissKey)) {
    return;
  }

  const reWire = 'Re-wire';
  const dontAsk = "Don't ask again";
  const selected = await vscode.window.showWarningMessage(
    `djobs' agent is wired to a missing interpreter (${deadCommand}). `
      + 'Re-wire it to the working djobs install?',
    reWire,
    dontAsk,
  );

  if (selected === dontAsk) {
    await context.workspaceState.update(dismissKey, true);
    return;
  }
  if (selected !== reWire) {
    return;
  }

  try {
    await client.reWireMcp();
    vscode.window.showInformationMessage(
      'djobs agent re-wired. Reload the window if the MCP server was already running.',
    );
    await provider.refresh();
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const diagnose = 'Diagnose Setup';
    const choice = await vscode.window.showErrorMessage(
      `djobs: could not re-wire the agent. ${detail}`,
      diagnose,
    );
    if (choice === diagnose) {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}

async function openChatWithPrompt(prompt: string): Promise<void> {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', { query: prompt });
  } catch {
    await vscode.commands.executeCommand('workbench.action.chat.open');
  }
}

/**
 * Compare two dotted version strings. Returns true when `a` is strictly older
 * than `b`. Non-numeric/suffix parts are ignored; missing parts count as 0.
 */
export function isOlderVersion(a: string, b: string): boolean {
  const parse = (v: string): number[] =>
    v.split('.').map((p) => parseInt(p, 10)).map((n) => (Number.isNaN(n) ? 0 : n));
  const av = parse(a);
  const bv = parse(b);
  const len = Math.max(av.length, bv.length);
  for (let i = 0; i < len; i++) {
    const ai = av[i] ?? 0;
    const bi = bv[i] ?? 0;
    if (ai !== bi) {
      return ai < bi;
    }
  }
  return false;
}

/**
 * The VS Code extension auto-updates via the Marketplace, but the djobs Python
 * package (which provides the CLI/MCP server the extension drives) does not. If
 * the installed package is older than this extension, offer a one-click upgrade
 * so new commands the extension relies on actually exist. Opt-out is remembered
 * per installed version so a new release can prompt again.
 */
async function maybeOfferUpdate(
  context: vscode.ExtensionContext,
  client: DjobsClient,
): Promise<void> {
  const extVersion = context.extension.packageJSON.version as string | undefined;
  if (!extVersion) {
    return;
  }
  const installed = await client.installedVersion();
  if (!installed || !isOlderVersion(installed, extVersion)) {
    return;
  }

  const dismissKey = `djobs.updateDismissed.${installed}`;
  if (context.globalState.get<boolean>(dismissKey)) {
    return;
  }

  const update = 'Update djobs';
  const dontAsk = "Don't ask again";
  const selected = await vscode.window.showInformationMessage(
    `The djobs package (v${installed}) is older than the djobs extension `
      + `(v${extVersion}). Update it so new features work?`,
    update,
    dontAsk,
  );

  if (selected === dontAsk) {
    await context.globalState.update(dismissKey, true);
    return;
  }
  if (selected !== update) {
    return;
  }

  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: 'Updating djobs…' },
      () => client.updatePackage(),
    );
    const now = await client.installedVersion();
    vscode.window.showInformationMessage(
      now ? `djobs updated to v${now}.` : 'djobs updated.',
    );
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    const diagnose = 'Diagnose Setup';
    const choice = await vscode.window.showErrorMessage(
      `djobs update failed: ${detail}. Update manually with "pipx upgrade djobs".`,
      diagnose,
    );
    if (choice === diagnose) {
      await vscode.commands.executeCommand('djobs.diagnose');
    }
  }
}
