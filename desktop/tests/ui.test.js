// Structural and syntax checks for the desktop UI (no browser required).
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const root = path.join(__dirname, '..');

function syntax(file) {
  const r = spawnSync(process.execPath, ['--check', path.join(root, file)], { encoding: 'utf8' });
  assert.equal(r.status, 0, `${file} syntax: ${r.stderr}`);
}

test('JavaScript sources parse', () => {
  for (const file of ['main.js', 'ui/app.js']) syntax(file);
});

test('HTML exposes every element the UI binds', () => {
  const html = fs.readFileSync(path.join(root, 'ui/index.html'), 'utf8');
  for (const id of [
    'stage', 'ascii', 'viewport', 'edges', 'nodes', 'tooltip', 'inspector',
    'chat', 'chatInput', 'sendBtn', 'cancelBtn', 'chatResponse',
    'historyDrawer', 'historyList', 'historySearch', 'commsDrawer', 'commsList',
    'connText', 'fitBtn', 'resetBtn', 'historyBtn', 'commsBtn', 'banner',
  ]) {
    assert.match(html, new RegExp(`id="${id}"`), `missing #${id}`);
  }
  assert.match(html, /href="\/styles\.css"/);
  assert.match(html, /src="\/app\.js"/);
});

test('canvas styling: dark theme, edge drawers, reduced motion', () => {
  const css = fs.readFileSync(path.join(root, 'ui/styles.css'), 'utf8');
  assert.match(css, /--bg:\s*#0/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /\.drawer\.left/);
  assert.match(css, /\.drawer\.right/);
  assert.match(css, /\.node\.orchestrator/);
  assert.match(css, /@keyframes flow/);
});

test('UI talks only to the backend API (no embedded fake data)', () => {
  const js = fs.readFileSync(path.join(root, 'ui/app.js'), 'utf8');
  for (const route of ['/api/snapshot', '/api/tasks', '/api/messages', '/api/jobs/']) {
    assert.ok(js.includes(route), `missing ${route}`);
  }
  assert.ok(js.includes('2500'), 'polling interval expected');
  assert.ok(!/(fake|mock|dummy|lorem)/i.test(js), 'no placeholder content allowed');
});

test('Electron shell starts the backend and shuts it down', () => {
  const js = fs.readFileSync(path.join(root, 'main.js'), 'utf8');
  assert.match(js, /backend\.py/);
  assert.match(js, /api\/health/);
  assert.match(js, /stopBackend\(\)/);
  assert.match(js, /BrowserWindow/);
});

test('package manifest is runnable', () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
  assert.equal(pkg.main, 'main.js');
  assert.equal(pkg.scripts.start, 'electron .');
  assert.ok(pkg.devDependencies.electron);
});
