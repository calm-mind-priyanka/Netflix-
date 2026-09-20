let titles=[];
let searchTitles=[];
const $=selector=>document.querySelector(selector);

const escapeHtml=value=>String(value??"").replace(/[&<>"']/g,match=>({
  "&":"&amp;",
  "<":"&lt;",
  ">":"&gt;",
  '"':"&quot;",
  "'":"&#39;"
}[match]));

function poster(title){
  if(!title.poster){
    return `<div class="poster posterFallback">🎬<span class="cardShade"></span></div>`;
  }

  return `<div class="poster">
    <img
      class="posterImage"
      src="${escapeHtml(title.poster)}"
      alt=""
      loading="lazy"
      decoding="async"
      referrerpolicy="no-referrer"
      onerror="this.remove();this.parentElement.classList.add('posterFallback')"
    >
    <span class="cardShade"></span>
  </div>`;
}

function card(title){
  const season=title.search_season??"";
  const episode=title.search_episode??"";
  const rawFile=String(title.id||"").startsWith("file:");
  return `<article class="card${rawFile?" searchFileCard":""}" data-id="${escapeHtml(title.id)}" data-raw-file="${rawFile?"1":"0"}" data-season="${escapeHtml(season)}" data-episode="${escapeHtml(episode)}">
    ${poster(title)}
    <strong>${escapeHtml(title.title)}</strong>
    <small>${title.type==="series"?"Series":"Movie"}${title.year?" • "+title.year:""}</small>
  </article>`;
}

function state(element,message,kind="loading"){
  if(element) element.innerHTML=`<div class="state ${kind}">${escapeHtml(message)}</div>`;
}

async function load(){
  state($("#latest"),"Loading catalog…");
  state($("#movies"),"Loading…");
  state($("#series"),"Loading…");

  try{
    const data=await API.get("/api/home");

    if(!Array.isArray(data.items)){
      throw new Error(data.error||"Invalid catalog response");
    }

    titles=data.items;

    $("#latest").innerHTML=titles.slice(0,18).map(card).join("")
      ||'<div class="state empty">No movies indexed yet.</div>';

    $("#movies").innerHTML=titles
      .filter(item=>item.type==="movie")
      .slice(0,24)
      .map(card)
      .join("")
      ||'<div class="state empty">No movies found.</div>';

    $("#series").innerHTML=titles
      .filter(item=>item.type==="series")
      .slice(0,24)
      .map(card)
      .join("")
      ||'<div class="state empty">No series found.</div>';

    bind();

    if(titles[0]){
      const hero=titles[0];

      $("#hero").style.backgroundImage=hero.backdrop
        ? `linear-gradient(90deg,rgba(5,5,5,.98),rgba(5,5,5,.55),rgba(5,5,5,.15)),url('${escapeHtml(hero.backdrop)}')`
        : "linear-gradient(90deg,#080808,#171717)";

      $("#hero").innerHTML=`<div class="heroText">
        <span class="eyebrow">STREAMBOX ORIGINAL CATALOG</span>
        <h1>${escapeHtml(hero.title)}</h1>
        <p>${escapeHtml(hero.description||"Discover and stream your next movie or series.")}</p>
        <button class="primary" id="heroWatch">▶ Watch now</button>
      </div>`;

      $("#heroWatch").onclick=()=>showDetails(hero.id);

    }else{
      $("#hero").style.backgroundImage="linear-gradient(90deg,#080808,#171717)";

      $("#hero").innerHTML=`<div class="heroText">
        <span class="eyebrow">STREAMBOX</span>
        <h1>Your catalog is empty</h1>
        <p>No readable movie records were returned from the configured Auto Filter Bot database.</p>
      </div>`;
    }

    renderContinue();
    return true;

  }catch(error){
    const message=error.message||"Unable to load the catalog.";

    ["latest","movies","series"].forEach(id=>{
      state(
        $("#"+id),
        `Catalog error: ${message}`,
        "error"
      );
    });

    $("#hero").style.backgroundImage=
      "linear-gradient(90deg,#080808,#171717)";

    $("#hero").innerHTML=`<div class="heroText">
      <span class="eyebrow">STREAMBOX</span>
      <h1>Catalog unavailable</h1>
      <p>The website is running, but the movie database could not be read. Check the backend/API status.</p>
      <button class="primary" id="retryCatalog">Retry</button>
    </div>`;

    $("#retryCatalog").onclick=load;
    return false;
  }
}

