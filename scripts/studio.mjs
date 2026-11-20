// RoboDE Studio launcher.
//   node scripts/studio.mjs setup     create .venv (Python packages) and build the Java server (server-java/target)
//   node scripts/studio.mjs           Java API (127.0.0.1:8800) + client dev server (127.0.0.1:5190)
//   node scripts/studio.mjs api       Java API only (serves the built client from client/dist)
//   node scripts/studio.mjs test      ./mvnw verify, then pytest (extra arguments go to pytest), then contracts:check;
//                                     runs all three in order and exits non-zero if any failed
import {spawn, spawnSync} from 'node:child_process';
import {existsSync, readdirSync} from 'node:fs';
import {delimiter, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const win = process.platform === 'win32';
if (existsSync(join(root, '.env'))) process.loadEnvFile(join(root, '.env'));
const venvPython = resolve(root, win ? '.venv/Scripts/python.exe' : '.venv/bin/python');
const python = process.env.STUDIO_PYTHON || venvPython;
const serverJava = join(root, 'server-java');
const maven = join(serverJava, win ? 'mvnw.cmd' : 'mvnw');
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

// Runs one step to completion and returns its exit status (1 if it could not start).
function step(label, command, args, options = {}) {
  console.log(`\n== ${label}`);
  const result = spawnSync(command, args, {stdio: 'inherit', shell: win && command.endsWith('.cmd'), ...options});
  if (result.error) console.error(result.error.message);
  return result.error ? 1 : (result.status ?? 1);
}

function needPython() {
  if (!existsSync(python)) {
    console.error(`Python not found at ${python}. Run "npm run setup" first, or set STUDIO_PYTHON.`);
    process.exit(1);
  }
}

// The runnable jar that `package` writes (studio-server-<version>.jar; the .original copy is not runnable).
function jarPath() {
  const dir = join(serverJava, 'target');
  const jar = existsSync(dir) ? readdirSync(dir).find(f => /^studio-server-.*\.jar$/.test(f)) : undefined;
  return jar ? join(dir, jar) : null;
}

function buildJava() {
  return step('Build the Java server', maven, ['-q', 'package', '-DskipTests'], {cwd: serverJava});
}

const mode = process.argv[2] || 'dev';

if (mode === 'setup') {
  const base = process.env.STUDIO_BASE_PYTHON || (win ? 'python' : 'python3');
  if (!existsSync(venvPython)) {
    const made = spawnSync(base, ['-m', 'venv', join(root, '.venv')], {stdio: 'inherit'});
    if (made.status) process.exit(made.status);
  }
  const installed = spawnSync(venvPython, ['-m', 'pip', 'install', '-r', join(root, 'requirements.txt')], {stdio: 'inherit'});
  if (installed.status) process.exit(installed.status);
  process.exit(buildJava());
}

if (mode === 'api' || mode === 'dev') {
  needPython();
  if (!jarPath() && buildJava()) process.exit(1);
  const jar = jarPath();
  if (!jar) { console.error('The Java server jar was not built.'); process.exit(1); }
  run('java', ['-jar', jar], {cwd: root, env: {...env, STUDIO_PYTHON: python}});
}
if (mode === 'dev') run(process.execPath, [join(root, 'node_modules', 'vite', 'bin', 'vite.js'), '--host', '127.0.0.1', '--port', '5190', '--strictPort'], {cwd: join(root, 'client')});

if (mode === 'test') {
  needPython();
  const results = {
    'Java tests (./mvnw verify)': step('Java tests (./mvnw verify)', maven, ['verify'], {cwd: serverJava}),
    'Python tests (pytest)': step('Python tests (pytest)', python, ['-m', 'pytest', '-c', join(root, 'pyproject.toml'), ...process.argv.slice(3)], {cwd: root, env}),
    'Contracts check': step('Contracts check (npm run contracts:check)', win ? 'npm.cmd' : 'npm', ['run', 'contracts:check'], {cwd: root}),
  };
  const failed = Object.entries(results).filter(([, status]) => status !== 0).map(([name]) => name);
  console.log(`\n== Summary: ${failed.length ? `failed: ${failed.join(', ')}` : 'all passed'}`);
  process.exit(failed.length ? 1 : 0);
}
