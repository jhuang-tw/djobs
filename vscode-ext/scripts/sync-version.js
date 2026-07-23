#!/usr/bin/env node
// Synchronize every published version from src/djobs/__init__.py.
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..', '..');
const initPath = path.join(root, 'src', 'djobs', '__init__.py');
const pkgPath = path.join(root, 'vscode-ext', 'package.json');
const lockPath = path.join(root, 'vscode-ext', 'package-lock.json');
const serverPath = path.join(root, 'server.json');

const initSrc = fs.readFileSync(initPath, 'utf8');
const match = initSrc.match(/__version__\s*=\s*["']([^"']+)["']/);
if (!match) {
  console.error(`Could not find __version__ in ${initPath}`);
  process.exit(1);
}
const version = match[1];

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function saveJson(file, value) {
  fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\n', 'utf8');
}

const pkg = loadJson(pkgPath);
if (pkg.version !== version) {
  pkg.version = version;
  saveJson(pkgPath, pkg);
  console.log(`Synced extension version -> ${version}`);
} else {
  console.log(`Extension version already ${version}`);
}

const lock = loadJson(lockPath);
let lockChanged = false;
if (lock.version !== version) {
  lock.version = version;
  lockChanged = true;
}
if (lock.packages && lock.packages[''] && lock.packages[''].version !== version) {
  lock.packages[''].version = version;
  lockChanged = true;
}
if (lockChanged) {
  saveJson(lockPath, lock);
  console.log(`Synced extension lock version -> ${version}`);
} else {
  console.log(`Extension lock version already ${version}`);
}

const server = loadJson(serverPath);
let serverChanged = false;
if (server.version !== version) {
  server.version = version;
  serverChanged = true;
}
for (const publishedPackage of server.packages || []) {
  if (publishedPackage.version !== version) {
    publishedPackage.version = version;
    serverChanged = true;
  }
}
if (serverChanged) {
  saveJson(serverPath, server);
  console.log(`Synced server.json version -> ${version}`);
} else {
  console.log(`server.json version already ${version}`);
}