function bind(){
  document.querySelectorAll(".card").forEach(element=>{
    // Raw Auto Filter search results are file records, not catalog/title IDs.
    // They must never open the title-detail resolver because that resolver
    // expects a grouped catalog ID and would show "file options unavailable".
    if(element.dataset.rawFile === "1" || String(element.dataset.id||"").startsWith("file:")){
      element.onclick=null;
      element.style.cursor="default";
      return;
    }
    element.onclick=()=>showDetails(
      element.dataset.id,
      element.dataset.season?Number(element.dataset.season):null,
      element.dataset.episode?Number(element.dataset.episode):null
    );
  });
}

function renderContinue(){
  let progress={};

  try{
    progress=
      JSON.parse(
        localStorage.getItem("stream_progress")||"{}"
      )||{};
  }catch(_){
    progress={};
  }

  const ids=Object.keys(progress)
    .sort(
      (a,b)=>
        (progress[b].updated||0)-
        (progress[a].updated||0)
    )
    .map(id=>titles.find(title=>title.id===id))
    .filter(Boolean);

  if(!ids.length)return;

  $("#continue").classList.remove("hidden");

  $("#continueRow").innerHTML=
    ids.slice(0,12).map(card).join("");

  bind();
}

async function renderAutoFilter(title, mount, initialSeason=null, initialEpisode=null, initialLanguage=null, initialQuality=null){
  const esc=escapeHtml;
  const state={
    season:initialSeason,
    episode:initialEpisode,
    language:initialLanguage,
    quality:initialQuality,
    subtitle:null
  };

  // The detail response already contains the bounded real-file pool. Derive
  // controls from that pool so we never invent seasons/episodes or make a
  // second Mongo request just to draw the filter UI.
  const variants=[];
  if(title.type==="series"){
    for(const season of (title.seasons||[])){
      for(const episode of (season.episodes||[])){
        for(const variant of (episode.variants||[])){
          variants.push(variant);
        }
      }
    }
  }else{
    variants.push(...(title.variants||[]));
  }

  const actualSeasons=[...new Set((title.available_seasons||title.seasons?.map(x=>x.season)||[]).map(Number))]
    .filter(Number.isFinite).sort((a,b)=>a-b);
  const normalSeasons=actualSeasons.filter(value=>value>=1&&value<=15);
  const extendedSeasons=actualSeasons.filter(value=>value>15);

  const actualLanguages=[...new Set(
    variants.flatMap(v=>[
      ...(v.audio_languages||[]),
      ...(v.languages||[])
    ]).filter(Boolean).map(String)
  )].sort((a,b)=>a.localeCompare(b));
  const languages=actualLanguages.length
    ?actualLanguages
    :["Malayalam","Tamil","English","Hindi","Telugu","Kannada","Gujarati","Marathi","Punjabi"];

  const actualQualities=[...new Set(
    variants.map(v=>String(v.quality||"").trim()).filter(v=>v&&v.toLowerCase()!=="auto")
  )].sort((a,b)=>{
    const an=parseInt(a,10)||0, bn=parseInt(b,10)||0;
    return an-bn||a.localeCompare(b);
  });
  const qualities=actualQualities.length
    ?actualQualities
    :["360P","480P","720P","1080P","1440P","2160P"];

  const captionLanguages=[...new Set(
    variants.flatMap(v=>v.subtitle_languages||[]).filter(Boolean).map(String)
  )].sort((a,b)=>a.localeCompare(b));

  const episodesForSeason=seasonNumber=>{
    const season=(title.seasons||[]).find(item=>Number(item.season)===Number(seasonNumber));
    return [...new Set((season?.episode_numbers||season?.episodes?.map(x=>x.episode)||[]).map(Number))]
      .filter(Number.isFinite).sort((a,b)=>a-b);
  };

  const button=(kind,value,label=value,active=false)=>`<button type="button" class="afChoice${active?" active":""}" data-af-kind="${esc(kind)}" data-af-value="${esc(value)}">${esc(label)}</button>`;
  const selectedLabel=()=>[
    state.season!=null?`Season ${state.season}`:null,
    state.episode!=null?`Episode ${String(state.episode).padStart(2,"0")}`:null,
    state.language?`Language: ${state.language}`:null,
    state.quality?`Quality: ${state.quality}`:null,
    state.subtitle?`Caption: ${state.subtitle}`:null
  ].filter(Boolean).join(" • ") || "Select an option";

  function draw(statusText="",statusKind="idle"){
    const episodes=state.season!=null?episodesForSeason(state.season):[];
    mount.innerHTML=`<section class="variantChoices autoFilterPanel">
      <div class="afHeader">
        <div>
          <b>🔎 AUTO FILTER ACTIVE</b>
          <small>${esc(title.title)}${selectedLabel()!=="Select an option"?" • "+esc(selectedLabel()):""}</small>
        </div>
        <span id="afStatus" class="afStatus ${esc(statusKind)}">${esc(statusText)}</span>
      </div>

      ${title.type==="series" ? `<div class="afGroup"><small>Season</small>
        <div class="afChoices">
          ${normalSeasons.map(v=>button("season",v,`Season ${v}`,state.season===v)).join("")}
          ${extendedSeasons.map(v=>button("season",v,`Season ${v}`,state.season===v)).join("")}
        </div>
      </div>`:""}

      ${title.type==="series"&&state.season!=null?`<div class="afGroup"><small>Episode</small>
        <div class="afChoices">
          ${episodes.map(v=>button("episode",v,`Episode ${String(v).padStart(2,"0")}`,state.episode===v)).join("")||
            "<span class='afEmpty'>No actual episodes found for this season.</span>"}
        </div>
      </div>`:""}

      <div class="afGroup"><small>Language</small><div class="afChoices">
        ${languages.map(v=>button("language",v,v,state.language===v)).join("")}
      </div></div>

      <div class="afGroup"><small>Quality</small><div class="afChoices">
        ${qualities.map(v=>button("quality",v,v,state.quality===v)).join("")}
      </div></div>

      ${captionLanguages.length?`<div class="afGroup"><small>Caption / Subtitle</small><div class="afChoices">
        ${captionLanguages.map(v=>button("subtitle",v,v,state.subtitle===v)).join("")}
      </div></div>`:""}

      <div id="afResult" class="afResult"></div>
    </section>`;

    mount.querySelectorAll(".afChoice").forEach(btn=>btn.onclick=async()=>{
      const kind=btn.dataset.afKind, value=btn.dataset.afValue;

      if(kind==="season"){
        state.season=Number(value);
        state.episode=null;
        state.subtitle=null;
        draw("⏳ FETCHING SEASON FILES…","fetching");
        await checkSelection();
        return;
      }

      if(kind==="episode") state.episode=Number(value);
      if(kind==="language") state.language=value;
      if(kind==="quality") state.quality=value;
      if(kind==="subtitle") state.subtitle=value;
      await checkSelection();
    });
  }

  async function checkSelection(){
    const result=mount.querySelector("#afResult");
    const status=mount.querySelector("#afStatus");
    if(status){
      status.className="afStatus fetching";
      status.textContent="⏳ FETCHING MATCHING FILES…";
    }
    if(result) result.innerHTML="";

    const params={title:title.title,id:title.id};
    if(title.year!=null)params.year=String(title.year);
    if(state.season!=null)params.season=String(state.season);
    if(state.episode!=null)params.episode=String(state.episode);
    if(state.language)params.language=state.language;
    if(state.quality)params.quality=state.quality;
    if(state.subtitle)params.subtitle=state.subtitle;

    try{
      const data=await API.get("/api/filter?"+new URLSearchParams(params));
      const matches=Array.isArray(data.matches)?data.matches.filter(file=>file?.file_id):[];
      const files=matches.length?matches:(data.file?.file_id?[data.file]:[]);
      const count=Number(data.count)||files.length;

      if(status){
        status.className=`afStatus ${count?"complete":"empty"}`;
        status.textContent=count
          ?`✅ FILTER COMPLETE • 📁 ${count} MATCHING FILE${count===1?"":"S"} FOUND`
          :"⚠️ NO MATCHING FILES FOUND";
      }

      if(result){
        result.innerHTML=`<div class="afResultSummary">
          <div>📺 ${state.season!=null?`S${String(state.season).padStart(2,"0")}${state.episode!=null?` • E${String(state.episode).padStart(2,"0")}`:""}`:"Title-wide"}
            ${state.language?` • 🌐 ${esc(state.language)}`:""}
            ${state.quality?` • 🎞 ${esc(state.quality)}`:""}
          </div>
          ${files.length?`<button type="button" class="primary" id="afPlay">🔴 PLAY</button>`:""}
        </div>`;
      }

      result?.querySelector("#afPlay")?.addEventListener("click",()=>{
        const file=files[0];
        const playFiles=files.slice(0,100);
        const season=file.season??state.season;
        const episode=file.episode??state.episode;
        Player.open(
          title.id,
          title.type==="series"
            ?`${title.title} • S${String(season||0).padStart(2,"0")} E${String(episode||0).padStart(2,"0")}`
            :title.title,
          playFiles,
          null,
          {title:title.title,type:title.type,year:title.year??null,season,episode}
        );
      });
    }catch(e){
      if(status){
        status.className="afStatus error";
        status.textContent="⚠️ FILTER REQUEST FAILED";
      }
      if(result)result.innerHTML=`<div class="state error" style="padding:12px 0">${esc(e.message||"Unable to query matching files")}</div>`;
    }
  }

  draw(
    (initialSeason!=null||initialEpisode!=null||initialLanguage||initialQuality)
      ?"⏳ FETCHING MATCHING FILES…"
      :"",
    (initialSeason!=null||initialEpisode!=null||initialLanguage||initialQuality)?"fetching":"idle"
  );

  // A search containing S/E (and optional language/quality tokens) should
  // immediately filter the real raw files instead of requiring another click.
  if(initialSeason!=null||initialEpisode!=null||initialLanguage||initialQuality){
    await checkSelection();
  }
}

