const $ = id => document.getElementById(id);

async function api(url, opt = {}) {
  const r = await fetch(url, { credentials: 'same-origin', ...opt });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || d.message || `Request failed (${r.status})`); e.status = r.status; throw e; }
  return d;
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function val(id){ return $(id)?.value || ''; }
function notice(message, type='info'){ const box=$('adminNotice'); if(!box)return; box.textContent=message; box.className=`adminNotice ${type} show`; clearTimeout(notice.t); notice.t=setTimeout(()=>box.classList.remove('show'),3500); }
function setBusy(id,busy,label='Saving…'){const b=$(id);if(!b)return;if(busy){b.dataset.originalText=b.dataset.originalText||b.textContent;b.disabled=true;b.textContent=label;}else{b.disabled=false;b.textContent=b.dataset.originalText||b.textContent;}}

async function saveSettings(buttonId, payload, message){
  setBusy(buttonId,true);
  try { await api('/admin/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); notice(message||'Saved.','success'); await loadSettings(); }
  catch(e){ notice(`Save failed: ${e.message}`,'error'); }
  finally{ setBusy(buttonId,false); }
}

function renderPlans(plans=[]){
  $('plansList').innerHTML = plans.map(p=>`<div class="request"><b>${esc(p.name)}</b> — ₹${esc(p.price_inr)} <span class="muted">(${esc(p.id)}, ${esc(p.days)} days)</span></div>`).join('') || '<p class="muted">No premium plans configured.</p>';
  $('premiumPlan').innerHTML = plans.map(p=>`<option value="${esc(p.id)}">${esc(p.name)} — ₹${esc(p.price_inr)}</option>`).join('');
}

async function loadSettings(){
  paymentUserFilter='';
  try{
    const s=await api('/admin/api/settings');
    const v=s.settings?.verification||{}, sh=v.shorteners||{}, se=s.settings?.search||{}, f=s.settings?.files||{}, m=s.settings?.metadata||{}, pay=s.settings?.payments||{};
    $('verifyEnabled').checked=!!v.enabled; $('shortlinkEnabled').checked=v.shortlink_mode!=='disabled';
    ['1','2','3'].forEach(n=>{$(`s${n}on`).checked=!!sh[n]?.enabled;$(`s${n}name`).value=sh[n]?.name||'';$(`s${n}api`).value='';});
    $('t1').value=v.tutorial_1||'';$('t2').value=v.tutorial_2||'';$('t3').value=v.tutorial_3||'';$('v2').value=v.verification_time_2||0;$('v3').value=v.verification_time_3||0;$('validity').value=v.validity_hours||24;
    $('maintenance').checked=!!s.settings?.site?.maintenance;
    $('maxResults').value=se.max_results||20;$('resultsPerPage').value=se.results_per_page||20;$('candidateLimit').value=se.candidate_limit||120;$('spellCheck').checked=se.spell_check!==false;$('fuzzy').checked=se.fuzzy_fallback!==false;$('imdb').checked=!!se.imdb_poster;
    $('fileSecure').checked=!!f.file_secure;$('autoDelete').checked=!!f.auto_delete;$('autoDeleteSeconds').value=f.auto_delete_seconds||60;$('tmdbEnabled').checked=!!m.tmdb_enabled;$('posterFallback').checked=m.poster_fallback!==false;
    $('activationMode').value=pay.activation_mode||'environment';$('premiumBypass').checked=pay.premium_bypass_verification!==false;$('premiumShortenerBypass').checked=pay.premium_bypass_shortener!==false;$('manualEnabled').checked=pay.manual_enabled!==false;$('upiId').value=pay.upi_id||'';$('paymentInstructions').value=pay.manual_instructions||'';
    if(pay.manual_qr){$('qrPreview').src=pay.manual_qr;$('qrPreview').classList.remove('hidden');$('removeQr').classList.remove('hidden');}else{$('qrPreview').removeAttribute('src');$('qrPreview').classList.add('hidden');$('removeQr').classList.add('hidden');}
    renderPlans(s.plans||[]);
    $('state').textContent=JSON.stringify(s.settings,null,2);
    $('panel').classList.remove('hidden');
    await Promise.all([loadUsers(),loadPremiumUsers(),loadRequests(),loadPayments()]); setHistoryVisible(false); $('historySummary').textContent='History is hidden until requested.';
  }catch(e){if(e.status===401){location.replace('/admin');return;}notice(`Could not load settings: ${e.message}`,'error');}
}

$('logout').onclick=async()=>{try{await api('/admin/logout',{method:'POST'});location.replace('/admin');}catch(e){notice(e.message,'error');}};
$('refresh').onclick=()=>loadSettings();
$('saveMaintenance').onclick=()=>saveSettings('saveMaintenance',{site:{maintenance:$('maintenance').checked}},'Website settings saved.');
$('saveSearch').onclick=()=>saveSettings('saveSearch',{search:{max_results:Number(val('maxResults')||20),results_per_page:Number(val('resultsPerPage')||20),candidate_limit:Number(val('candidateLimit')||120),spell_check:$('spellCheck').checked,fuzzy_fallback:$('fuzzy').checked,imdb_poster:$('imdb').checked}},'Search settings saved.');
$('saveFiles').onclick=()=>saveSettings('saveFiles',{files:{file_secure:$('fileSecure').checked,auto_delete:$('autoDelete').checked,auto_delete_seconds:Number(val('autoDeleteSeconds')||60)},metadata:{tmdb_enabled:$('tmdbEnabled').checked,poster_fallback:$('posterFallback').checked}},'File and metadata settings saved.');
$('saveVerify').onclick=()=>saveSettings('saveVerify',{verification:{enabled:$('verifyEnabled').checked,shortlink_mode:$('shortlinkEnabled').checked?'enabled':'disabled',shorteners:{'1':{enabled:$('s1on').checked,name:val('s1name')},'2':{enabled:$('s2on').checked,name:val('s2name')},'3':{enabled:$('s3on').checked,name:val('s3name')}},tutorial_1:val('t1'),tutorial_2:val('t2'),tutorial_3:val('t3'),verification_time_2:Number(val('v2')||0),verification_time_3:Number(val('v3')||0),validity_hours:Number(val('validity')||24)}},'Verification settings saved.');

async function qrDataUrl(){
  const f=$('qrUpload').files?.[0];
  if(!f)return null;
  if(!/^image\/(png|jpeg|webp)$/.test(f.type)||f.size>1024*1024) throw new Error('QR image must be PNG/JPG/WEBP and 1MB or smaller.');
  return await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(r.result);r.onerror=reject;r.readAsDataURL(f);});
}
$('savePayments').onclick=async()=>{
  setBusy('savePayments',true);
  try{
    const qr=await qrDataUrl();
    const payments={activation_mode:val('activationMode'),premium_bypass_verification:$('premiumBypass').checked,premium_bypass_shortener:$('premiumShortenerBypass').checked,manual_enabled:$('manualEnabled').checked,upi_id:val('upiId'),manual_instructions:val('paymentInstructions')};
    if(qr) payments.manual_qr=qr;
    await api('/admin/api/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({payments})});
    $('qrUpload').value=''; notice('Payment settings saved to MongoDB.','success'); await loadSettings();
  }catch(e){notice(`Payment save failed: ${e.message}`,'error');}finally{setBusy('savePayments',false);}
};

$('removeQr').onclick=async()=>{
  if(!confirm('Remove the configured payment QR?')) return;
  setBusy('removeQr',true,'Removing…');
  try{
    await api('/admin/api/payment/qr/remove',{method:'POST'});
    $('qrUpload').value='';
    notice('Payment QR removed.','success');
    await loadSettings();
  }catch(e){notice(`QR removal failed: ${e.message}`,'error');}
  finally{setBusy('removeQr',false);}
};

async function loadUsers(){
  try{
    const q=encodeURIComponent(val('userSearch').trim());
    const d=await api(`/admin/api/users${q?`?q=${q}`:''}`);
    $('usersList').innerHTML=d.users?.length?d.users.map(u=>{const p=u.premium||{};return `<div class="request"><b>${esc(u.nickname)}</b> — <code>${esc(u.user_id)}</code><br><small>Created: ${new Date((u.created_at||0)*1000).toLocaleString()} • Premium: ${p.premium?'ACTIVE':'INACTIVE'}${p.expires_at?` • Expires ${new Date(p.expires_at*1000).toLocaleString()}`:''}</small><br><button data-user="${esc(u.user_id)}" class="viewUser">View</button></div>`}).join(''):'<p class="muted">No website users found.</p>';
    document.querySelectorAll('.viewUser').forEach(b=>b.onclick=()=>viewUser(b.dataset.user));
  }catch(e){notice(`Users: ${e.message}`,'error');}
}
$('searchUsers').onclick=loadUsers;$('clearUserSearch').onclick=()=>{$('userSearch').value='';loadUsers();};

async function viewUser(uid){
  try{
    const d=await api(`/admin/api/users/${encodeURIComponent(uid)}`);
    const p=d.premium||{};
    $('premiumUserId').value=uid; paymentUserFilter=uid;
    $('selectedUser').innerHTML=`<div class="request"><b>${esc(d.user.nickname)}</b> — <code>${esc(uid)}</code><br>Premium: <b>${p.premium?'ACTIVE':'INACTIVE'}</b>${p.expires_at?` • Expires ${new Date(p.expires_at*1000).toLocaleString()}`:''}<br>Payments: ${d.payments?.length||0} • Manual requests: ${d.manual_requests?.length||0} • History events: ${d.history?.length||0}</div>`;
    await loadPayments(uid);
  }catch(e){notice(e.message,'error');}
}

async function premiumAction(endpoint, actionLabel){
  const uid=val('premiumUserId').trim(), plan=val('premiumPlan');
  if(!/^\d{8}$/.test(uid)){notice('Enter an existing 8-digit User ID.','error');return;}
  try{const d=await api(endpoint,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid,plan_id:plan})});notice(`${actionLabel} completed. Expiry: ${new Date(d.expires_at*1000).toLocaleString()}`,'success');await viewUser(uid);await loadUsers();}catch(e){notice(`${actionLabel} failed: ${e.message}`,'error');}
}
$('grantPremium').onclick=()=>premiumAction('/admin/api/premium/grant','Premium added');
$('extendPremium').onclick=()=>premiumAction('/admin/api/premium/extend','Premium extended');
$('revokePremium').onclick=async()=>{const uid=val('premiumUserId').trim();if(!/^\d{8}$/.test(uid)){notice('Enter an existing 8-digit User ID.','error');return;}if(!confirm('Remove Premium from this user?'))return;try{await api('/admin/api/premium/revoke',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})});notice('Premium removed.','success');await viewUser(uid);await loadUsers();}catch(e){notice(e.message,'error');}};


