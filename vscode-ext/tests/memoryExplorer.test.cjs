const assert = require('node:assert/strict');
const test = require('node:test');
const Module = require('node:module');

// API contract fixtures, not a claim of visual or actual Extension Host acceptance.
class EventEmitter {
  constructor() { this.listeners = []; this.event = fn => { this.listeners.push(fn); return { dispose() {} }; }; }
  fire(value) { this.listeners.forEach(fn => fn(value)); }
  dispose() { this.listeners = []; }
}
class Uri {
  constructor(value) { this.value = value; this.fsPath = value.replace(/^file:\/\//, ''); }
  static parse(value) { return new Uri(value); }
  static file(value) { return new Uri(`file://${value}`); }
  toString() { return this.value; }
}
class TreeItem { constructor(label, state) { this.label = label; this.collapsibleState = state; } }
class ProcessExecution {
  constructor(command, args, options) { Object.assign(this, { command, args, options }); }
}
class Task { constructor(definition, scope, name, source, execution) { Object.assign(this, { definition, scope, name, source, execution }); } }
const folders = [
  { name: 'A', uri: Uri.file('/synthetic/a'), index: 0 },
  { name: 'B', uri: Uri.file('/synthetic/b'), index: 1 },
];
let confirmations = [], quick = [], inputs = [], displayed = [], launched = [], registered = [];
const disposable = () => ({ dispose() {} });
const fake = {
  EventEmitter, Uri, TreeItem, ProcessExecution, Task,
  TreeItemCollapsibleState: { None: 0, Collapsed: 1 }, TaskRevealKind: { Always: 1 },
  workspace: {
    isTrusted: true, workspaceFolders: folders,
    openTextDocument: async uri => ({ uri }),
    registerTextDocumentContentProvider: disposable, onDidCloseTextDocument: disposable,
    onDidChangeWorkspaceFolders: disposable, onDidChangeConfiguration: disposable,
    onDidGrantWorkspaceTrust: disposable,
  },
  window: {
    createTreeView: disposable,
    showTextDocument: async doc => { displayed.push(doc); },
    showWarningMessage: async (...args) => { const answer = confirmations.shift(); return typeof answer === 'function' ? answer(args) : answer; },
    showQuickPick: async () => quick.shift(), showInputBox: async () => inputs.shift(),
  },
  commands: { registerCommand: (name, fn) => { registered.push([name, fn]); return disposable(); } },
  tasks: { executeTask: async task => { launched.push(task); return {}; } },
};
class FakeClient {
  constructor(root) { this.root = root; }
  mcpServerLaunch() { return { command: 'synthetic-python', args: [], cwd: this.root, env: { DJOBS_DB: `${this.root}/synthetic.db` } }; }
}
const original = Module._load;
Module._load = function(name, parent, main) {
  if (name === 'vscode') { return fake; }
  if (name === './djobsClient') { return { DjobsClient: FakeClient }; }
  return original.call(this, name, parent, main);
};
const { MemoryExplorer, contextAddress, parseMemoryResponse, registerMemoryExplorer } = require('../out/memoryExplorer');
const { djobsCommandLaunch } = require('../out/commands');
Module._load = original;

const root = 'djobs://repo/family%3Asynthetic/';
const id = 'mem_' + 'a'.repeat(32);
const item = { id, uri: root + 'facts/' + id, title: 'Parser fact', status: 'candidate',
  authority: 'agent_proposed', stored_content_is_data: true };
const groupResult = { ok: true, root_uri: root, folders: [{ name: 'facts', uri: root + 'facts/', shown_count: 1 }] };
const leafResult = { ok: true, root_uri: root, memories: [item], truncated: false };
function reset() {
  fake.workspace.isTrusted = true; fake.workspace.workspaceFolders = folders;
  confirmations = []; quick = []; inputs = []; displayed = []; launched = []; registered = [];
}
async function setup(run) {
  reset();
  const calls = [];
  const execute = run || (async (client, args) => {
    calls.push({ root: client.root, args });
    if (args[1] === 'tree') { return JSON.stringify(args.includes('--uri') ? leafResult : groupResult); }
    if (args[1] === 'show') { return JSON.stringify({ ok: true, memories: [item] }); }
    if (args[1] === 'review') { return JSON.stringify({ ok: true, requires_human_review: true, preview: { artifact: item } }); }
    if (args[1] === 'forget') { return JSON.stringify({ ok: true, forgotten: true }); }
    return JSON.stringify({ ok: true, trace: { query_hash: 'fixture' } });
  });
  const explorer = new MemoryExplorer(execute);
  const workspace = (await explorer.getChildren())[0];
  const folder = (await explorer.getChildren(workspace))[0];
  const memory = (await explorer.getChildren(folder))[0];
  return { explorer, calls, workspace, folder, memory };
}

test('root expansion is lazy and only uses the existing read-only tree action', async () => {
  reset(); const calls = [];
  const explorer = new MemoryExplorer(async (_client, args) => { calls.push(args); return JSON.stringify(groupResult); });
  const roots = await explorer.getChildren();
  assert.equal(calls.length, 0); assert.equal(roots.length, 2);
  await explorer.getChildren(roots[0]);
  assert.deepEqual(calls[0], ['memory', 'tree', '', '--depth', '0', '--exposure', 'resume']);
  explorer.dispose();
});

test('workspace trust gates every read and explicit action', async () => {
  const { explorer, calls, memory } = await setup();
  const before = calls.length; fake.workspace.isTrusted = false;
  assert.deepEqual(await explorer.getChildren(), []);
  await explorer.open(memory); await explorer.forget(memory); await explorer.review(memory);
  await explorer.search(); await explorer.selectExposure();
  assert.equal(calls.length, before); assert.equal(launched.length, 0);
  explorer.dispose();
});

test('each workspace node binds its own process cwd', async () => {
  reset(); const rootsUsed = [];
  const explorer = new MemoryExplorer(async client => { rootsUsed.push(client.root); return JSON.stringify(groupResult); });
  const nodes = await explorer.getChildren();
  await explorer.getChildren(nodes[0]); await explorer.getChildren(nodes[1]);
  assert.deepEqual(rootsUsed, ['/synthetic/a', '/synthetic/b']); explorer.dispose();
});

test('refresh discards a delayed response and invalidates saved command nodes', async () => {
  reset(); let complete; let calls = 0;
  const explorer = new MemoryExplorer(() => { calls++; return new Promise(resolve => { complete = resolve; }); });
  const workspace = (await explorer.getChildren())[0];
  const pending = explorer.getChildren(workspace); explorer.refresh();
  complete(JSON.stringify(groupResult)); assert.deepEqual(await pending, []);
  await explorer.getChildren(workspace); assert.equal(calls, 1); explorer.dispose();
});

test('removed workspace invalidates pending and saved nodes', async () => {
  const { explorer, calls, memory } = await setup();
  fake.workspace.workspaceFolders = [folders[1]];
  const count = calls.length; await explorer.open(memory); assert.equal(calls.length, count);
  explorer.dispose();
});

test('foreign repository and oversized result arrays are rejected', async () => {
  reset(); const explorer = new MemoryExplorer(async (_client, args) => JSON.stringify(args.includes('--uri')
    ? { ...leafResult, root_uri: 'djobs://repo/family%3Aother/' } : groupResult));
  const folder = (await explorer.getChildren((await explorer.getChildren())[0]))[0];
  assert.equal((await explorer.getChildren(folder))[0].kind, 'notice'); explorer.dispose();
  assert.throws(() => contextAddress('command:executeAnything'));
  assert.throws(() => contextAddress(root + 'facts/' + id + '?execute=true'));
  assert.throws(() => parseMemoryResponse('x'.repeat(256 * 1024 + 1)));
  assert.throws(() => parseMemoryResponse('{"ok":false,"error":"secret"}'));
});

test('forged command arguments cannot create mutations or launch an interpreter', async () => {
  const { explorer, calls } = await setup(); const count = calls.length;
  await explorer.forget({ kind: 'memory', record: { id }, workspace: folders[0], generation: 0 });
  await explorer.review(undefined); assert.equal(calls.length, count); assert.equal(launched.length, 0);
  explorer.dispose();
});

test('read-only virtual documents expire on refresh and explicit document close', async () => {
  const { explorer, memory } = await setup();
  await explorer.open(memory, 1); assert.equal(displayed.length, 1);
  const uri = displayed[0].uri;
  assert.match(explorer.provideTextDocumentContent(uri), /UNTRUSTED DATA/);
  explorer.releaseDocument(uri); assert.match(explorer.provideTextDocumentContent(uri), /expired/);
  await explorer.open(memory, 2); const second = displayed[1].uri; explorer.refresh();
  assert.match(explorer.provideTextDocumentContent(second), /expired/); explorer.dispose();
});

test('L2 is an explicit requested depth, not the initial tree payload', async () => {
  const { explorer, memory, calls } = await setup(); quick.push('2 · Sources, relations and evidence');
  await explorer.selectDepth(memory);
  assert.deepEqual(calls.at(-1).args, ['memory', 'show', item.uri, '--depth', '2']); explorer.dispose();
});

test('cancelled forget is read-only and confirmed forget uses exact native ID', async () => {
  const { explorer, memory, calls } = await setup(); const count = calls.length;
  await explorer.forget(memory); assert.equal(calls.length, count);
  confirmations.push('Forget This Memory'); await explorer.forget(memory);
  assert.deepEqual(calls.at(-1).args, ['memory', 'forget', id]);
  const after = calls.length; await explorer.forget(memory); assert.equal(calls.length, after);
  explorer.dispose();
});

test('configuration refresh during a modal prevents a stale confirmed deletion', async () => {
  const { explorer, memory, calls } = await setup(); const count = calls.length;
  confirmations.push(() => { explorer.refresh(); return 'Forget This Memory'; });
  await explorer.forget(memory); assert.equal(calls.length, count); explorer.dispose();
});

test('review shows backend preview and never supplies an accept decision', async () => {
  const { explorer, memory, calls } = await setup();
  await explorer.review(memory); assert.equal(launched.length, 0);
  confirmations.push('Open Interactive Review'); await explorer.review(memory);
  assert.equal(launched.length, 1);
  const execution = launched[0].execution;
  assert.equal(execution.options.cwd, '/synthetic/a');
  assert.deepEqual(execution.args.slice(-4), ['memory', 'review', id, '--apply']);
  assert.ok(!JSON.stringify(execution).includes('ACCEPT'));
  assert.ok(calls.every(call => !call.args.includes('--apply'))); explorer.dispose();
});

test('exposure changes invalidate old nodes without modifying lifecycle', async () => {
  const { explorer, memory, calls } = await setup(); const count = calls.length;
  quick.push('audit'); await explorer.selectExposure(); await explorer.open(memory);
  assert.equal(calls.length, count); assert.match((await explorer.getChildren())[0].label, /audit/);
  assert.equal(launched.length, 0); explorer.dispose();
});

test('untrusted content remains a plain label and JSON, never a Markdown command', async () => {
  reset(); const malicious = { ...item, title: '[activate](command:exec)\u202e\n' };
  const explorer = new MemoryExplorer(async (_client, args) => JSON.stringify(args.includes('--uri')
    ? { ...leafResult, memories: [malicious] } : groupResult));
  const group = (await explorer.getChildren((await explorer.getChildren())[0]))[0];
  const node = (await explorer.getChildren(group))[0];
  assert.equal(typeof node.label, 'string'); assert.ok(!node.label.includes('\u202e'));
  assert.equal(node.command.command, 'djobs.memoryOpen'); assert.equal(typeof node.tooltip, 'string');
  explorer.dispose();
});

test('launcher passes hostile-looking argument strings as argv, not a shell expression', () => {
  const launch = djobsCommandLaunch(new FakeClient('/synthetic/a'), ['memory', 'search', 'x; touch BAD && $(whoami)']);
  assert.equal(launch.command, 'synthetic-python');
  assert.deepEqual(launch.args.slice(0, 2), ['-m', 'djobs.public_cli']);
  assert.equal(launch.args.at(-1), 'x; touch BAD && $(whoami)');
  assert.ok(!Object.hasOwn(launch, 'shell'));
});

test('canonical djobs MCP launch becomes the same public CLI without the mcp subcommand', () => {
  const client = { mcpServerLaunch: () => ({
    command: '/tools/djobs', args: ['mcp'], cwd: '/synthetic/a', env: {},
  }) };
  const launch = djobsCommandLaunch(client, ['doctor', '--json']);
  assert.equal(launch.command, '/tools/djobs');
  assert.deepEqual(launch.args, ['doctor', '--json']);
});

test('registration has no polling, implicit installation or read on construction', () => {
  reset(); const context = { subscriptions: [] }; registerMemoryExplorer(context);
  const names = registered.map(([name]) => name);
  assert.ok(names.includes('djobs.memoryReview')); assert.ok(names.includes('djobs.memoryDepth'));
  assert.ok(!names.includes('djobs.acceptSkill'));
  assert.equal(displayed.length, 0); assert.equal(launched.length, 0);
  context.subscriptions.forEach(value => value.dispose());
});


test('backend ambiguity is visible instead of being reported as ordinary empty memory', async () => {
  reset();
  const explorer = new MemoryExplorer(async (_client, args) => JSON.stringify(args.includes('--uri')
    ? { ...leafResult, memories: [], ambiguous: true, conflicts: [['left', 'right']] } : groupResult));
  const group = (await explorer.getChildren((await explorer.getChildren())[0]))[0];
  const nodes = await explorer.getChildren(group);
  assert.ok(nodes.some(node => /Unresolved conflicts/.test(node.label)));
  assert.ok(!nodes.some(node => node.kind === 'memory'));
  explorer.dispose();
});
