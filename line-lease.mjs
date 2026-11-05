import {spawn} from 'node:child_process';
export async function acquireLineLease(context, path='D:/CodexTelegram/profile/line-browser.lock') {
 const code="import sys;from pathlib import Path;sys.path.insert(0,'D:/CodexTelegram/bridge');from lifecycle import SingleInstance;lock=SingleInstance(Path(sys.argv[1]));print('locked',flush=True);sys.stdin.read();lock.close()";
 const child=spawn('D:/CodexTelegram/runtime/gateway-venv/Scripts/python.exe',['-B','-u','-c',code,path],{windowsHide:true,stdio:['pipe','pipe','ignore']});
 try {
  await new Promise((resolve,reject)=>{
   const timer=setTimeout(()=>reject(Error('LINE lease unavailable')),5000);
   child.stdout.once('data',data=>{clearTimeout(timer);data.toString().trim()==='locked'?resolve():reject(Error('LINE busy'));});
   child.once('error',()=>{clearTimeout(timer);reject(Error('LINE lease unavailable'));});
   child.once('exit',()=>{clearTimeout(timer);reject(Error('LINE busy'));});
  });
 } catch(error) {child.stdin.end();child.kill();throw error;}
 context.once('close',()=>child.stdin.end());
 // Losing the lease must end this browser rather than permit concurrent operations.
 child.once('exit',()=>{context.close().catch(()=>{});});
 return child;
}
