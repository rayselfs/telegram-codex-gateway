import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {EventEmitter,once} from 'node:events';
import {chromium} from 'file:///D:/CodexTelegram/runtime/browser/node_modules/playwright/index.mjs';
import {prepare,sendOnce,matchingMessages} from '../line-send-flow.mjs';
import {acquireLineLease} from '../line-lease.mjs';
const browser=await chromium.launch({executablePath:'D:/CodexTelegram/runtime/line/chromium/chrome.exe',headless:true});
try{
 const page=await browser.newPage();await page.route('**/*',r=>r.abort());
 async function fixture({duplicate=false,confirm=true}={}){
  await page.setContent(`<input placeholder="以姓名搜尋"><button>假  群組 (3)</button>${duplicate?'<button>假 群組 (4)</button>':''}<textarea aria-label="輸入訊息"></textarea><div data-direction="reverse" data-message-id="111"><span data-message-content data-is-message-text="true">假訊息</span></div>`);
  await page.evaluate(confirm=>{
   window.sends=0;document.querySelector('textarea').addEventListener('keydown',e=>{
    if(e.key!=='Enter'||!e.altKey)return;e.preventDefault();window.sends++;
    const wrap=document.createElement('div');wrap.setAttribute('data-direction','reverse');wrap.setAttribute('data-message-id','local-fixture');
    const text=document.createElement('span');text.setAttribute('data-message-content','');text.setAttribute('data-is-message-text','true');text.textContent=e.target.value;wrap.append(text);document.body.append(wrap);e.target.value='';
    if(confirm)setTimeout(()=>wrap.setAttribute('data-message-id','222'),200);
   });
  },confirm);
 }
 await fixture();const prepared=await prepare(page,'假 群組','假訊息');assert.deepEqual(prepared.before,['111']);
 const start=Date.now();assert.equal(await sendOnce(page,prepared),'222');assert.ok(Date.now()-start>=180);assert.equal(await page.evaluate(()=>window.sends),1);
 await fixture();
 const incoming=await prepare(page,'假 群組','假訊息');
 await page.evaluate(()=>{
  const textarea=document.querySelector('textarea');const clone=textarea.cloneNode(true);textarea.replaceWith(clone);
  clone.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.altKey){e.preventDefault();window.sends++;clone.value='';
   document.body.insertAdjacentHTML('beforeend','<div data-direction="" data-message-id="999"><span data-message-content data-is-message-text="true">假訊息</span></div>');}});
 });
 const incomingWait=page.waitForFunction.bind(page);page.waitForFunction=(f,a)=>incomingWait(f,a,{timeout:300});
 await assert.rejects(sendOnce(page,incoming));assert.equal(await page.evaluate(()=>window.sends),1);
 page.waitForFunction=incomingWait;
 await fixture({duplicate:true});await assert.rejects(prepare(page,'假 群組','假訊息'),/uniquely/);assert.equal(await page.evaluate(()=>window.sends),0);
 await fixture({confirm:false});const unknown=await prepare(page,'假 群組','假訊息');
 const originalWait=page.waitForFunction.bind(page);page.waitForFunction=(fn,arg,options)=>originalWait(fn,arg,{...options,timeout:300});
 await assert.rejects(sendOnce(page,unknown));assert.equal(await page.evaluate(()=>window.sends),1);
 page.waitForFunction=originalWait;
 await fixture();const changed=await prepare(page,'假 群組','假訊息');await page.getByRole('textbox',{name:'輸入訊息',exact:true}).fill('changed');await assert.rejects(sendOnce(page,changed),/changed/);assert.equal(await page.evaluate(()=>window.sends),0);
 // Production-shaped emoji DOM, including existing same-text messages and a wrong emoji.
 const emojiText='📣 假提醒\n第一場\n第二場\n謝謝。🙏';
 await fixture();
 await page.evaluate(text=>{
  const render=e=>{const value=e.textContent;e.textContent='';for(const ch of value){if(['📣','🙏','🚫'].includes(ch)){const img=document.createElement('img');img.className='emoji native_emoji';img.alt=ch;e.append(img);}else e.append(document.createTextNode(ch));}};
  const old=document.querySelector('[data-message-content]');old.textContent=text;render(old);
  new MutationObserver(()=>{const el=document.querySelector('[data-message-id="local-fixture"] [data-message-content]');if(el&&!el.querySelector('img'))render(el);}).observe(document.body,{childList:true,subtree:true});
 },emojiText);
 const emojiPrepared=await prepare(page,'假 群組',emojiText);assert.deepEqual(emojiPrepared.before,['111']);
 assert.equal(await sendOnce(page,emojiPrepared),'222');assert.equal(await page.evaluate(()=>window.sends),1);
 assert.deepEqual(await page.evaluate(matchingMessages,{text:emojiText.replace('🙏','🚫')}),[]);
 assert.deepEqual(await page.evaluate(matchingMessages,{text:emojiText,before:['111','222']}),[]);
 console.log('EMOJI_RECEIPT_PASS: native image alt, exact content, existing IDs, changed emoji');
 console.log('OFFLINE_UI_PASS: exact unique group, draft, one send, old/local IDs excluded, ambiguity stops');
}finally{await browser.close();}
const folder='D:/CodexTelegram/test-state/line-lease-'+Date.now();await fs.mkdir(folder,{recursive:true});
function context(){const c=new EventEmitter();c.closed=false;c.close=async()=>{if(!c.closed){c.closed=true;c.emit('close');}};return c;}
const a=context();const first=await acquireLineLease(a,folder+'/shared.lock');
await assert.rejects(acquireLineLease(context(),folder+'/shared.lock'));
const ended=once(first,'exit');await a.close();await ended;
const b=context();const second=await acquireLineLease(b,folder+'/shared.lock');const killed=once(second,'exit');second.kill();await killed;await new Promise(r=>setTimeout(r,50));assert.ok(b.closed);
console.log('SHARED_OS_LEASE_PASS: concurrent rejection, release, lease-loss closes browser context');
