"""Real authenticated Next + wdguest IPC, actual loop filesystem quota and IO."""
from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
import urllib.parse
import uuid
import zipfile
from linux_guest_layer import GuestLayerTests as F

class FileRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): F.setUpClass()
    @classmethod
    def tearDownClass(cls): F.tearDownClass()
    def fresh(self,prefix='file'):return prefix+'-'+uuid.uuid4().hex
    def create(self,name=None,kind='file',content='',directory='~'):
        status,data,_=F.web('/api/sys/fs','POST',{'dir':directory,'name':name or self.fresh(),'kind':kind,'content':content})
        self.assertEqual(status,200,data[:300])
        return json.loads(data)['path']
    def upload_url(self,name,key,offset=0,done=False,directory='~'):
        return '/api/sys/upload?'+urllib.parse.urlencode({'dir':directory,'name':name,'id':key,'offset':offset,'done':'1' if done else '0'})
    def binary(self,path,body=None,method='GET',auth=True,chunked=False):
        c=http.client.HTTPConnection('127.0.0.1',3000,timeout=35)
        headers={'Content-Type':'application/octet-stream'}
        if auth:headers.update(Cookie=F.cookie,**{'X-Desktop-Session':F.session})
        try:
            c.request(method,path,body=body,headers=headers,encode_chunked=chunked)
            r=c.getresponse();return r.status,r.read(),r.headers
        finally:c.close()
    def patch(self,b):return F.web('/api/sys/fs','PATCH',b)
    def download(self,p):return self.binary('/api/sys/download?path='+urllib.parse.quote(p))

    def test_copy_move_rename_and_atomic_edit(self):
        source=self.create(content='before');dest=self.create(kind='folder')
        code,data,_=self.patch({'copyPaths':[source],'destDir':dest})
        self.assertEqual(code,200,data)
        copied=json.loads(data)['paths'][0]
        self.assertEqual(self.download(copied)[1],b'before')
        code,data,_=self.patch({'path':copied,'name':'renamed.txt'})
        self.assertEqual(code,200,data);renamed=json.loads(data)['path']
        self.assertEqual(F.web('/api/sys/fs','PUT',{'path':renamed,'content':'after'})[0],200)
        self.assertEqual(self.download(source)[1],b'before')
        self.assertEqual(self.download(renamed)[1],b'after')
        self.assertEqual(self.patch({'path':renamed,'moveTo':'~'})[0],200)

    def test_recursive_copy_is_complete_and_source_retained(self):
        source=self.create(kind='folder');sub=self.create('sub',kind='folder',directory=source)
        self.create('data.txt',content='tree',directory=sub)
        dest=self.create(kind='folder')
        code,data,_=self.patch({'copyPaths':[source],'destDir':dest})
        self.assertEqual(code,200,data)
        self.assertEqual(self.download(json.loads(data)['paths'][0]+'/sub/data.txt')[1],b'tree')

    def test_trash_restore_permanent_and_empty(self):
        source=self.create(content='restore')
        code,data,_=F.web('/api/sys/fs?path='+urllib.parse.quote(source),'DELETE')
        self.assertEqual(code,200,data);trash=json.loads(data)['path']
        self.assertEqual(self.download(source)[0],404)
        self.assertEqual(self.patch({'path':trash,'restore':True})[0],200)
        self.assertEqual(self.download(source)[1],b'restore')
        self.assertEqual(F.web('/api/sys/fs?path='+urllib.parse.quote(source)+'&permanent=1','DELETE')[0],200)
        self.assertEqual(self.download(source)[0],404)
        other=self.create(content='trash')
        self.assertEqual(F.web('/api/sys/fs?path='+urllib.parse.quote(other),'DELETE')[0],200)
        self.assertEqual(F.web('/api/sys/fs?emptyTrash=1','DELETE')[0],200)

    def test_partial_batch_reports_created_paths_and_preserves_failed_sources(self):
        source=self.create(content='copy');dest=self.create(kind='folder')
        code,data,_=self.patch({'copyPaths':[source,str(F.root_file)],'destDir':dest})
        self.assertEqual(code,409,data)
        value=json.loads(data);self.assertTrue(value['partial']);self.assertEqual(value['count'],1)
        self.assertEqual(self.download(source)[1],b'copy')
        self.assertEqual(self.download(value['paths'][0])[1],b'copy')

    def test_nested_symlinks_and_hardlinks_reject_copy_and_zip(self):
        for hard in (False,True):
            directory=self.create(kind='folder');dest=self.create(kind='folder')
            good=self.create('good.txt',content='safe',directory=directory)
            link=Path(directory)/'bad'
            if hard:os.link(good,link)
            else:os.symlink(F.service_file,link)
            self.assertEqual(self.patch({'copyPaths':[directory],'destDir':dest})[0],403)
            self.assertEqual(self.download(directory)[0],403)
            link.unlink()

    def test_path_traversal_absolute_protected_and_internal_paths_rejected(self):
        for p in ('/etc/passwd','../root','~/.wd-state','~/Desktop/../../root',str(F.service_file)):
            self.assertIn(self.download(p)[0],(403,404))
            self.assertIn(F.web('/api/sys/fs?path='+urllib.parse.quote(p)+'&permanent=1','DELETE')[0],(403,404))
        self.assertEqual(self.patch({'copyPaths':[str(F.service_file)],'destDir':'~'})[0],403)

    def test_cross_filesystem_destination_fails_without_removing_source(self):
        mount=self.create(kind='folder');source=self.create(content='retained')
        subprocess.run(['mount','-t','tmpfs','-o','size=1m,nosuid,nodev','none',mount],check=True)
        os.chown(mount,F.guest.pw_uid,F.guest.pw_gid)
        try:self.assertEqual(self.patch({'copyPaths':[source],'destDir':mount,'cut':True})[0],403)
        finally:subprocess.run(['umount',mount],check=True)
        self.assertEqual(self.download(source)[1],b'retained')

    def test_single_binary_upload_and_download(self):
        name=self.fresh();url=self.upload_url(name,uuid.uuid4().hex,done=True)
        data=os.urandom(1024*1024)
        code,body,_=self.binary(url,data,'POST')
        self.assertEqual(code,200,body[:300])
        self.assertEqual(hashlib.sha256(self.download(json.loads(body)['path'])[1]).digest(),hashlib.sha256(data).digest())

    def test_chunked_upload_resume_survives_actual_agent_restart(self):
        name=self.fresh();key=uuid.uuid4().hex;url=self.upload_url(name,key)
        first=b'a'*200000;second=b'b'*300000
        self.assertEqual(self.binary(url,iter([first[:50000],first[50000:]]),'POST',chunked=True)[0],200)
        F.stop_agent();F.start_agent()
        code,body,_=self.binary(url);self.assertEqual(code,200,body)
        self.assertEqual(json.loads(body)['offset'],len(first))
        code,body,_=self.binary(self.upload_url(name,key,len(first),True),second,'POST')
        self.assertEqual(code,200,body)
        self.assertEqual(self.download(json.loads(body)['path'])[1],first+second)

    def test_upload_offset_conflict_does_not_truncate_committed_data(self):
        name=self.fresh();key=uuid.uuid4().hex;url=self.upload_url(name,key)
        self.assertEqual(self.binary(url,b'abc','POST')[0],200)
        self.assertEqual(self.binary(url,b'wrong','POST')[0],409)
        self.assertEqual(json.loads(self.binary(url)[1])['offset'],3)
        code,body,_=self.binary(self.upload_url(name,key,3,True),b'def','POST')
        self.assertEqual(code,200,body);self.assertEqual(self.download(json.loads(body)['path'])[1],b'abcdef')

    def test_cancel_upload_and_completed_cancel_never_deletes_published_file(self):
        name=self.fresh();key=uuid.uuid4().hex;url=self.upload_url(name,key)
        self.assertEqual(self.binary(url,b'cancel','POST')[0],200)
        self.assertEqual(self.binary(url,method='DELETE')[0],200)
        self.assertEqual(self.binary(url)[0],404)
        code,body,_=self.binary(self.upload_url(name,key,done=True),b'keep','POST')
        self.assertEqual(code,200,body);published=json.loads(body)['path']
        self.assertEqual(self.binary(url,method='DELETE')[0],200)
        self.assertEqual(self.download(published)[1],b'keep')

    def test_oversized_chunk_rolls_back(self):
        name=self.fresh();key=uuid.uuid4().hex;url=self.upload_url(name,key)
        code,body,_=self.binary(url,iter([b'x'*65536]*257),'POST',chunked=True)
        self.assertEqual(code,413,body[:200])
        self.assertEqual(json.loads(self.binary(url)[1])['offset'],0)
        self.assertEqual(self.binary(url,method='DELETE')[0],200)

    def test_interrupted_network_upload_rolls_back(self):
        name=self.fresh();key=uuid.uuid4().hex;url=self.upload_url(name,key)
        with socket.create_connection(('127.0.0.1',3000),timeout=10) as s:
            headers='POST '+url+' HTTP/1.1\r\nHost: localhost\r\nCookie: '+F.cookie+'\r\nX-Desktop-Session: '+F.session+'\r\nContent-Type: application/octet-stream\r\nContent-Length: 100000\r\n\r\n'
            s.sendall(headers.encode()+b'x'*20000)
            time.sleep(.2)
        time.sleep(.5)
        code,body,_=self.binary(url)
        self.assertEqual(code,200,body);self.assertEqual(json.loads(body)['offset'],0)

    def test_upload_mkdir_and_malicious_path(self):
        root=self.fresh('nested')
        self.assertEqual(self.binary('/api/sys/upload?mkdir=1&dir=~&name='+root+'/sub',b'','POST')[0],200)
        for name,directory in (('../escape','~'),('/root/private','~'),('bad','/root')):
            self.assertEqual(self.binary(self.upload_url(name,uuid.uuid4().hex,directory=directory),b'x','POST')[0],403)

    def test_zip_stream_names_empty_folders_and_contents(self):
        directory=self.create(kind='folder');self.create('empty',kind='folder',directory=directory)
        self.create('data.txt',content='archive',directory=directory)
        self.create('中文.txt',content='你好',directory=directory)
        code,body,headers=self.download(directory)
        self.assertEqual(code,200,body[:100]);self.assertEqual(headers.get('Content-Type'),'application/zip')
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertTrue(all(not n.startswith('/') and '..' not in n.split('/') for n in z.namelist()))
            self.assertEqual(z.read(Path(directory).name+'/data.txt'),b'archive')
            self.assertIn(Path(directory).name+'/empty/',z.namelist())
            self.assertEqual(z.read(Path(directory).name+'/中文.txt'),'你好'.encode())

    def test_archive_rejects_nonportable_names_and_oversized_trees(self):
        directory=self.create(kind='folder')
        for name in ('C:escape','bad\\..\\escape'):
            p=Path(directory)/name;p.write_text('untrusted');os.chown(p,F.guest.pw_uid,F.guest.pw_gid)
            self.assertEqual(self.download(directory)[0],403)
            p.unlink()
        for n in ('first','second'):
            p=Path(directory)/n
            with p.open('wb') as f:f.truncate(60*1024*1024)
            os.chown(p,F.guest.pw_uid,F.guest.pw_gid)
        code,body,_=self.download(directory)
        self.assertEqual(code,413);self.assertLess(len(body),1000)
        dest=self.create(kind='folder')
        self.assertEqual(self.patch({'copyPaths':[directory],'destDir':dest})[0],413)

    def test_publication_crash_recovers_durable_intent_without_duplicate(self):
        name=self.fresh('crash-publish');key=uuid.uuid4().hex
        log=open(F.root/'syscall-fault.txt','wb')
        tracer=subprocess.Popen(['strace','-f','-qq','-e','trace=renameat2','-e','inject=renameat2:signal=SIGKILL:when=4','-p',str(F.agent.pid)],stdout=log,stderr=subprocess.STDOUT)
        try:
            time.sleep(.2);self.assertIsNone(tracer.poll())
            code,body,_=self.binary(self.upload_url(name,key,done=True),b'durable-publication','POST')
            self.assertEqual(code,503,body+Path(log.name).read_bytes()[-1500:])
            F.agent.wait(timeout=10);tracer.wait(timeout=10)
            self.assertEqual(F.agent.returncode,-9)
        finally:
            if tracer.poll() is None:tracer.terminate();tracer.wait(timeout=10)
            log.close();F.stop_agent();F.start_agent()
        code,body,_=self.binary(self.upload_url(name,key))
        self.assertEqual(code,200,body);value=json.loads(body);self.assertTrue(value['complete'])
        self.assertEqual(self.download(value['path'])[1],b'durable-publication')
        self.assertEqual(self.binary(self.upload_url(name,key),method='DELETE')[0],200)
        self.assertTrue(Path(value['path']).is_file())

    def test_cancelled_zip_stream_releases_temporary_archive(self):
        directory=self.create(kind='folder');p=Path(directory)/'random.bin'
        p.write_bytes(os.urandom(4*1024*1024));os.chown(p,F.guest.pw_uid,F.guest.pw_gid)
        before=set((F.home/'.wd-state').glob('tmp-*'))
        free_before=os.statvfs(F.home).f_bavail
        c=http.client.HTTPConnection('127.0.0.1',3000,timeout=20)
        c.request('GET','/api/sys/download?path='+urllib.parse.quote(directory),headers={'Cookie':F.cookie,'X-Desktop-Session':F.session})
        r=c.getresponse();self.assertEqual(r.status,200);r.read(1024);r.close();c.close()
        for _ in range(60):
            if os.statvfs(F.home).f_bavail>=free_before-16:break
            time.sleep(.05)
        self.assertEqual(set((F.home/'.wd-state').glob('tmp-*')),before)
        self.assertGreaterEqual(os.statvfs(F.home).f_bavail,free_before-16)

    def test_concurrent_edits_publish_whole_files(self):
        target=self.create(content='initial');values=[str(i)*100000 for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            statuses=list(pool.map(lambda value:F.web('/api/sys/fs','PUT',{'path':target,'content':value})[0],values))
        self.assertIn(200,statuses);self.assertTrue(set(statuses)<={200,409,503})
        self.assertIn(self.download(target)[1].decode(),values)

    def test_kernel_capacity_enforced_and_full_disk_write_preserves_original(self):
        v=os.statvfs(F.home);self.assertLessEqual(v.f_blocks*v.f_frsize,512*1024*1024)
        self.assertTrue(v.f_flag & os.ST_NOSUID)
        original=self.create(content='original');fill=F.home/self.fresh('quota-fill')
        code="""import os,sys,json
f=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
 while True:os.write(f,b'x'*65536)
except OSError as e: print(json.dumps({'errno':e.errno,'bytes':os.fstat(f).st_size}))
finally:os.close(f)
"""
        try:
            r=subprocess.run(['/usr/bin/python3','-I','-c',code,str(fill)],user=F.guest.pw_uid,group=F.guest.pw_gid,extra_groups=[],capture_output=True,text=True,timeout=45)
            self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout)['errno'],errno.ENOSPC)
            status,body,_=F.web('/api/sys/fs','PUT',{'path':original,'content':'z'*1024*1024})
            self.assertEqual(status,507,body[:200]);self.assertEqual(self.download(original)[1],b'original')
            self.assertEqual(F.web('/api/health',auth=False)[0],200)
            self.assertEqual(F.web('/api/sys/fs?path='+urllib.parse.quote(str(fill))+'&permanent=1','DELETE')[0],200)
        finally:fill.unlink(missing_ok=True)
        self.assertGreater(os.statvfs(F.home).f_bavail,0)

    def test_unbounded_home_refuses_agent_without_fallback(self):
        F.stop_agent()
        subprocess.run(['umount',str(F.home)],check=True)
        try:
            with self.assertRaises(RuntimeError):F.start_agent()
        finally:
            F.stop_agent()
            subprocess.run(['mount','-o','loop,rw,nosuid,nodev,noexec','/var/lib/webdesktop-guest-volume/workspace.ext4',str(F.home)],check=True)
            F.start_agent()
        self.assertEqual(F.web('/api/sys/info?lite=1')[0],200)

    def test_new_mutations_require_authentication(self):
        self.assertEqual(F.web('/api/sys/fs','PATCH',{'path':'test','name':'new'},auth=False)[0],401)
        self.assertEqual(self.binary(self.upload_url(self.fresh(),uuid.uuid4().hex),b'data','POST',auth=False)[0],401)
        self.assertEqual(self.binary('/api/sys/download?path=~',auth=False)[0],401)

    def test_browser_upload_controller_recovers_lost_reply_and_cancels(self):
        directory=self.create(kind='folder')
        script="""import assert from 'node:assert/strict';
import {uploadFile} from './src/components/desktop/upload-file.mjs';
let input='';for await(const chunk of process.stdin)input+=chunk;
const cfg=JSON.parse(input),native=globalThis.fetch;
let drop=true;
globalThis.fetch=async(url,options={})=>{
 const r=await native('http://127.0.0.1:3000'+url,{...options,headers:{Cookie:cfg.cookie,'X-Desktop-Session':cfg.session}});
 if(options.method==='POST'&&drop){drop=false;await r.arrayBuffer();throw new Error('Injected lost reply after real commit');}
 return r;
};
const records=new Map(),storage={getItem:k=>records.get(k),setItem:(k,v)=>records.set(k,v),removeItem:k=>records.delete(k)};
const file=new File([new Uint8Array(9*1024*1024)],'browser.bin',{lastModified:123});
const result=await uploadFile(cfg.dir,'browser.bin',file,{storage});
assert.equal(result.complete,true);assert.equal(records.size,0);
const controller=new AbortController();
await assert.rejects(()=>uploadFile(cfg.dir,'cancel.bin',file,{storage,signal:controller.signal,onProgress:off=>{if(off>=8*1024*1024)controller.abort();}}),{name:'AbortError'});
assert.equal(records.size,0);
console.log(JSON.stringify({path:result.path,complete:true,cancelled:true}));
"""
        node=subprocess.check_output(['which','node'],text=True).strip()
        result=subprocess.run([node,'--input-type=module','-e',script],input=json.dumps({'cookie':F.cookie,'session':F.session,'dir':directory}),capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr[-2000:])
        value=json.loads(result.stdout)
        self.assertTrue(value['complete']);self.assertTrue(value['cancelled'])
        rows=json.loads(F.web('/api/sys/fs?path='+urllib.parse.quote(directory))[1])['entries']
        self.assertEqual([row['name'] for row in rows],['browser.bin'])

if __name__=='__main__':
    r=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FileRuntimeTests))
    summary=dict(total=r.testsRun,passed=max(0,r.testsRun-len(r.failures)-len(r.errors)-len(r.skipped)),failures=len(r.failures),errors=len(r.errors),skipped=len(r.skipped))
    Path('/tmp/webdesktop-files-runtime.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary));sys.exit(0 if r.wasSuccessful() and not r.skipped else 1)
