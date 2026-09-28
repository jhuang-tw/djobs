// Isolated actual Extension Host acceptance; synthetic data only, no fake human acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vscode = require('vscode');

exports.run = async function run() {
  const manifestPath = process.env.DJOBS_EXPLORER_SMOKE_MANIFEST;
  assert.ok(manifestPath, 'Explicit synthetic fixture manifest is required');
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  assert.equal(manifest.synthetic_only, true);
  const checks = {};
  const fingerprint = () => Object.fromEntries(
    ['', '-wal', '-shm'].filter(suffix => fs.existsSync(manifest.database + suffix)).map(suffix => {
      const file = manifest.database + suffix;
      return [suffix, [fs.statSync(file).mtimeMs,
        crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]];
    }),
  );
  const before = fingerprint();
  try {
    assert.equal(vscode.workspace.isTrusted, true);
    const normalizeDrive = value => path.resolve(value).replace(/^[A-Z]:/, drive => drive.toLowerCase());
    assert.equal(normalizeDrive(vscode.workspace.workspaceFolders[0].uri.fsPath), normalizeDrive(manifest.workspace));
    const extension = vscode.extensions.getExtension('jhuang-tw.djobs');
    assert.ok(extension);
    const api = await extension.activate();
    const explorer = api.memoryExplorer;
    const roots = await explorer.getChildren();
    const groups = await explorer.getChildren(roots[0]);
    assert.equal(groups.length, 6);
    const facts = groups.find(node => node.address?.endsWith('/facts/'));
    assert.ok(facts);
    const nodes = await explorer.getChildren(facts);
    const item = nodes.find(node => node.record?.id === manifest.artifact_id);
    assert.ok(item, 'Synthetic accepted fact must appear through real CLI');
    checks.real_cli_tree_and_workspace_binding = true;
    await vscode.commands.executeCommand('djobsMemoryExplorer.focus');
    await explorer.open(item, 2);
    const doc = vscode.window.activeTextEditor?.document;
    assert.ok(doc && doc.uri.scheme === 'djobs-memory-view');
    const payload = JSON.parse(doc.getText());
    assert.equal(payload.memories[0].id, manifest.artifact_id);
    assert.ok(payload.memories[0].sources.length);
    assert.ok(payload.notice.includes('UNTRUSTED DATA'));
    checks.real_virtual_l2_sources = true;
    const originalText = doc.getText();
    try { await vscode.commands.executeCommand('type', { text: 'USER_TYPE_MUST_BE_REFUSED' }); } catch { /* readonly editor */ }
    assert.equal(doc.getText(), originalText);
    checks.normal_editor_typing_is_readonly = true;
    const edit = new vscode.WorkspaceEdit();
    edit.insert(doc.uri, new vscode.Position(0, 0), 'SHOULD_NOT_BE_EDITABLE');
    let writable = false;
    try { writable = await vscode.workspace.applyEdit(edit); } catch { writable = false; }
    // Trusted extension APIs are not sandboxed. They may edit an editor buffer,
    // but neither this display nor WorkspaceEdit has canonical memory authority.
    checks.privileged_workspace_edit_changed_buffer = writable;
    assert.deepEqual(fingerprint(), before);
    checks.privileged_buffer_edit_does_not_mutate_memory = true;
    explorer.refresh();
    assert.ok(explorer.provideTextDocumentContent(doc.uri).includes('expired'));
    checks.refresh_invalidates_old_content = true;
    assert.deepEqual(fingerprint(), before);
    checks.source_bytes_mtime_sidecars_unchanged = true;
    const ttyFile = path.join(path.dirname(manifestPath), 'tty-result.json');
    const code = 'import sys,json,pathlib; pathlib.Path(sys.argv[1]).write_text(json.dumps({"stdin":sys.stdin.isatty(),"stdout":sys.stdout.isatty()}), encoding="utf-8")';
    const task = new vscode.Task({ type: 'djobs-tty-smoke' }, vscode.workspace.workspaceFolders[0],
      'Synthetic TTY check', 'djobs-test',
      new vscode.ProcessExecution(manifest.python, ['-c', code, ttyFile], { cwd: manifest.workspace }), []);
    let subscription;
    const terminal = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('TTY task timeout')), 15000);
      subscription = vscode.tasks.onDidEndTaskProcess(event => {
        if (event.execution.task.definition.type === 'djobs-tty-smoke') {
          clearTimeout(timer); resolve(event.exitCode);
        }
      });
    });
    await vscode.tasks.executeTask(task);
    assert.equal(await terminal, 0); subscription.dispose();
    const tty = JSON.parse(fs.readFileSync(ttyFile, 'utf8'));
    assert.equal(tty.stdin, true); assert.equal(tty.stdout, true);
    checks.interactive_review_terminal_has_tty = true;
    checks.no_acceptance_was_submitted = true;
    fs.writeFileSync(manifest.result, JSON.stringify({ ok: true, checks, vscode: vscode.version }, null, 2));
  } catch (error) {
    fs.writeFileSync(manifest.result, JSON.stringify({ ok: false, checks, error: String(error), vscode: vscode.version }, null, 2));
    throw error;
  }
};