async function showDetails(id,preferredSeason=null,preferredEpisode=null,preferredLanguage=null,preferredQuality=null){
  try{
    const localTitle=
      titles.find(item=>item.id===id)||
      searchTitles.find(item=>item.id===id)||
      null;

    let title=localTitle;
    if(localTitle?.title){
      try{
        const expanded=await API.get(
          "/api/title/"+encodeURIComponent(id)+
          "?q="+encodeURIComponent(localTitle.title)
        );
        if(expanded?.id===id || expanded?.title) title=expanded;
      }catch(_){
        // Keep the already returned search/home object usable if expansion is unavailable.
      }
    }else{
      title=await API.get("/api/title/"+encodeURIComponent(id));
    }

    if(!title) throw new Error("Title not found");

    $("#details").classList.remove("hidden");

    const detailPoster=title.poster
      ? `background-image:linear-gradient(0deg,rgba(8,8,8,.98),rgba(8,8,8,.22)),url("${escapeHtml(title.poster)}")`
      : "";

    let html=`<div class="detail" style="${detailPoster}">
      <div>
        <span class="eyebrow">${title.type==="series"?"SERIES":"MOVIE"}</span>
        <h1>${escapeHtml(title.title)}</h1>
        <p>
          ${escapeHtml(title.description||"")}
          ${title.rating?`<br>★ ${Number(title.rating).toFixed(1)}`:""}
        </p>
      </div>
    </div>`;

    // Preserve the original Netflix detail/episode interface. The Auto Filter
    // panel is mounted inside this same detail screen rather than replacing it.
    if(title.type==="movie"){
      html+=`<section class="variantChoices">
        <div class="episode">
          <span>Choose language, quality and source from Player Settings.</span>
          <button class="primary" id="playMovie">▶ Play</button>
        </div>
      </section>`;
    }else{
      const allSeasons=Array.isArray(title.seasons)?title.seasons:[];
      const seasons=allSeasons;

      if(!seasons.length){
        html+='<div class="state empty">No episodes found for this series.</div>';
      }else{
        html+=seasons.map((season,seasonIndex)=>`<section class="${preferredSeason!=null&&Number(season.season)===Number(preferredSeason)?"search-selected-season":""}">
          <h2>Season ${season.season}${preferredSeason!=null&&Number(season.season)===Number(preferredSeason)?" ← selected":""}</h2>
          ${(season.episodes||[]).map((episode,episodeIndex)=>{
            const highlighted=preferredEpisode!=null && Number(episode.episode)===Number(preferredEpisode);
            return `<div class="episode ${highlighted?"search-highlight":""}">
              <span>Episode ${episode.episode}${highlighted?" ← selected":""}</span>
              <button class="primary episodePlay" data-season="${season.season}" data-episode="${episode.episode}">▶</button>
            </div>`;
          }).join("")}
        </section>`).join("");
      }
    }

    // Keep Auto Filter in the original detail page. It supplies the real-file
    // lookup and extended-season controls without creating another page/UI.
    html+=`<div id="autoFilterPanel"></div>`;
    $("#detailBody").innerHTML=html;

    if(title.type==="movie"){
      const variants=Array.isArray(title.variants)?title.variants:[];
      $("#playMovie").onclick=()=>Player.open(
        title.id,title.title,variants,null,
        {title:title.title,type:title.type,year:title.year??null}
      );
    }else{
      const allSeasons=Array.isArray(title.seasons)?title.seasons:[];
      const seasons=allSeasons;

      $("#detailBody").querySelectorAll(".episodePlay").forEach(button=>{
        const seasonNumber=Number(button.dataset.season);
        const episodeNumber=Number(button.dataset.episode);
        const season=allSeasons.find(item=>Number(item.season)===seasonNumber);
        const episode=season?.episodes?.find(item=>Number(item.episode)===episodeNumber);
        const seasonIndex=allSeasons.findIndex(item=>Number(item.season)===seasonNumber);
        const episodeIndex=season?.episodes?.findIndex(item=>Number(item.episode)===episodeNumber)??-1;
        const nextEpisode=season?.episodes?.[episodeIndex+1];
        const nextSeason=allSeasons[seasonIndex+1]?.episodes?.[0];
        const next=nextEpisode?{
          title:title.title,year:title.year??null,season:seasonNumber,
          episode:nextEpisode.episode,variants:nextEpisode.variants||[]
        }:nextSeason?{
          title:title.title,year:title.year??null,season:allSeasons[seasonIndex+1].season,
          episode:nextSeason.episode,variants:nextSeason.variants||[]
        }:null;
        button.onclick=()=>Player.open(
          title.id,
          `${title.title} • S${String(seasonNumber).padStart(2,"0")} E${String(episodeNumber).padStart(2,"0")}`,
          episode?.variants||[],next,
          {title:title.title,type:"series",year:title.year??null,season:seasonNumber,episode:episodeNumber}
        );
      });
    }

    await renderAutoFilter(
      title,
      $("#autoFilterPanel"),
      preferredSeason,
      preferredEpisode,
      preferredLanguage,
      preferredQuality
    );
  }catch(error){
    alert(error.message||"Unable to open title");
  }
}

