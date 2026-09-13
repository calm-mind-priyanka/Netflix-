let titles=[];const $=s=>document.querySelector(s);
const esc=x=>String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
const poster=t=>t.poster?`style="background-image:url('${esc(t.poster)}')"`:'';
function card(t){return `<article class="card" data-id="${esc(t.id)}"><div class="poster" ${poster(t)}>${t.poster?'':'🎬'}<span class="cardShade"></span></div><strong>${esc(t.title)}</strong><small>${t.type==='series'?'Series':'Movie'}${t.year?' • '+t.year:''}</small></article>`}
function state(el,msg,kind='loading'){el.innerHTML=`<div class="state ${kind}">${esc(msg)}</div>`}
async function load(){
  state($('#latest'),'Loading catalog…'); state($('#movies'),'Loading…'); state($('#series'),'Loading…');
  try{
    const d=await API.get('/api/home'); if(!Array.isArray(d.items))throw new Error(d.error||'Invalid catalog response');
    titles=d.items;
    $('#latest').innerHTML=titles.slice(0,18).map(card).join('')||'<div class="state">No movies indexed yet.</div>';
    $('#movies').innerHTML=titles.filter(x=>x.type==='movie').slice(0,24).map(card).join('')||'<div class="state">No movies found.</div>';
    $('#series').innerHTML=titles.filter(x=>x.type==='series').slice(0,24).map(card).join('')||'<div class="state">No series found.</div>';
    bind();
    if(titles[0]){$('#hero').style.backgroundImage=titles[0].backdrop?`linear-gradient(90deg,rgba(5,5,5,.98),rgba(5,5,5,.55),rgba(5,5,5,.15)),url('${esc(titles[0].backdrop)}')`:'linear-gradient(90deg,#080808,#171717)';$('#hero').innerHTML=`<div class="heroText"><span class="eyebrow">STREAMBOX ORIGINAL CATALOG</span><h1>${esc(titles[0].title)}</h1><p>${esc(titles[0].description||'Discover and stream your next movie or series.')}</p><button class="primary" onclick="showDetails('${esc(titles[0].id)}')">▶ Watch now</button></div>`}
    renderContinue();
  }catch(e){
    const msg=e.message||'Unable to load the catalog.';
    ['latest','movies','series'].forEach(id=>state($('#'+id),`Catalog error: ${msg}`,'error'));
    $('#hero').innerHTML='<div class="heroText"><span class="eyebrow">STREAMBOX</span><h1>Catalog unavailable</h1><p>The website is running, but the movie database could not be read. Check the backend/API status.</p><button class="primary" onclick="load()">Retry</button></div>';
  }
}
function bind(){document.querySelectorAll('.card').forEach(x=>x.onclick=()=>showDetails(x.dataset.id))}
function renderContinue(){const p=JSON.parse(localStorage.getItem('stream_progress')||'{}');const ids=Object.keys(p).sort((a,b)=>p[b].updated-p[a].updated).map(id=>titles.find(t=>t.id===id)).filter(Boolean);if(!ids.length)return;$('#continue').classList.remove('hidden');$('#continueRow').innerHTML=ids.slice(0,12).map(card).join('');bind()}
async function showDetails(id){try{const t=titles.find(x=>x.id===id)||await API.get('/api/title/'+id);$('#details').classList.remove('hidden');let html=`<div class="detail" ${poster(t)}><div><span class="eyebrow">${t.type==='series'?'SERIES':'MOVIE'}</span><h1>${esc(t.title)}</h1><p>${esc(t.description||'')}${t.rating?`<br>★ ${Number(t.rating).toFixed(1)}`:''}</p></div></div>`;if(t.type==='movie')html+=`<div class="episode"><span>Available versions</span><button class="primary" onclick='Player.open(${JSON.stringify(t.id)},${JSON.stringify(t.title)},${JSON.stringify(t.variants)},null)'>▶ Play</button></div>`;else html+=t.seasons.map((s,si)=>`<section><h2>Season ${s.season}</h2>${s.episodes.map((e,ei)=>{const next=s.episodes[ei+1]?{title:t.title,season:s.season,episode:s.episodes[ei+1].episode,variants:s.episodes[ei+1].variants}:t.seasons[si+1]?.episodes?.[0]?{title:t.title,season:t.seasons[si+1].season,episode:t.seasons[si+1].episodes[0].episode,variants:t.seasons[si+1].episodes[0].variants}:null;return `<div class="episode"><span>Episode ${e.episode}</span><button class="primary" onclick='Player.open(${JSON.stringify(t.id)},${JSON.stringify(t.title+' • S'+String(s.season).padStart(2,'0')+' E'+String(e.episode).padStart(2,'0'))},${JSON.stringify(e.variants)},${JSON.stringify(next)})'>▶</button></div>`}).join('')}</section>`).join('');$('#detailBody').innerHTML=html}catch(e){alert(e.message||'Unable to open title')}}
async function doSearch(){const q=$('#query').value.trim();if(!q){$('#results').innerHTML='<div class="state">Type a movie or series name.</div>';return}state($('#results'),'Searching…');try{const d=await API.get('/api/search?q='+encodeURIComponent(q));if(!d.items?.length){state($('#results'),`No results found for “${q}”.`,'empty');return}$('#results').innerHTML=d.items.map(card).join('');bind()}catch(e){state($('#results'),`Search failed: ${e.message||'backend error'}`,'error')}}
$('#searchBtn').onclick=$('#bottomSearch').onclick=()=>{$('#search').classList.remove('hidden');$('#query').focus();doSearch()};$('#closeSearch').onclick=()=>$('#search').classList.add('hidden');$('#backDetails').onclick=()=>$('#details').classList.add('hidden');$('#settings').onclick=()=>$('#menu').classList.toggle('hidden');$('#download').onclick=()=>Player.download();$('#closePlayer').onclick=()=>Player.close();$('#query').oninput=()=>{clearTimeout(window.s);window.s=setTimeout(doSearch,250)};$('#query').onkeydown=e=>{if(e.key==='Enter')doSearch()};load();
