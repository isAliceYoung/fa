import assert from 'node:assert/strict';
import {privateHttpHeaders} from '../scripts/http-boundary.mjs';
const make=(host,extra={})=>privateHttpHeaders({headers:{host,...extra},socket:{remoteAddress:'127.0.0.1'}},new Set(['https://desktop.example','http://127.0.0.1:18890']));
const secure=make('desktop.example',{'x-forwarded-proto':'http','x-forwarded-for':'attacker',forwarded:'proto=http','x-webdesktop-https':'0','x-webdesktop-client-ip':'evil'});
assert.equal(secure['x-webdesktop-https'],'1');assert.equal(secure['x-webdesktop-client-ip'],'127.0.0.1');assert(!secure.forwarded);assert(!secure['x-forwarded-for']);
assert.equal(make('127.0.0.1:18890',{'x-forwarded-proto':'https','x-webdesktop-https':'1'})['x-webdesktop-https'],'0');
assert.equal(make('desktop.example:444')['x-webdesktop-https'],'0');
console.log('Private HTTP provenance and configured TLS cookie boundary: passed');