let searchRequestController=null;
let searchSequence=0;
let searchInFlight=false;
let lastSubmittedQuery="";

async function doSearch(){
  const query=$("#query").value.trim();
  if(!query){
    $("#results").innerHTML='<div class="state">Type a movie or series name.</div>';
    return;
  }

  // One request per submitted query. Double-clicks and repeated Enter presses
  // while the same request is running are ignored; an already completed query
  // is also not fetched again until the user changes it.
  if(searchInFlight || query.toLowerCase()===lastSubmittedQuery){
    return;
  }

  searchInFlight=true;
  const sequence=++searchSequence;
  lastSubmittedQuery=query.toLowerCase();

  state($("#results"),"Searching…");

  try{
    if(searchRequestController) searchRequestController.abort();
    searchRequestController=new AbortController();

    const data=await API.get(
      "/api/search?q="+encodeURIComponent(query),
      {signal:searchRequestController.signal}
    );

    if(sequence!==searchSequence)return;
    if(!Array.isArray(data.items)) throw new Error(data.error||"Invalid search response");

    searchTitles=data.items;
    if(!data.items.length){
      state($("#results"),`No results found for “${query}”.`,"empty");
      return;
    }

    const first=data.items[0];
    const seasonMatch=query.match(/(?:\bs|season\s*)0*(\d{1,3})\b/i);
    const episodeMatch=query.match(/(?:\be|episode\s*)0*(\d{1,4})\b/i);
    const season=seasonMatch?Number(seasonMatch[1]):null;
    const episode=episodeMatch?Number(episodeMatch[1]):null;

    // normalize_query is the backend source of truth for metadata tokens.
    // These client-side extractions only seed the existing detail filter state.
    const languageNames=["Malayalam","Tamil","English","Hindi","Telugu","Kannada","Gujarati","Marathi","Punjabi"];
    const language=languageNames.find(name=>new RegExp(`(?:^|[^a-z])${name}(?:$|[^a-z])`,"i").test(query))||null;
    const qualityMatch=query.match(/(?:^|[\s._-])(\d{3,4})p?(?:$|[\s._-])/i);
    const quality=qualityMatch?`${qualityMatch[1]}P`:null;

    $("#search").classList.add("hidden");
    await showDetails(first.id,season,episode,language,quality);
  }catch(error){
    if(error?.name==="AbortError" || sequence!==searchSequence)return;
    state($("#results"),`Search failed: ${error.message||"backend error"}`,"error");
    lastSubmittedQuery="";
  }finally{
    if(sequence===searchSequence){
      searchInFlight=false;
    }

    // A completed/aborted request must never leave the visible search panel
    // stuck on “Searching…”. Successful searches open the details panel;
    // reopening search with an empty box resets the result state in openSearch().
  }
}

