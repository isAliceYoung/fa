// Root test instrument inside the experimental network namespace only.
import WebSocket from '/opt/webdesktop/node_modules/ws/wrapper.mjs';
import fs from 'node:fs';
const app=process.argv[2],out='/root/wd-systemd-evidence/probe-'+app+'-cdp.json';
const report={app,pages:[],sandbox:null};
let pages=[];
for(let i=0;i<40;i++){
 try{pages=await(await fetch('http://127.0.0.1:9222/json',{signal:AbortSignal.timeout(1000)})).json();if(pages.some(p=>p.type==='page'))break;}catch{}
 await new Promise(r=>setTimeout(r,200));
}
report.pages=pages.map(p=>({title:p.title,url:p.url,type:p.type}));
const page=pages.find(p=>p.type==='page'&&p.webSocketDebuggerUrl);
if(page){
 const ws=new WebSocket(page.webSocketDebuggerUrl);await new Promise((r,j)=>{ws.once('open',r);ws.once('error',j);});let id=0;const waiting=new Map();
 ws.on('message',raw=>{const m=JSON.parse(raw);if(m.id){waiting.get(m.id)?.(m);waiting.delete(m.id);}});
 const call=(method,params={})=>new Promise((r,j)=>{const n=++id;waiting.set(n,r);ws.send(JSON.stringify({id:n,method,params}));const t=setTimeout(()=>{waiting.delete(n);j(new Error('CDP timeout'));},3000);t.unref();});
 report.sandbox=(await call('Runtime.evaluate',{expression:'document.body.innerText',returnByValue:true})).result?.result?.value;
 if(app==='chromium'){await call('Page.navigate',{url:'https://example.com'});await new Promise(r=>setTimeout(r,2500));report.external=(await call('Runtime.evaluate',{expression:'document.body.innerText',returnByValue:true})).result?.result?.value;}
 ws.close();
}
fs.writeFileSync(out,JSON.stringify(report,null,2));console.log(JSON.stringify({app,pages:report.pages.length,sandbox_page:!!report.sandbox}));
