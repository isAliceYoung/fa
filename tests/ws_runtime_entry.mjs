// Real Next lifecycle fixture; does not mock authentication or Upgrade.
import { startGateway } from '../server.mjs';
import fs from 'node:fs';
const gateway = await startGateway();
fs.writeFileSync(process.env.HOME + '/gateway-runtime.json', JSON.stringify({ public: gateway.server.address(), internal: gateway.internal.address() }), { mode: 0o600 });
const sentinel = () => { fs.writeFileSync(process.env.HOME + '/forbidden-internal-upgrade', 'unexpected'); };
gateway.internal.on('upgrade', sentinel);
gateway.internal.on('upgrade', sentinel);
setTimeout(() => gateway.internal.prependListener('upgrade', sentinel), 1000).unref();
for (const name of ['SIGINT', 'SIGTERM']) process.once(name, () => gateway.close().finally(() => process.exit(0)));