function openSearch(){
  $("#search").classList.remove("hidden");

  // Never show a stale loading state from a previous completed search.
  // The search panel can be reopened after the title/details view, so reset
  // the result area when there is no active query.
  if(!$("#query").value.trim() && !searchInFlight){
    state($("#results"),"Type a movie or series name.");
  }

  $("#query").focus();
}

function showHome(){
  $("#search")?.classList.add("hidden");
  $("#details")?.classList.add("hidden");
  if(!$("#player")?.classList.contains("hidden")) Player.close();
  window.scrollTo({top:0,behavior:"smooth"});
}

$("#bottomHome").onclick=showHome;

$("#searchBtn").onclick=()=>{
  if($("#search").classList.contains("hidden")) openSearch();
  else doSearch();
};

$("#bottomSearch").onclick=()=>{
  if($("#search").classList.contains("hidden")) openSearch();
  else doSearch();
};

$("#closeSearch").onclick=()=>{
  $("#search").classList.add("hidden");
};

$("#backDetails").onclick=()=>{
  $("#details").classList.add("hidden");
};

$("#settings").onclick=()=>{
  $("#menu").classList.toggle("hidden");
};

$("#download").onclick=()=>{
  Player.download();
};

$("#closePlayer").onclick=()=>{
  Player.close();
};

