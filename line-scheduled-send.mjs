import fs from 'node:fs';
import readline from 'node:readline';
import {chromium} from 'file:///D:/CodexTelegram/runtime/browser/node_modules/playwright/index.mjs';
import {login} from './line-autologin.mjs';
import {prepare,sendOnce} from './line-send-flow.mjs';
const input=readline.createInterface({input:process.stdin})[Symbol.asyncIterator]();
const say=value=>process.stdout.write(JSON.stringify(value)+'\n');
let context,allowed=false;
try{
 const first=await input.next();if(first.done)throw Error();
 const options=JSON.parse(first.value);
 const cfg=JSON.parse(fs.readFileSync(options.config,'utf8'));
 if(cfg.browser.userDataDir.replaceAll('\\','/').toLowerCase()!=='d:/codextelegram/profile/line-browser')throw Error();
 try{context=await chromium.launchPersistentContext(cfg.browser.userDataDir,{...cfg.browser.launchOptions,headless:true});}
 catch(error){say({stage:/already in use|ProcessSingleton|SingletonLock|browser is already running/i.test(String(error))?'busy':'blocked'});process.exitCode=1;}
 if(context){
  process.stdin.once('end',()=>{context.close().catch(()=>{});});
  const page=context.pages()[0]||await context.newPage();
  await login(page);
  const prepared=await prepare(page,options.recipient,options.text);
  say({stage:'ready',recipient:prepared.recipient,room:prepared.room,before:prepared.before});
  const next=await input.next();if(next.done||JSON.parse(next.value).send!==true)throw Error();
  allowed=true;
  const messageId=await sendOnce(page,prepared);
  say({stage:'sent',messageId});
 }
}catch{say({stage:allowed?'unknown':'blocked'});}
finally{if(context)await context.close();process.exit();}
