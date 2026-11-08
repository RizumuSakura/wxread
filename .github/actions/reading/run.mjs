import { spawn } from 'node:child_process';

const env = { ...process.env };
if (env.WXREAD_SKIP_PUSH === 'true') env.PUSH_METHOD = '';
const child = spawn('python', ['main.py', ...(env.WXREAD_CHECK_AUTH === 'true' ? ['--check-auth'] : [])], {
  env, stdio: 'inherit'
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
child.on('error', () => { process.exitCode = 1; });
child.on('exit', (code) => { process.exitCode = code ?? 1; });