$("#query").onkeydown=event=>{
  if(event.key==="Enter"){
    event.preventDefault();
    doSearch();
  }
};

function renderIntroSamples(){
  const target=$("#introSamples");
  if(!target)return;

  const samples=titles
    .filter(item=>item?.poster)
    .slice(0,4);

  target.innerHTML=samples.map(item=>
    `<div class="introPoster">
      <img
        src="${escapeHtml(item.poster)}"
        alt="${escapeHtml(item.title)}"
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        onerror="this.parentElement.remove()"
      >
    </div>`
  ).join("");
}

function showStage(id){
  ["splashStage","loadingStage","introStage"].forEach(stageId=>{
    const stage=document.getElementById(stageId);
    if(stage)stage.classList.toggle("hidden",stageId!==id);
  });
}

async function waitForWindowLoad(){
  if(document.readyState==="complete")return;
  await new Promise(resolve=>window.addEventListener("load",resolve,{once:true}));
}

async function startExperience(){
  const startup=$("#startup");
  const appShell=$("#appShell");
  const startButton=$("#startStreaming");

  if(!startup||!appShell||!startButton){
    await load();
    return;
  }

  // The splash must be visible before any network/database work can hide it.
  // Start the catalog in parallel, but never let a fast/slow API determine when
  // the logo animation begins. This guarantees users see the intro from frame 1.
  showStage("splashStage");
  const catalogPromise=load();
  await waitForWindowLoad();

  // The logo animation itself is about 1.1s including the staggered letters.
  // Keep the splash on screen long enough for the complete animation to be seen.
  await new Promise(resolve=>setTimeout(resolve,2300));

  showStage("loadingStage");
  await new Promise(resolve=>setTimeout(resolve,550));

  await Promise.race([
    catalogPromise.catch(()=>false),
    new Promise(resolve=>setTimeout(resolve,900))
  ]);

  renderIntroSamples();
  showStage("introStage");

  startButton.onclick=()=>{
    startup.classList.add("startupDone");
    appShell.classList.remove("startupHidden");
    setTimeout(()=>startup.remove(),500);
  };
}

