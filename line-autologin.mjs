// Fixed shared LINE login adapter; secrets stay in local child-pipe/DOM memory.
import {execFileSync} from 'node:child_process';
import {acquireLineLease} from './line-lease.mjs';
const attempted=new WeakSet();
const home='chrome-extension://ophjlpahpchlmihnnnihgmmeilfjmjjc/index.html';
export default async ({page}) => {
 const context=page.context();
 if(attempted.has(context))return;
 try{await acquireLineLease(context);}catch(error){await context.close();throw error;}
 attempted.add(context);
 return login(page);
};
export async function login(page) {
 await page.goto(home);
 const ready=page.getByRole('button',{name:'Popover more actions',exact:true});
 const password=page.getByPlaceholder('密碼',{exact:true});
 const email=page.getByPlaceholder('電子郵件帳號',{exact:true});
 await ready.or(password).first().waitFor({state:'visible',timeout:20000});
 if(await ready.isVisible())return;
 if(page.url().split('#')[0]!==home)throw new Error('Unexpected LINE login location');
 let secret;
 try {
  // No public credential-read endpoint, arguments, files, tracing or model result.
  const code="import sys;sys.path.insert(0,r'D:\\CodexTelegram\\bridge');from vault import Vault;sys.stdout.write(Vault('system').read('line-shared-login'))";
  const raw=execFileSync('D:/CodexTelegram/runtime/gateway-venv/Scripts/python.exe',
   ['-B','-X','utf8','-c',code],{windowsHide:true,stdio:['ignore','pipe','ignore'],timeout:10000});
  try{secret=JSON.parse(raw.toString('utf8'));}finally{raw.fill(0);}
  if(typeof secret.email!=='string'||typeof secret.password!=='string')throw Error();
  await email.fill(secret.email);await password.fill(secret.password);
  secret.email='';secret.password='';
  await page.getByRole('button',{name:'登入',exact:true}).click();
  await ready.waitFor({state:'visible',timeout:30000});
 } catch {
  throw new Error('LINE automatic login did not complete. Do not retry repeatedly; use the local login window for credentials or phone verification.');
 } finally {
  if(secret){secret.email='';secret.password='';}
  // Never leave populated fields for a subsequent MCP snapshot on failure.
  for(const field of [email,password]){
   try{if(await field.isVisible())await field.fill('');}catch{}
  }
 }
};
