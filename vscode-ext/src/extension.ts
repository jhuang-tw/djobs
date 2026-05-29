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
        const terminal = vscode.window.createTerminal('djobs');
        terminal.show();
        terminal.sendText('python -m djobs.cli install-mcp --global --force');
      }
    } else {
      vscode.window.showInformationMessage('djobs now uses the per-workspace queue (djobs_mcp.db).');
    }
    await provider.refresh();
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
    configWatcher,
    { dispose: () => clearInterval(timer) },
  );

  await provider.refresh();
  await maybeOfferPackageInstall(context, client, provider);
  await maybeOfferGlobalWiring(context, client, provider);
}

export function deactivate(): void {}

/**
 * When the djobs Python package is missing from the detected interpreter,
 * offer a one-click `pip install djobs` so the sidebar and agent can work.
 * Skips silently once the user opts out per workspace.
 */
async function maybeOfferPackageInstall(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  provider: DjobsTasksProvider,
): Promise<void> {
  if (await client.isPackageInstalled()) {
    return;
  }
  const dismissKey = 'djobs.packageInstall.dismissed';
  if (context.workspaceState.get<boolean>(dismissKey)) {
    return;
  }

  const install = 'Install (pip)';
  const dontAsk = "Don't ask again";
  const selected = await vscode.window.showInformationMessage(
    'The djobs Python package is not installed for this project\'s interpreter. '
      + 'Install it so the sidebar and agent can run?',
    install,
    dontAsk,
  );

  if (selected === install) {
    try {
      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: 'Installing djobs…' },
        () => client.installPackage(),
      );
      vscode.window.showInformationMessage('djobs installed.');
      await provider.refresh();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(
        `djobs: pip install failed. Install it manually with "pip install djobs". ${detail}`,
      );
    }
  } else if (selected === dontAsk) {
    await context.workspaceState.update(dismissKey, true);
  }
}

/**
 * When the shared global queue is active but this workspace's agent (MCP
 * server) is not yet wired to it, offer a one-click setup so reads and writes
 * share the same queue. Skips silently once the user opts out per workspace.
 */
async function maybeOfferGlobalWiring(
  context: vscode.ExtensionContext,
  client: DjobsClient,
  provider: DjobsTasksProvider,
): Promise<void> {
  if (!client.isGlobalQueue() || client.isGlobalMcpWired()) {
    return;
  }
  const dismissKey = 'djobs.globalWiring.dismissed';
  if (context.workspaceState.get<boolean>(dismissKey)) {
    return;
  }

  const wireUp = 'Set up now';
  const dontAsk = "Don't ask again";
  const selected = await vscode.window.showInformationMessage(
    'djobs uses a shared global queue. Wire this project\'s agent to it so '
      + 'tasks it creates show up everywhere?',
    wireUp,
    dontAsk,
  );

  if (selected === wireUp) {
    try {
      await client.wireGlobalMcp();
      vscode.window.showInformationMessage(
        'djobs agent wired to the global queue. Reload the window if the MCP server was already running.',
      );
      await provider.refresh();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(`djobs: could not wire the global queue. ${detail}`);
    }
  } else if (selected === dontAsk) {
    await context.workspaceState.update(dismissKey, true);
  }
}

async function openChatWithPrompt(prompt: string): Promise<void> {
  try {
    await vscode.commands.executeCommand('workbench.action.chat.open', { query: prompt });
  } catch {
    await vscode.commands.executeCommand('workbench.action.chat.open');
  }
}
