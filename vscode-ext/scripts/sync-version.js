#!/usr/bin/env node
// Sync the extension version from the single source of truth:
// ../src/djobs/__init__.py (__version__). Run before compile/package.
const fs = require('fs');
const path = require('path');

const initPath = path.join(__dirname, '..', '..', 'src', 'djobs', '__init__.py');
const pkgPath = path.join(__dirname, '..', 'package.json');
const serverJsonPath = path.join(__dirname, '..', '..', 'server.json');

const initSrc = fs.readFileSync(initPath, 'utf8');
const match = initSrc.match(/__version__\s*=\s*["']([^"']+)["']/);
if (!match) {
  console.error(`Could not find __version__ in ${initPath}`);
  process.exit(1);
}
const version = match[1];

const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));
if (pkg.version !== version) {
  pkg.version = version;
  fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + '\n', 'utf8');
  console.log(`Synced extension version -> ${version}`);
} else {
  console.log(`Extension version already ${version}`);
}

// Keep the MCP Registry manifest (server.json) version in lockstep too, so the
// single source of truth stays src/djobs/__init__.py. The registry requires the
// server.json version to match the published PyPI package version.
if (fs.existsSync(serverJsonPath)) {
  const server = JSON.parse(fs.readFileSync(serverJsonPath, 'utf8'));
  let changed = false;
  if (server.version !== version) {
    server.version = version;
    changed = true;
  }
  for (const p of server.packages || []) {
    if (p.version !== version) {
      p.version = version;
      changed = true;
    }
  }
  if (changed) {
    fs.writeFileSync(serverJsonPath, JSON.stringify(server, null, 2) + '\n', 'utf8');
    console.log(`Synced server.json version -> ${version}`);
  } else {
    console.log(`server.json version already ${version}`);
  }
}
