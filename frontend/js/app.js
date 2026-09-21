const $=id=>document.getElementById(id);

let currentTitle=null,
    currentFile=null,
    currentRetry=null,
    homeData=null;

const toast=msg=>{
  const t=$('toast');

  if(!t)return;

  t.textContent=msg;
  t.classList.add('show');

  clearTimeout(toast.t);

  toast.t=setTimeout(
    ()=>t.classList.remove('show'),
    2800
  );
};

const esc=s=>
  String(s??'').replace(
    /[&<>"']/g,
    m=>({
      '&':'&amp;',
      '<':'&lt;',
      '>':'&gt;',
      '"':'&quot;',
      "'":'&#39;'
    }[m])
  );

const show=id=>
  $(id)?.classList.remove('hidden');

const hide=id=>
  $(id)?.classList.add('hidden');


function titleMeta(x){
  return [
    x.type==='series'
      ?'Web Series'
      :'Movie',
    x.year
  ].filter(Boolean).join(' • ');
}


function chip(item){
  const b=document.createElement('button');

  b.className='nameChip';

  b.type='button';

  b.innerHTML=`
    <strong>${esc(item.title)}</strong>
    <small>
      ${esc(titleMeta(item))}
      ${item.count
        ?` • ${item.count} files`
        :''}
    </small>
  `;

  b.onclick=()=>openTitle(item);

  return b;
}


function listItem(item){
  const b=document.createElement('button');

  b.className='nameItem';

  b.type='button';

  b.innerHTML=`
    <strong>${esc(item.title)}</strong>
    <small>
      ${esc(titleMeta(item))}
      ${item.count
        ?` • ${item.count} files`
        :''}
    </small>
  `;

  b.onclick=()=>openTitle(item);

  return b;
}


function renderHome(data){

  homeData=data;

  const items=data.items||[];

  const trend=items.slice(0,12);
  const latest=items.slice(0,18);

  const movies=
    items
      .filter(x=>x.type==='movie')
      .slice(0,18);

  const series=
    items
      .filter(x=>x.type==='series')
      .slice(0,18);

  for(
    const [id,arr]
    of [
      ['trending',trend],
      ['latest',latest]
    ]
  ){
    $(id).innerHTML='';

    arr.forEach(
      x=>$(id).append(chip(x))
    );
  }

  for(
    const [id,arr]
    of [
      ['movies',movies],
      ['series',series]
    ]
  ){
    $(id).innerHTML='';

    arr.forEach(
      x=>$(id).append(listItem(x))
    );
  }

  const cats=new Set();

  items.forEach(x=>{
    if(x.type){
      cats.add(
        x.type==='series'
          ?'Web Series'
          :'Movies'
      );
    }

    (x.languages||
      x.audio_languages||
      [])
      .slice(0,4)
      .forEach(v=>cats.add(v));
  });

  $('categories').innerHTML=
    [...cats]
      .map(c=>
        `<button
          class="category"
          type="button"
          data-q="${esc(c)}"
        >
          ${esc(c)}
        </button>`
      )
      .join('');

  $('categories')
    .querySelectorAll('button')
    .forEach(
      b=>b.onclick=()=>search(b.dataset.q)
    );
}


async function loadHome(){
  try{
    renderHome(
      await API.get('/api/home')
    );
  }catch(e){
    toast(e.message);
  }
}


function filterButton(
  label,
  kind,
  value,
  active=false
){
  return `
    <button
      class="filterBtn ${active?'active':''}"
      data-filter="${esc(kind)}"
      data-value="${esc(value??'')}"
      type="button"
    >
      ${esc(label)}
    </button>
  `;
}


function renderSearchCard(item){

  const row=
    document.createElement('article');

  row.className='afCard';

  row.innerHTML=`
    <div class="afHead">
      <div>
        <div class="afTitle">
          ${esc(item.title)}
        </div>

        <div class="afMeta">
          ${esc(titleMeta(item))}
          ${item.count
            ?` • ${item.count} files`
            :''}
        </div>
      </div>

      <button
        class="afOpen"
        type="button"
      >
        Open
      </button>
    </div>

    <div class="afHint">
      Choose Language, Quality, Season
      or Episode — exactly like an
      AutoFilter result.
    </div>

    <div class="afFilters">
      <span class="muted">
        Loading filters…
      </span>
    </div>

    <div class="afMatches">
      <span class="muted">
        Loading files…
      </span>
    </div>
  `;

  row
    .querySelector('.afOpen')
    .onclick=()=>openTitle(item);

  row._page=0;

  row._pageSize=
    window._searchPageSize||20;

  loadInlineFilters(item,row);

  return row;
}


async function loadInlineFilters(item,row){

  try{

    const opt=
      await API.get(
        `/api/filter-options?id=${
          encodeURIComponent(item.id)
        }&title=${
          encodeURIComponent(item.title)
        }`
      );

    const state={
      language:null,
      quality:null,
      season:null,
      episode:null
    };

    row._filterState=state;

    const filters=
      row.querySelector('.afFilters');

    const groups=
      buildFilterGroups(opt,state);

    renderInlineGroups(
      filters,
      groups,
      state,
      row,
      item,
      opt
    );

    await applyInlineFilter(
      item,
      row,
      opt
    );

  }catch(e){

    row
      .querySelector('.afMatches')
      .innerHTML=
        `<span class="muted">
          ${esc(e.message)}
        </span>`;
  }
}


function buildFilterGroups(opt,state){

  const groups=[];

  if((opt.languages||[]).length){
    groups.push([
      'Language',
      'language',
      opt.languages
    ]);
  }

  if((opt.qualities||[]).length){
    groups.push([
      'Quality',
      'quality',
      opt.qualities
    ]);
  }

  if((opt.seasons||[]).length){
    groups.push([
      'Season',
      'season',
      opt.seasons.map(
        x=>`S${String(x).padStart(2,'0')}`
      )
    ]);
  }

  const seasonKey=
    state.season
      ?String(Number(state.season))
      :null;

  const episodeValues=
    seasonKey
      ?(
        (opt.episodes||{})[seasonKey]||[]
      )
      :[
        ...new Set(
          Object
            .values(opt.episodes||{})
            .flat()
        )
      ].sort(
        (a,b)=>Number(a)-Number(b)
      );

  if(episodeValues.length){
    groups.push([
      'Episode',
      'episode',
      episodeValues.map(
        x=>`E${String(x).padStart(2,'0')}`
      )
    ]);
  }

  return groups;
}


function renderInlineGroups(
  container,
  groups,
  state,
  row,
  item,
  opt
){

  container.innerHTML=
    groups
      .map(
        ([label,key,vals])=>`
          <div class="filterGroup">

            <span class="filterLabel">
              ${esc(label)}
            </span>

            <div class="filterButtons">

              ${filterButton(
                'All',
                key,
                '',
                !state[key]
              )}

              ${vals.map(v=>{

                const raw=
                  key==='season' ||
                  key==='episode'
                    ?String(v)
                      .replace(
                        /^[SE]0*/i,
                        ''
                      )
                    :String(v);

                return filterButton(
                  v,
                  key,
                  raw,
                  state[key]===raw
                );

              }).join('')}

            </div>

          </div>
        `
      )
      .join('');

  container
    .querySelectorAll('[data-filter]')
    .forEach(btn=>{

      btn.onclick=async()=>{

        const k=
          btn.dataset.filter;

        const v=
          btn.dataset.value;

        state[k]=v||null;

        if(k==='season'){
          state.episode=null;
        }

        if(
          k==='episode' &&
          !state.season
        ){
          state.episode=v||null;
        }

        row._page=0;

        renderInlineGroups(
          container,
          buildFilterGroups(opt,state),
          state,
          row,
          item,
          opt
        );

        if(row===$('detailBody')){
          await applyDetailFilter(
            item,
            row
          );
        }else{
          await applyInlineFilter(
            item,
            row,
            opt
          );
        }
      };
    });
}


async function applyInlineFilter(
  item,
  row,
  opt
){

  const s=
    row._filterState||{};

  const q=
    new URLSearchParams({
      id:item.id,
      title:item.title
    });

  if(s.language)
    q.set('language',s.language);

  if(s.quality)
    q.set('quality',s.quality);

  if(s.season)
    q.set('season',s.season);

  if(s.episode)
    q.set('episode',s.episode);

  const box=
    row.querySelector('.afMatches');

  box.innerHTML=
    '<span class="muted">Finding matching files…</span>';

  try{

    const d=
      await API.get(
        '/api/filter?'+q.toString()
      );

    renderMatches(
      box,
      d,
      row,
      item,
      opt
    );

  }catch(e){

    box.innerHTML=
      `<span class="muted">
        ${esc(e.message)}
      </span>`;
  }
}


function renderMatches(
  box,
  d,
  row,
  item,
  opt
){

  const matches=d.matches||[];

  const max=
    Number(row._pageSize||20);

  const page=
    Math.max(
      0,
      Number(row._page||0)
    );

  const total=matches.length;

  const visible=
    matches.slice(
      page*max,
      page*max+max
    );

  box.innerHTML=`
    <div class="matchHead">

      <strong>
        ${total}
        matching file${total===1?'':'s'}
      </strong>

      <span>
        ${
          total>max
            ?`Page ${
              page+1
            } of ${
              Math.ceil(total/max)
            }`
            :''
        }
      </span>

    </div>
  `;

  if(!visible.length){

    box.innerHTML+=`
      <p class="muted">
        No file matches these filters.
        Try another option.
      </p>
    `;

    return;
  }

  const list=
    document.createElement('div');

  list.className='matchList';

  visible.forEach((f,i)=>{

    const el=
      document.createElement('div');

    el.className='matchItem';

    const langs=
      (
        f.languages||
        f.audio_languages||
        []
      ).join(', ');

    el.innerHTML=`
      <div>

        <strong>
          ${esc(
            f.file_name||
            `File ${page*max+i+1}`
          )}
        </strong>

        <small>
          ${esc(
            [
              langs,
              f.quality,
              f.file_size
                ?formatBytes(f.file_size)
                :''
            ]
            .filter(Boolean)
            .join(' • ')
          )}
        </small>

      </div>

      <div class="matchActions">

        <button
          class="watch mini"
          type="button"
        >
          ▶
        </button>

        <button
          class="download mini"
          type="button"
        >
          ⬇
        </button>

      </div>
    `;

    el
      .querySelector('.watch')
      .onclick=
        ()=>selectAndAccess(
          f,
          'watch'
        );

    el
      .querySelector('.download')
      .onclick=
        ()=>selectAndAccess(
          f,
          'download'
        );

    list.append(el);
  });

  box.append(list);

  if(total>max){

    const nav=
      document.createElement('div');

    nav.className='pager';

    const pages=
      Math.ceil(total/max);

    for(let i=0;i<pages;i++){

      const b=
        document.createElement('button');

      b.type='button';

      b.className=
        `pageBtn ${
          i===page?'active':''
        }`;

      b.textContent=i+1;

      b.onclick=()=>{
        row._page=i;

        renderMatches(
          box,
          d,
          row,
          item,
          opt
        );
      };

      nav.append(b);
    }

    box.append(nav);
  }
}


function formatBytes(n){

  n=Number(n||0);

  if(!n)return '';

  const u=[
    'B',
    'KB',
    'MB',
    'GB',
    'TB'
  ];

  let i=0;

  while(
    n>=1024 &&
    i<u.length-1
  ){
    n/=1024;
    i++;
  }

  return `${n.toFixed(
    n>=10||i===0?0:1
  )} ${u[i]}`;
}


function selectAndAccess(
  file,
  mode
){
  currentFile=file;
  access(mode);
}


async function search(
  q,
  page=0
){

  q=(q||'').trim();

  if(!q)return;

  show('searchSection');

  $('resultTitle').textContent=
    `Searching “${q}”…`;

  $('results').innerHTML=
    '<p class="muted">Searching the real catalog…</p>';

  try{

    const d=
      await API.get(
        `/api/search?q=${
          encodeURIComponent(q)
        }&page=${page}`
      );

    const items=d.items||[];

    window._searchPageSize=
      Number(d.page_size||20);

    window._searchQuery=q;

    window._searchTotal=
      Number(
        d.total||
        d.count||
        0
      );

    $('resultTitle').textContent=
      `${window._searchTotal} result${
        window._searchTotal===1?'':'s'
      } found`;

    $('results').innerHTML='';

    if(!items.length){

      $('results').innerHTML=
        '<p class="muted">Nothing found. Try a simpler title or spelling.</p>';

      return;
    }

    items.forEach(
      item=>
        $('results').append(
          renderSearchCard(item)
        )
    );

    const pages=
      Math.ceil(
        window._searchTotal/
        window._searchPageSize
      );

    if(pages>1){

      const nav=
        document.createElement('div');

      nav.className=
        'pager searchPager';

      for(let i=0;i<pages;i++){

        const b=
          document.createElement('button');

        b.type='button';

        b.className=
          `pageBtn ${
            i===page?'active':''
          }`;

        b.textContent=i+1;

        b.onclick=
          ()=>search(q,i);

        nav.append(b);
      }

      $('results').append(nav);
    }

  }catch(e){

    $('results').innerHTML=
      `<p class="muted">
        ${esc(e.message)}
      </p>`;
  }
}


async function openTitle(item){

  try{

    const d=
      await API.get(
        `/api/title/${
          encodeURIComponent(item.id)
        }?q=${
          encodeURIComponent(item.title||'')
        }${
          item.year
            ?`&year=${
              encodeURIComponent(item.year)
            }`
            :''
        }`
      );

    currentTitle=d;

    renderDetail(d);

    show('detail');

  }catch(e){

    toast(e.message);
  }
}


function renderDetail(d){

  const html=`
    <div class="detail">

      <p class="detailKicker">
        ${
          esc(
            d.type==='series'
              ?'WEB SERIES'
              :'MOVIE'
          )
        }
      </p>

      <h2>
        ${esc(d.title)}
        ${
          d.year
            ?` <span class="muted">
              (${esc(d.year)})
            </span>`
            :''
        }
      </h2>

      ${
        d.description
          ?`<p class="detailDesc">
            ${esc(d.description)}
          </p>`
          :''
      }

      <div id="detailFilters"></div>

      <div id="detailMatches">
        <span class="muted">
          Loading files…
        </span>
      </div>

    </div>
  `;

  $('detailBody').innerHTML=html;

  const item={
    id:d.id,
    title:d.title,
    year:d.year,
    type:d.type
  };

  const row=$('detailBody');

  row._page=0;

  row._pageSize=
    window._searchPageSize||20;

  row._filterState={
    language:null,
    quality:null,
    season:null,
    episode:null
  };

  (async()=>{

    try{

      const opt=
        await API.get(
          `/api/filter-options?id=${
            encodeURIComponent(item.id)
          }&title=${
            encodeURIComponent(item.title)
          }`
        );

      const groups=
        buildFilterGroups(
          opt,
          row._filterState
        );

      renderInlineGroups(
        $('detailFilters'),
        groups,
        row._filterState,
        row,
        item,
        opt
      );

      await applyDetailFilter(
        item,
        row
      );

    }catch(e){

      $('detailMatches').innerHTML=
        `<p class="muted">
          ${esc(e.message)}
        </p>`;
    }

  })();
}


async function applyDetailFilter(
  item,
  row
){

  const s=
    row._filterState||{};

  const q=
    new URLSearchParams({
      id:item.id,
      title:item.title
    });

  if(s.language)
    q.set('language',s.language);

  if(s.quality)
    q.set('quality',s.quality);

  if(s.season)
    q.set('season',s.season);

  if(s.episode)
    q.set('episode',s.episode);

  const box=$('detailMatches');

  box.innerHTML=
    '<span class="muted">Finding matching files…</span>';

  try{

    const d=
      await API.get(
        '/api/filter?'+q.toString()
      );

    renderDetailMatches(
      box,
      d,
      row,
      item
    );

  }catch(e){

    box.innerHTML=
      `<p class="muted">
        ${esc(e.message)}
      </p>`;
  }
}


function renderDetailMatches(
  box,
  d,
  row,
  item
){

  const matches=d.matches||[];

  const max=
    Number(row._pageSize||20);

  const page=
    Math.max(
      0,
      Number(row._page||0)
    );

  const pages=
    Math.ceil(
      matches.length/max
    );

  box.innerHTML=`
    <div class="matchHead">

      <strong>
        ${matches.length}
        matching file${
          matches.length===1?'':'s'
        }
      </strong>

      <span>
        ${
          pages>1
            ?`Page ${
              page+1
            } of ${pages}`
            :''
        }
      </span>

    </div>
  `;

  const list=
    document.createElement('div');

  list.className='matchList';

  matches
    .slice(
      page*max,
      page*max+max
    )
    .forEach(f=>{

      const el=
        document.createElement('div');

      el.className='matchItem';

      el.innerHTML=`
        <div>

          <strong>
            ${esc(
              f.file_name||'Media'
            )}
          </strong>

          <small>
            ${esc(
              [
                (
                  f.languages||
                  f.audio_languages||
                  []
                ).join(', '),

                f.quality,

                f.file_size
                  ?formatBytes(f.file_size)
                  :''
              ]
              .filter(Boolean)
              .join(' • ')
            )}
          </small>

        </div>

        <div class="matchActions">

          <button
            class="watch mini"
            type="button"
          >
            ▶
          </button>

          <button
            class="download mini"
            type="button"
          >
            ⬇
          </button>

        </div>
      `;

      el
        .querySelector('.watch')
        .onclick=
          ()=>selectAndAccess(
            f,
            'watch'
          );

      el
        .querySelector('.download')
        .onclick=
          ()=>selectAndAccess(
            f,
            'download'
          );

      list.append(el);
    });

  box.append(list);

  if(pages>1){

    const nav=
      document.createElement('div');

    nav.className='pager';

    for(let i=0;i<pages;i++){

      const b=
        document.createElement('button');

      b.type='button';

      b.className=
        `pageBtn ${
          i===page?'active':''
        }`;

      b.textContent=i+1;

      b.onclick=()=>{
        row._page=i;

        renderDetailMatches(
          box,
          d,
          row,
          item
        );
      };

      nav.append(b);
    }

    box.append(nav);
  }
}


async function access(mode){

  if(!currentFile){
    toast(
      'Choose a real file first.'
    );
    return;
  }

  currentRetry=
    info=>openVerify(
      info,
      mode
    );

  try{

    await Player.open(
      currentFile,
      mode,
      currentRetry
    );

  }catch(e){

    toast(e.message);
  }
}


function formatVerificationDuration(seconds){
  const value=Math.max(0,Number(seconds)||0);
  if(value===0)return 'now';
  if(value%86400===0){const n=value/86400;return `${n} day${n===1?'':'s'}`;}
  if(value%3600===0){const n=value/3600;return `${n} hour${n===1?'':'s'}`;}
  if(value%60===0){const n=value/60;return `${n} minute${n===1?'':'s'}`;}
  return `${value} seconds`;
}


function openVerify(
  info,
  mode
){

  const stage = Number(info.stage||1);
  const shortenerName = info.shortener_name || `Shortener ${stage}`;
  const gap = Number(info.stage_gap_seconds||0);
  const gapText = gap > 0
    ? `After you complete this step, you will be free for ${formatVerificationDuration(gap)} before the next enabled verification step.`
    : `After you complete this step, the next enabled verification step will be available immediately.`;

  $('verifyText').textContent=
    info.verification_error||
    `Verification Step ${stage} — ${shortenerName}. Please complete this step before you can ${
      mode==='download'
        ?'download'
        :'watch'
    } this file. ${gapText}`;

  $('verifyLink').href=
    info.verification_url||'#';

  $('verifyLink').classList.toggle(
    'hidden',
    !info.verification_url
  );

  $('tutorialLink').classList.toggle(
    'hidden',
    !info.tutorial_url
  );

  if(info.tutorial_url){
    $('tutorialLink').href=
      info.tutorial_url;
  }

  $('verifyState').textContent=
    `Complete Step ${stage} (${shortenerName}), return here, then press “Check again”.`;

  $('verifyRefresh').onclick=
    async()=>{
      try{

        const s=
          await API.get(
            '/api/access-status'
          );

        if(
          s.verified||
          s.premium
        ){

          hide('verify');

          await Player.open(
            currentFile,
            mode,
            currentRetry
          );

        }else{

          toast(
            'Verification is not complete yet.'
          );
        }

      }catch(e){

        toast(e.message);
      }
    };

  $('buyPremiumFromVerify').onclick=
    ()=>{
      hide('verify');

      $('premiumSection')
        .scrollIntoView({
          behavior:'smooth'
        });
    };

  show('verify');
}


/* =========================
   PREMIUM / PAYMENT
   ========================= */

function setPremiumButtonState(
  button,
  busy,
  label
){

  if(!button)return;

  if(busy){

    if(!button.dataset.originalText){
      button.dataset.originalText=
        button.textContent;
    }

    button.disabled=true;

    button.classList.add(
      'isBusy'
    );

    button.textContent=
      label||'Processing…';

  }else{

    button.disabled=false;

    button.classList.remove(
      'isBusy'
    );

    button.textContent=
      button.dataset.originalText||
      'Buy';
  }
}


async function loadPremium(){

  try{

    const d=
      await API.get(
        '/api/premium'
      );

    $('premiumBadge').textContent=
      d.premium
        ?`ACTIVE • ${
          new Date(
            d.expires_at*1000
          ).toLocaleDateString()
        }`
        :'FREE';

    $('premiumCopy').textContent=
      d.premium
        ?'Premium is active. Verification is bypassed while it remains active.'
        :'Get unlimited movies & series with faster access and verification bypass while premium is active.';

    $('premiumPlans').innerHTML=
      (d.plans||[])
        .map(p=>`
          <div class="plan">

            <strong>
              ${esc(p.name)}
            </strong>

            <span>
              ₹${esc(p.price_inr)}
            </span>

            <button
              type="button"
              data-plan="${esc(p.id)}"
            >
              Buy
            </button>

          </div>
        `)
        .join('');

    $('premiumPlans')
      .querySelectorAll(
        '[data-plan]'
      )
      .forEach(
        b=>
          b.onclick=
            ()=>buyPlan(
              b.dataset.plan,
              d,
              b
            )
      );

    const manualAllowed=(String(d.activation_mode||'environment')==='manual') || (String(d.activation_mode||'environment')==='environment' && (d.provider==='manual'||d.provider==='both') && d.manual_enabled !== false);

    if(manualAllowed){

      show('manualPay');

      $('manualInstructions').textContent=d.manual_instructions||'Pay using the configured UPI/QR, then submit the UTR/reference and screenshot for admin approval.';
      $('manualUpi').textContent=d.upi_id ? `UPI ID: ${d.upi_id}` : 'UPI ID is not configured yet.';

      $('manualPlan').innerHTML=
        (d.plans||[])
          .map(p=>`
            <option value="${esc(p.id)}">
              ${esc(p.name)}
              — ₹${esc(p.price_inr)}
            </option>
          `)
          .join('');

      if(d.manual_qr){

        $('manualQr').src=
          d.manual_qr;

        show('manualQr');
      }

      loadManualHistory();
    }

  }catch(e){

    toast(
      `Premium could not be loaded: ${e.message}`
    );
  }
}


async function buyPlan(
  id,
  d,
  button
){

  const p=
    (d.plans||[])
      .find(x=>x.id===id);

  if(!p)return;

  const mode=
    String(
      d.activation_mode||
      'environment'
    );


  /* Manual mode */

  if(
    mode==='manual'||
    (mode==='environment' && (d.provider==='manual'||d.provider==='both') && d.manual_enabled !== false)
  ){

    const sel=
      $('manualPlan');

    if(sel){
      sel.value=id;
    }

    show('manualPay');

    $('manualPay')
      .scrollIntoView({
        behavior:'smooth',
        block:'center'
      });

    toast(
      'Upload your payment screenshot for admin approval.'
    );

    return;
  }


  /* Razorpay */

  if(
    d.provider==='razorpay'||
    d.provider==='both'
  ){

    setPremiumButtonState(
      button,
      true,
      'Creating payment…'
    );

    try{

      const o=
        await API.post(
          '/api/premium/order',
          {
            plan_id:id
          }
        );

      if(!window.Razorpay){

        toast(
          'Razorpay checkout is unavailable. You can use manual payment if enabled.'
        );

        return;
      }

      const checkout=
        new Razorpay({

          key:o.key_id,

          amount:o.amount,

          currency:o.currency,

          name:'VYRA',

          description:p.name,

          order_id:o.order_id,

          handler:async r=>{

            try{

              toast(
                'Verifying payment…'
              );

              await API.post(
                '/api/premium/verify',
                r
              );

              toast(
                '✓ Premium activated'
              );

              await loadPremium();

            }catch(e){

              toast(
                `Payment verification failed: ${e.message}`
              );
            }
          },

          modal:{
            ondismiss:()=>{
              toast(
                'Payment window closed.'
              );
            }
          }

        });

      checkout.open();

    }catch(e){

      const msg=
        String(
          e.message||
          'Payment could not be started.'
        );

      if(
        /authentication failed|razorpay|key|credential/i
          .test(msg)
      ){

        toast(
          'Razorpay is not configured correctly yet. Add matching Razorpay credentials in Koyeb.'
        );

      }else{

        toast(
          `Payment could not be started: ${msg}`
        );
      }

    }finally{

      setPremiumButtonState(
        button,
        false
      );
    }

  }else{

    toast(
      'Premium purchase is not configured.'
    );
  }
}


async function loadManualHistory(){

  try{

    const d=
      await API.get(
        '/api/premium/manual'
      );

    const box=
      $('manualHistory');

    if(!box)return;

    if(!d.requests?.length){

      box.innerHTML=
        '<p class="muted">No manual payment requests yet.</p>';

      return;
    }

    box.innerHTML=
      '<h3>Payment status</h3>'+
      d.requests
        .map(r=>`
          <div class="manualHistoryItem">

            <strong>
              ${esc(
                r.plan_name||
                r.plan_id
              )}
            </strong>

            <span>
              ₹${esc(r.amount)}
            </span>

            <small>
              ${esc(String(r.status||'pending').toUpperCase())}
              ${r.utr ? ` • UTR: ${esc(r.utr)}` : ''}
            </small>

          </div>
        `)
        .join('');

  }catch(e){

    const box=
      $('manualHistory');

    if(box){

      box.innerHTML=
        `<p class="muted">
          Could not load payment history:
          ${esc(e.message)}
        </p>`;
    }
  }
}


$('manualSubmit').onclick=
  async()=>{

    const button=
      $('manualSubmit');

    const f=
      $('premiumProof')
        .files[0];

    const plan=
      $('manualPlan').value;

    if(!f||!plan){

      toast(
        'Select a plan and payment screenshot.'
      );

      return;
    }

    const fd=
      new FormData();

    fd.append(
      'proof',
      f
    );

    fd.append('plan_id', plan);

    fd.append('utr', utr);

    fd.append(
      'note',
      $('premiumNote').value
    );

    button.disabled=true;

    button.classList.add(
      'isBusy'
    );

    button.textContent=
      'Submitting…';

    $('manualState').textContent=
      'Uploading payment proof…';

    try{

      const r=
        await fetch(
          '/api/premium/manual',
          {
            method:'POST',
            body:fd,
            credentials:'same-origin'
          }
        );

      const d=
        await r.json()
          .catch(()=>({}));

      if(!r.ok){

        throw new Error(
          d.error||
          d.message||
          `Request failed (${r.status})`
        );
      }

      $('manualState').textContent=
        d.message||
        '✓ Proof submitted. Waiting for admin approval.';

      $('manualState').className=
        'manualSuccess';

      toast(
        '✓ Payment proof submitted.'
      );

      $('premiumProof').value='';
      $('premiumUtr').value='';
      $('premiumNote').value='';

      await loadManualHistory();

    }catch(e){

      $('manualState').textContent=
        `Payment proof failed: ${e.message}`;

      $('manualState').className=
        'manualError';

      toast(e.message);

    }finally{

      button.disabled=false;

      button.classList.remove(
        'isBusy'
      );

      button.textContent=
        'Submit proof';
    }
  };



/* =========================
   WEBSITE ACCOUNT
   ========================= */

let accountData = null;

function accountGate(showGate=true){
  if(showGate) show('accountGate');
  else hide('accountGate');
}

function setAccountMode(mode){
  const create = mode === 'create';
  $('showCreateAccount')?.classList.toggle('active', create);
  $('showLoginAccount')?.classList.toggle('active', !create);
  $('createAccountBox')?.classList.toggle('hidden', !create);
  $('loginAccountBox')?.classList.toggle('hidden', create);
  $('accountKeyBox')?.classList.add('hidden');
  $('accountTitle').textContent = create ? 'Create your account' : 'Login to your account';
  $('accountSubtitle').textContent = create
    ? 'Create a simple website account. No phone number or social login is required.'
    : 'Use your permanent 8-digit User ID and the separate Account Key you received when the account was created.';
  $('accountState').textContent = '';
}

function renderAccountPanel(data){
  accountData = data;
  if(!data?.authenticated){
    $('accountBtn').textContent = 'Account';
    return;
  }
  const u=data.user||{};
  const p=data.premium||{};
  $('accountBtn').textContent = u.nickname ? u.nickname.slice(0,16) : 'Account';
  $('profileNickname').textContent = u.nickname || '';
  $('profileUserId').textContent = u.user_id || '';
  $('profileNicknameInput').value = u.nickname || '';
  $('profilePremium').textContent = p.premium ? `ACTIVE • ${p.plan_name||p.plan||''}` : 'INACTIVE';
  $('profileExpiry').textContent = p.expires_at ? new Date(p.expires_at*1000).toLocaleString() : '—';
}

async function bootstrapAccount(){
  try{
    const d=await API.get('/api/account/me');
    if(d.authenticated){
      renderAccountPanel(d);
      accountGate(false);
      show('app');
      await loadPremium();
      return true;
    }
    hide('app');
    accountGate(true);
    setAccountMode('create');
    return false;
  }catch(e){
    hide('app');
    accountGate(true);
    $('accountState').textContent='Account service is unavailable. Please try again.';
    return false;
  }
}

$('showCreateAccount').onclick=()=>setAccountMode('create');
$('showLoginAccount').onclick=()=>setAccountMode('login');

$('createAccountBtn').onclick=async()=>{
  const button=$('createAccountBtn');
  const nickname=$('accountNickname').value.trim();
  if(nickname.length<2){$('accountState').textContent='Enter a nickname with at least 2 characters.';return;}
  button.disabled=true; button.textContent='Creating…'; $('accountState').textContent='Creating your permanent website account…';
  try{
    const d=await API.post('/api/account/create',{nickname});
    $('newAccountKey').textContent=d.account_key;
    $('newAccountUserId').textContent=d.user.user_id;
    $('accountState').textContent=`Account created. User ID: ${d.user.user_id}`;
    $('accountKeyBox').classList.remove('hidden');
    $('createAccountBox').classList.add('hidden');
    $('loginAccountBox').classList.add('hidden');
  }catch(e){$('accountState').textContent=e.message;}finally{button.disabled=false;button.textContent='Create account';}
};


async function copyText(value){
  try{ await navigator.clipboard.writeText(String(value||'')); toast('Copied.'); }
  catch(_){ toast('Copy failed. Long-press the value to copy it.'); }
}
$('copyNewUserId').onclick=()=>copyText($('newAccountUserId').textContent);
$('copyNewAccountKey').onclick=()=>copyText($('newAccountKey').textContent);
$('copyProfileUserId').onclick=()=>copyText($('profileUserId').textContent);

$('continueAccountBtn').onclick=async()=>{
  accountGate(false);
  show('app');
  await bootstrapAccount();
  loadHome();
};

$('loginAccountBtn').onclick=async()=>{
  const button=$('loginAccountBtn');
  const user_id=$('loginUserId').value.trim();
  const account_key=$('loginAccountKey').value.trim();
  if(!/^\d{8}$/.test(user_id)||!account_key){$('accountState').textContent='Enter the 8-digit User ID and Account Key.';return;}
  button.disabled=true; button.textContent='Logging in…'; $('accountState').textContent='';
  try{
    const d=await API.post('/api/account/login',{user_id,account_key});
    await bootstrapAccount();
    toast(`Welcome back, ${d.user.nickname}`);
    loadHome();
  }catch(e){$('accountState').textContent=e.message;}finally{button.disabled=false;button.textContent='Login';}
};

$('accountBtn').onclick=async()=>{
  const d=await API.get('/api/account/me');
  if(!d.authenticated){accountGate(true);setAccountMode('login');return;}
  renderAccountPanel(d);show('accountPanel');
};

$('saveNickname').onclick=async()=>{
  const nickname=$('profileNicknameInput').value.trim();
  try{
    const d=await API.request('/api/account/nickname',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({nickname})});
    $('profileNickname').textContent=d.nickname;
    $('accountBtn').textContent=d.nickname.slice(0,16);
    toast('Nickname updated.');
  }catch(e){toast(e.message);}
};

