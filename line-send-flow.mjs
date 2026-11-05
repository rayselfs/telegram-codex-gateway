// UI-only deterministic flow; no server APIs or retries around send.
// LINE renders native emoji as images; recover their literal alt text without weakening equality.
export function matchingMessages({text,before=[],wait=false}){
 const ids=[...document.querySelectorAll('[data-message-content][data-is-message-text="true"]')].filter(e=>{
  const wrapper=e.closest('[data-direction]');
  if(wrapper?.getAttribute('data-direction')!=='reverse'||wrapper.querySelector('.icon_state'))return false;
  const copy=e.cloneNode(true);
  for(const emoji of copy.querySelectorAll('img.emoji.native_emoji[alt]'))emoji.replaceWith(document.createTextNode(emoji.getAttribute('alt')));
  return copy.textContent===text;
 }).map(e=>e.closest('[data-message-id]')?.getAttribute('data-message-id')).filter(id=>/^\d+$/.test(id||'')&&!before.includes(id));
 return wait ? ids.length>0 : ids;
}
export async function prepare(page,recipient,text){
 const normalize=s=>s.replace(/\s/g,'');
 await page.getByPlaceholder('以姓名搜尋',{exact:true}).fill(recipient,{timeout:5000});
 const escaped=[...normalize(recipient)].map(c=>c.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s*');
 const candidates=page.getByRole('button',{name:new RegExp(escaped)});
 await candidates.first().waitFor({state:'visible',timeout:10000});
 const names=await candidates.allTextContents();
 // A group carries a member count. Similar names and duplicate matches stop here.
 const matches=names.map((name,index)=>({name,index})).filter(x=>/\(\d+\)\s*$/.test(x.name)&&normalize(x.name.replace(/\(\d+\)\s*$/,''))===normalize(recipient));
 if(matches.length!==1)throw Error('Group must match uniquely');
 await candidates.nth(matches[0].index).click({timeout:5000});
 const editor=page.getByRole('textbox',{name:'輸入訊息',exact:true});
 await editor.waitFor({state:'visible',timeout:10000});
 await editor.fill(text,{timeout:5000});
 if(await editor.inputValue()!==text)throw Error('Draft mismatch');
 const before=await page.evaluate(matchingMessages,{text});
 return {recipient,text,room:page.url(),before};
}
export async function sendOnce(page,prepared){
 const editor=page.getByRole('textbox',{name:'輸入訊息',exact:true});
 if(page.url()!==prepared.room||await editor.inputValue()!==prepared.text)throw Error('Target/draft changed');
 await editor.press('Alt+Enter',{timeout:5000});
 await page.waitForFunction(matchingMessages,{...prepared,wait:true},{timeout:15000});
 const found=await page.evaluate(matchingMessages,prepared);
 if(found.length!==1||await editor.inputValue()!=='')throw Error('Result ambiguous');
 return found[0];
}
