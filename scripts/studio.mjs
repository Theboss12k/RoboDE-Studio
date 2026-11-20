// RoboDE Studio launcher.
//   node scripts/studio.mjs setup     create .venv and install the Python packages
//   node scripts/studio.mjs           API (127.0.0.1:8800) + client dev server (127.0.0.1:5190)
//   node scripts/studio.mjs api       API only (serves the built client from client/dist)
//   node scripts/studio.mjs test      pytest (extra arguments are passed on)
import {spawn, spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {delimiter, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
if (existsSync(join(root, '.env'))) process.loadEnvFile(join(root, '.env'));
const venvPython = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const python = process.env.STUDIO_PYTHON || venvPython;
const env = {...process.env, PYTHONPATH: [join(root, 'server'), process.env.PYTHONPATH].filter(Boolean).join(delimiter)};

const children = [];
function run(command, args, options) {
  const child = spawn(command, args, {stdio: 'inherit', ...options});
  children.push(child);
  child.on('exit', code => {
    if (code) process.exitCode = code;
    for (const other of children) if (other !== child && other.exitCode === null) other.kill('SIGTERM');
  });
  child.on('error', error => { console.error(error.message); process.exitCode = 1; });
  return child;
}
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => children.forEach(c => c.kill(signal)));

function needPython() {
  if (!existsSync(python)) {
    console.error(`Python not found at ${python}. Run "npm run setup" first, or set STUDIO_PYTHON.`);
    process.exit(1);
  }
}

const mode = process.argv[2] || 'dev';

if (mode === 'setup') {
  const base = process.env.STUDIO_BASE_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
  if (!existsSync(venvPython)) {
    const made = spawnSync(base, ['-m', 'venv', join(root, '.venv')], {stdio: 'inherit'});
    if (made.status) process.exit(made.status);
  }
  const installed = spawnSync(venvPython, ['-m', 'pip', 'install', '-r', join(root, 'requirements.txt')], {stdio: 'inherit'});
  process.exit(installed.status ?? 1);
}
if (mode === 'api' || mode === 'dev') { needPython(); run(python, ['-m', 'studio.api'], {cwd: root, env}); }
if (mode === 'dev') run(process.execPath, [join(root, 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '127.0.0.1', '--port', '5190', '--strictPort'], {cwd: join(root, 'client')});
if (mode === 'test') { needPython(); run(python, ['-m', 'pytest', '-c', join(root, 'pyproject.toml'), ...process.argv.slice(3)], {cwd: root, env}); }