$('logoutAccount').onclick=async()=>{
  try{
    await API.post('/api/account/logout',{});
    hide('accountPanel');
    hide('app');
    accountGate(true);
    setAccountMode('login');
    $('loginUserId').value='';
    $('loginAccountKey').value='';
    toast('Logged out.');
  }catch(e){toast(e.message);}
};

/* =========================
   MAIN CONTROLS
   ========================= */

$('homeSearch').onclick=
  ()=>search(
    $('homeQuery').value
  );

$('homeQuery').onkeydown=
  e=>{
    if(e.key==='Enter'){
      search(e.target.value);
    }
  };

$('searchBtn').onclick=
  ()=>{
    $('homeQuery').focus();

    window.scrollTo({
      top:0,
      behavior:'smooth'
    });
  };

$('clearSearch').onclick=
  ()=>{
    hide('searchSection');
    $('results').innerHTML='';
  };

$('homeBtn').onclick=
  ()=>{
    window.scrollTo({
      top:0,
      behavior:'smooth'
    });
  };

$('premiumBtn').onclick=
  ()=>
    $('premiumSection')
      .scrollIntoView({
        behavior:'smooth'
      });

document
  .querySelectorAll('[data-close]')
  .forEach(
    b=>
      b.onclick=
        ()=>hide(
          b.dataset.close
        )
  );

$('playerClose').onclick=
  ()=>Player.close();

(async()=>{
  const authenticated=await bootstrapAccount();
  if(authenticated){
    loadHome();
  }
})();
