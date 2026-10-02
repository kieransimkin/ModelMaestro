import http from 'node:http';
import next from 'next';
import httpProxy from 'http-proxy';

const dev = process.argv.includes('--dev');
const host = process.env.HOST || '127.0.0.1';
const port = Number(process.env.PORT || 3000);
const origin = new URL(process.env.APP_ORIGIN || `http://127.0.0.1:${port}`);
const apiPort = Number(process.env.BACKEND_PORT || 8765);
const allowedHosts = new Set([origin.host, `127.0.0.1:${port}`, `localhost:${port}`]);
const app = next({dev, hostname: host, port});
await app.prepare();
const handle = app.getRequestHandler();
const upgrade = app.getUpgradeHandler();
const proxy = httpProxy.createProxyServer({
  target: `http://127.0.0.1:${apiPort}`, ws: true,
  // Do not buffer streaming output or rewrite Origin / authentication headers.
  proxyTimeout: 0, timeout: 0,
});
proxy.on('error', (error, req, res) => {
  console.error('Backend proxy:', error.message);
  if (res && typeof res.writeHead === 'function' && !res.headersSent) {
    res.writeHead(502, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({detail: 'Backend unavailable; check launcher logs.'}));
  } else res?.destroy();
});
const isAPI = url => url?.startsWith('/api/') || url?.startsWith('/socket.io/');
const server = http.createServer((req, res) => {
  // Host checking also blocks browser DNS-rebinding against local services.
  if (!allowedHosts.has(req.headers.host || '')) {
    res.writeHead(403); res.end('Unrecognised Host'); return;
  }
  if (isAPI(req.url)) proxy.web(req, res);
  else handle(req, res);
});
server.on('upgrade', (req, socket, head) => {
  if (!allowedHosts.has(req.headers.host || '')) { socket.destroy(); return; }
  if (req.url?.startsWith('/socket.io/')) proxy.ws(req, socket, head);
  else upgrade(req, socket, head);
});
server.listen(port, host, () => console.log(`ModelMaestro: ${origin.origin}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => {
  proxy.close(); server.close(); process.exit(0);
});
