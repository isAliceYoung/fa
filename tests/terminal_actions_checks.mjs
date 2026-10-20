import assert from 'node:assert/strict';
import { terminalAction } from '../src/components/desktop/terminal-actions.mjs';
const enc = s => Buffer.from(s).toString('base64');
assert.equal(terminalAction(`gui;${enc('pcmanfm')};${enc('~')}`).cmd, 'pcmanfm');
assert.equal(terminalAction('open;dir;~/Documents').app, 'sysfiles');
assert.equal(terminalAction('open;file;~/Documents/n.txt').app, 'sysedit');
for (const value of ['open;url;https://example.com','open;file;/root/.env','open;dir;~/../.git','open;dir;~/a/../../root','open;dir;~\\root','open;file;~/a\nfoo','open;file;~/a;cmd','x'.repeat(4096),`gui;${enc('pcmanfm; touch /tmp/pwn')};${enc('~')}`,`gui;${enc('bash')};${enc('~')}`,`gui;${enc('pcmanfm')};${enc('/root')}`,'gui;%%%;;']) assert.equal(terminalAction(value), null, value);
console.log('Terminal action whitelist: passed');