async function loadPremiumUsers(){
  try{
    const d=await api('/admin/api/premium/users');
    $('premiumUsersList').innerHTML=d.users?.length?d.users.map(u=>`<div class="request"><b>${esc(u.nickname)}</b> — <code>${esc(u.user_id)}</code><br>${esc(u.plan_name||u.plan_id)} • ₹${esc(u.amount)} • ${esc(u.source||'')}<br><small>Started ${new Date((u.started_at||0)*1000).toLocaleString()} • Expires ${new Date((u.expires_at||0)*1000).toLocaleString()}</small><br><button data-premium-user="${esc(u.user_id)}">Manage</button></div>`).join(''):'<p class="muted">No active Premium users.</p>';
    document.querySelectorAll('[data-premium-user]').forEach(b=>b.onclick=()=>viewUser(b.dataset.premiumUser));
  }catch(e){notice(`Premium users: ${e.message}`,'error');}
}
$('loadPremiumUsers').onclick=loadPremiumUsers;

async function loadRequests(){
  try{
    const d=await api('/admin/api/premium/manual'); const box=$('manualRequests'); box.innerHTML='';
    if(!d.requests?.length){box.innerHTML='<p class="muted">No manual payment requests.</p>';return;}
    d.requests.forEach(r=>{const el=document.createElement('div');el.className='request';el.innerHTML=`<b>${esc(r.nickname||'Unknown')}</b> — <code>${esc(r.user_id)}</code><br>${esc(r.plan_name)} — ₹${esc(r.amount)} • ${esc(String(r.status||'pending').toUpperCase())}<br>UTR: <code>${esc(r.utr||'—')}</code><br><small>${new Date((r.created_at||0)*1000).toLocaleString()}</small><br><a href="/admin/api/premium/manual/${encodeURIComponent(r.id)}/proof" target="_blank" rel="noopener">View screenshot</a> ${r.status==='pending'?'<button data-rid="'+esc(r.id)+'" data-act="approve">Approve</button><button data-rid="'+esc(r.id)+'" data-act="reject" class="danger">Reject</button>':''}`;box.appendChild(el);});
    box.querySelectorAll('[data-act]').forEach(b=>b.onclick=async()=>{try{await api('/admin/api/premium/manual/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:b.dataset.rid,action:b.dataset.act})});notice(`Request ${b.dataset.act}d.`,'success');await loadRequests();paymentUserFilter='';await loadPayments('',1);}catch(e){notice(e.message,'error');}});
  }catch(e){notice(`Requests: ${e.message}`,'error');}
}

let paymentPage=1, historyPage=1, paymentUserFilter='';
const pageSize=50;

async function loadPayments(uidOverride=null, page=1){
  try{
    const uid=uidOverride!==null?uidOverride:(paymentUserFilter||val('paymentUserId').trim());
    paymentUserFilter=uid;
    const status=val('paymentStatus');
    const qs=new URLSearchParams({page:String(page),limit:String(pageSize)});
    if(uid)qs.set('user_id',uid);
    if(status)qs.set('status',status);
    const d=await api('/admin/api/payments?'+qs.toString());
    paymentPage=d.page||1;
    $('paymentsList').innerHTML=d.payments?.length?d.payments.map(p=>`<div class="request"><b>${esc(p.nickname||'Unknown')}</b> — <code>${esc(p.user_id||'')}</code><br>${esc(p.plan_name||p.plan_id||'')} — ₹${esc(p.amount??'')} • ${esc(p.method||'')} • <b>${esc(p.status||'')}</b><br>${p.utr?`UTR: <code>${esc(p.utr)}</code><br>`:''}<small>${new Date((p.created_at||0)*1000).toLocaleString()}</small></div>`).join(''):'<p class="muted">No payments found.</p>';
    const pager=$('paymentPager');
    pager.innerHTML=d.pages>1?`<button id="paymentPrev" ${paymentPage<=1?'disabled':''}>Previous</button><span>Page ${paymentPage} / ${d.pages} • ${d.total} total</span><button id="paymentNext" ${paymentPage>=d.pages?'disabled':''}>Next</button>`:(d.total?`<span class="muted">${d.total} payment${d.total===1?'':'s'}</span>`:'');
    $('paymentPrev')?.addEventListener('click',()=>loadPayments(paymentUserFilter,paymentPage-1));
    $('paymentNext')?.addEventListener('click',()=>loadPayments(paymentUserFilter,paymentPage+1));
  }catch(e){notice(`Payments: ${e.message}`,'error');}
}
$('loadPayments').onclick=()=>{paymentUserFilter=val('paymentUserId').trim();loadPayments(paymentUserFilter,1);};
$('paymentStatus').onchange=()=>loadPayments(paymentUserFilter,1);

function setHistoryVisible(show){
  $('historyList').classList.toggle('hidden',!show);
  $('historyPager').classList.toggle('hidden',!show);
  $('hideHistory').classList.toggle('hidden',!show);
  $('clearHistory').classList.toggle('hidden',!show);
  $('viewHistory').classList.toggle('hidden',show);
}

async function loadHistory(uidOverride=null, page=1){
  try{
    const uid=uidOverride!==null?uidOverride:val('historyUserId').trim();
    const qs=new URLSearchParams({page:String(page),limit:String(pageSize)});
    if(uid)qs.set('user_id',uid);
    const d=await api('/admin/api/history?'+qs.toString());
    historyPage=d.page||1;
    setHistoryVisible(true);
    $('historySummary').textContent=d.total?`${d.total} history event${d.total===1?'':'s'}`:'No history available.';
    $('historyList').innerHTML=d.history?.length?d.history.map(h=>`<div class="request"><b>${esc(h.event_type||'Event')}</b> — <code>${esc(h.user_id||'')}</code><br><small>${new Date((h.created_at||0)*1000).toLocaleString()} • actor: ${esc(h.actor||'system')}</small><pre>${esc(JSON.stringify(h.metadata||{},null,2))}</pre><button class="danger" data-history-id="${esc(h.event_id||'')}">Delete</button></div>`).join(''):'<p class="muted">No history available.</p>';
    $('historyList').querySelectorAll('[data-history-id]').forEach(b=>b.onclick=async()=>{
      if(!b.dataset.historyId || !confirm('Delete this history event?'))return;
      try{await api('/admin/api/history/'+encodeURIComponent(b.dataset.historyId),{method:'DELETE'});notice('History event deleted.','success');await loadHistory(uid,historyPage);}
      catch(e){notice(`Delete failed: ${e.message}`,'error');}
    });
    const pager=$('historyPager');
    $('historyPage').textContent=d.pages>1?`Page ${historyPage} / ${d.pages} • ${d.total} total`:`${d.total||0} total`;
    $('historyPrev').disabled=historyPage<=1;$('historyNext').disabled=!d.pages||historyPage>=d.pages;
  }catch(e){notice(`History: ${e.message}`,'error');}
}
$('viewHistory').onclick=()=>loadHistory(null,1);
$('hideHistory').onclick=()=>setHistoryVisible(false);
$('historyPrev').onclick=()=>loadHistory(null,historyPage-1);
$('historyNext').onclick=()=>loadHistory(null,historyPage+1);
$('clearHistory').onclick=async()=>{
  const uid=val('historyUserId').trim();
  if(!confirm(uid?`Clear all history for User ${uid}?`:'Clear ALL user history?'))return;
  try{
    const d=await api('/admin/api/history/clear',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_id:uid})});
    notice(`Cleared ${d.deleted||0} history event(s).`,'success');await loadHistory(uid,1);
  }catch(e){notice(`Clear history failed: ${e.message}`,'error');}
};

(async()=>{try{await loadSettings();}catch(e){notice(e.message,'error');}})();
