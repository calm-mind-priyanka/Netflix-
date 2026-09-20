let maintenance=false;let settings={};
async function req(url,options={}){const response=await fetch(url,{credentials:"same-origin",...options});if(response.status===401){location.href="/admin";return null}const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.error||data.message||"Request failed");return data}
function get(obj,path){return path.split('.').reduce((v,k)=>v==null?undefined:v[k],obj)}
function set(obj,path,value){const keys=path.split('.');let cur=obj;keys.forEach((k,i)=>{if(i===keys.length-1)cur[k]=value;else cur=cur[k]||(cur[k]={})})}
function syncInputs(){document.querySelectorAll('[data-path]').forEach(el=>{let v=get(settings,el.dataset.path);if(el.dataset.path==='files.fsub_channels'&&Array.isArray(v))v=v.join('\n');if(el.dataset.secret){el.value='';return}if(el.type==='checkbox')el.checked=Boolean(v);else el.value=v==null?'':v});const legacy=document.getElementById('legacyPreview');if(legacy)legacy.textContent=JSON.stringify(settings.ultron||{},null,2)}
function collect(){const out=JSON.parse(JSON.stringify(settings));document.querySelectorAll('[data-path]').forEach(el=>{if(el.dataset.secret && !el.value.trim())return;let v;if(el.type==='checkbox')v=el.checked;else if(el.type==='number')v=Number(el.value||0);else v=el.value;if(el.dataset.path==='files.fsub_channels')v=String(v).split(/[\n,]+/).map(x=>x.trim()).filter(Boolean);set(out,el.dataset.path,v)});return out}
async function load(){const [status,conf]=await Promise.all([req('/admin/api/status'),req('/admin/api/settings')]);if(!status||!conf)return;maintenance=Boolean(status.maintenance);settings=conf.settings;document.getElementById('titles').textContent=status.titles;document.getElementById('movies').textContent=status.movies;document.getElementById('series').textContent=status.series;document.getElementById('state').textContent=maintenance?'Website is OFFLINE — maintenance mode is ON.':'Website is ONLINE.';document.getElementById('toggle').textContent=maintenance?'Turn website ON':'Put website in maintenance';syncInputs()}
document.getElementById('toggle').onclick=async()=>{const b=document.getElementById('toggle');b.disabled=true;try{await req('/admin/api/maintenance',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({maintenance:!maintenance})});await load()}catch(e){alert(e.message)}finally{b.disabled=false}}
document.getElementById('refresh').onclick=async()=>{const b=document.getElementById('refresh');b.disabled=true;try{await req('/admin/api/refresh',{method:'POST'});await load()}catch(e){alert(e.message)}finally{b.disabled=false}}
document.getElementById('save').onclick=async()=>{const b=document.getElementById('save');b.disabled=true;try{settings=collect();const data=await req('/admin/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings})});settings=data.settings;syncInputs();document.getElementById('saveState').textContent='All settings saved successfully.';setTimeout(()=>document.getElementById('saveState').textContent='',3000)}catch(e){alert(e.message)}finally{b.disabled=false}}
document.getElementById('reset').onclick=async()=>{if(!confirm('Reset ALL website/Ultron-compatible settings to defaults?'))return;const b=document.getElementById('reset');b.disabled=true;try{const data=await req('/admin/api/settings/reset',{method:'POST'});settings=data.settings;syncInputs();document.getElementById('saveState').textContent='Settings reset.'}catch(e){alert(e.message)}finally{b.disabled=false}}
document.querySelectorAll('.remove-setting').forEach(btn=>btn.onclick=async()=>{const path=btn.dataset.remove;if(!confirm('Remove this setting and restore its default?'))return;try{const data=await req('/admin/api/settings/remove',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});settings=data.settings;syncInputs();document.getElementById('saveState').textContent='Setting removed.'}catch(e){alert(e.message)}})
[1,2,3].forEach(n=>document.getElementById('testShortener'+n)?.addEventListener('click',async()=>{const out=document.getElementById('shortenerTest');out.textContent='Testing shortener '+n+'…';try{const r=await req('/admin/api/shortener/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stage:String(n),url:location.origin+'/api/health'})});out.innerHTML='Shortener '+n+' result: <a href="'+r.shortlink+'" target="_blank" rel="noopener">'+r.shortlink+'</a>'}catch(e){out.textContent='Shortener '+n+' test failed: '+e.message}}));
document.getElementById('logout').onclick=async()=>{try{await req('/admin/logout',{method:'POST'})}finally{location.href='/admin'}}
load().catch(e=>{document.getElementById('state').textContent=e.message})

async function loadPremiumPending(){
  const box=document.getElementById('premiumPending');
  if(!box)return;
  try{
    const d=await req('/admin/api/premium/manual');
    const rows=d.requests||[];
    if(!rows.length){box.textContent='No pending manual payment requests.';return;}
    box.innerHTML=rows.map(r=>`<div class="premiumRequest" style="border:1px solid #333;border-radius:10px;padding:12px;margin:8px 0"><b>${String(r.plan_name||r.plan_id)}</b> • ₹${String(r.amount||'')}<br><small>User: ${String(r.user_id||'')} • ${new Date((r.created_at||0)*1000).toLocaleString()}</small><br><a href="/admin/api/premium/manual/${encodeURIComponent(r.id)}/proof" target="_blank">View payment proof</a><br><button type="button" class="mini premiumApprove" data-id="${String(r.id)}">Approve</button> <button type="button" class="mini premiumReject" data-id="${String(r.id)}">Reject</button></div>`).join('');
    box.querySelectorAll('.premiumApprove,.premiumReject').forEach(btn=>btn.onclick=async()=>{
      btn.disabled=true;
      try{await req('/admin/api/premium/manual/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:btn.dataset.id,action:btn.classList.contains('premiumApprove')?'approve':'reject'})});await loadPremiumPending();}
      catch(e){alert(e.message);btn.disabled=false;}
    });
  }catch(e){box.textContent='Unable to load pending payments: '+e.message;}
}

const premiumGrant=document.getElementById('premiumGrant');

loadPremiumPending();
if(premiumGrant){premiumGrant.onclick=async()=>{const user_id=document.getElementById('premiumUserId').value.trim();const plan_id=document.getElementById('premiumPlanId').value.trim()||'30day';const el=document.getElementById('premiumGrantState');if(!user_id){el.textContent='Enter a user ID.';return;}try{const d=await req('/admin/api/premium/grant',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id,plan_id})});el.textContent=d.ok?`Premium ${d.plan} active until ${new Date(d.expires_at*1000).toLocaleString()}`:(d.error||'Grant failed');}catch(e){el.textContent=e.message;}}}