startExperience();

// Premium plans: automatic checkout activates immediately; manual mode requires payment proof approval.
async function loadPremiumPlan(){
  const statusEl=document.getElementById('premiumStatus'), plansEl=document.getElementById('premiumPlans');
  if(!statusEl||!plansEl)return;
  try{
    const r=await fetch('/api/premium',{credentials:'same-origin'}), d=await r.json();
    if(d.premium){statusEl.textContent='Premium active • '+(d.plan_name||d.plan)+' • expires '+new Date(d.expires_at*1000).toLocaleString(); plansEl.innerHTML=''; return;}
    statusEl.textContent='Choose a plan. Automatic payment activates immediately; manual payment requires admin approval.';
    plansEl.innerHTML=(d.plans||[]).map(p=>`<div class="premiumPlan"><b>${p.name}</b><span>₹${p.price_inr}</span><small>${p.days} days</small><button class="primary planBuy" data-plan="${p.id}">Choose Plan</button></div>`).join('');
    plansEl.querySelectorAll('.planBuy').forEach(btn=>btn.onclick=()=>startPremium(btn.dataset.plan,d));
    const manual=document.getElementById('manualPremium');
    if(d.provider==='manual'||d.provider==='both'){
      manual.classList.remove('hidden'); document.getElementById('manualInstructions').textContent=d.manual_instructions||'';
      const qr=document.getElementById('manualQr'); if(d.manual_qr){qr.src=d.manual_qr;qr.classList.remove('hidden');}
      manual.dataset.plan=(d.plans&&d.plans[0]&&d.plans[0].id)||'30day';
    }
  }catch(e){statusEl.textContent='Premium status unavailable.';}
}
async function startPremium(planId,d){
  if(d.provider==='manual'||d.provider==='both'){
    const manual=document.getElementById('manualPremium'); manual.classList.remove('hidden'); manual.dataset.plan=planId; manual.scrollIntoView({behavior:'smooth',block:'center'}); return;
  }
  try{
    const r=await fetch('/api/premium/order',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({plan_id:planId})}); const o=await r.json();
    if(!o.ok){alert(o.error||'Checkout is not configured.');return;}
    if(!window.Razorpay){alert('Automatic payment provider is not configured.');return;}
    new Razorpay({key:o.key_id,amount:o.amount,currency:o.currency,name:'StreamBox',description:`${o.plan_name} Premium`,order_id:o.order_id,handler:async resp=>{const vr=await fetch('/api/premium/verify',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(resp)});const v=await vr.json();if(v.ok)loadPremiumPlan();else alert(v.error||'Payment verification failed.');}}).open();
  }catch(e){alert('Premium checkout failed.');}
}
const manualSubmit=document.getElementById('premiumManualSubmit');
if(manualSubmit)manualSubmit.onclick=async()=>{const f=document.getElementById('premiumProof').files[0], state=document.getElementById('premiumManualState'), manual=document.getElementById('manualPremium');if(!f){state.textContent='Upload the payment screenshot first.';return;}const fd=new FormData();fd.append('plan_id',manual.dataset.plan||'30day');fd.append('proof',f);fd.append('note',document.getElementById('premiumNote').value||'');manualSubmit.disabled=true;state.textContent='Sending proof…';try{const r=await fetch('/api/premium/manual',{method:'POST',credentials:'same-origin',body:fd});const d=await r.json();state.textContent=d.ok?'Proof sent. Premium will activate after admin approval.':(d.error||'Submission failed.');}catch(e){state.textContent='Submission failed.';}finally{manualSubmit.disabled=false;}};
window.addEventListener('load',loadPremiumPlan);
