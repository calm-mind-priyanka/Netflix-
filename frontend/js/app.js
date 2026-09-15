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

async function renderAutoFilter(title, mount, initialSeason=null, initialEpisode=null){
  const esc=escapeHtml;
  const state={season:initialSeason,episode:initialEpisode,language:null,quality:null};
  let options;
  try{
    options=await API.get("/api/filter-options?"+new URLSearchParams({title:title.title,id:title.id}));
  }catch(e){
    mount.innerHTML=`<div class="state error">Filter options unavailable.</div>`;
    return;
  }

  const button=(kind,value,label=value)=>`<button type="button" class="afChoice" data-af-kind="${esc(kind)}" data-af-value="${esc(value)}">${esc(label)}</button>`;
  const selectedLabel=()=>[
    state.season!=null?`Season ${state.season}`:null,
    state.episode!=null?`Episode ${state.episode}`:null,
    state.language,
    state.quality
  ].filter(Boolean).join(" • ") || "Select an option";

  function draw(message=""){
    const seasons=options.seasons||[];
    const episodes=state.season!=null?(options.episodes?.[String(state.season)]||[]):[];
    mount.innerHTML=`<div class="variantChoices" style="padding:18px;margin-top:18px;border:1px solid rgba(255,255,255,.12);border-radius:16px;background:rgba(255,255,255,.035)">
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap">
        <div><b>Auto Filter</b><small style="display:block;opacity:.65;margin-top:4px">${esc(selectedLabel())}</small></div>
        <span id="afStatus" style="opacity:.7;font-size:.9rem">${esc(message)}</span>
      </div>
      ${title.type==="series"?`<div style="margin-top:16px"><small style="display:block;opacity:.65;margin-bottom:8px">Season</small><div style="display:flex;flex-wrap:wrap;gap:8px">${seasons.map(v=>button("season",String(v).replace(/\D/g,""),v)).join("")}</div></div>`:""}
      ${title.type==="series"&&state.season!=null?`<div style="margin-top:16px"><small style="display:block;opacity:.65;margin-bottom:8px">Episode</small><div style="display:flex;flex-wrap:wrap;gap:8px">${episodes.map(v=>button("episode",String(v),`Episode ${v}`)).join("")||"<span style='opacity:.6'>No episodes found for this season.</span>"}</div></div>`:""}
      <div style="margin-top:16px"><small style="display:block;opacity:.65;margin-bottom:8px">Language</small><div style="display:flex;flex-wrap:wrap;gap:8px">${(options.languages||[]).map(v=>button("language",v)).join("")}</div></div>
      <div style="margin-top:16px"><small style="display:block;opacity:.65;margin-bottom:8px">Quality</small><div style="display:flex;flex-wrap:wrap;gap:8px">${(options.qualities||[]).map(v=>button("quality",v)).join("")}</div></div>
      <div id="afResult" style="margin-top:16px"></div>
    </div>`;
    mount.querySelectorAll(".afChoice").forEach(btn=>btn.onclick=async()=>{
      const kind=btn.dataset.afKind, value=btn.dataset.afValue;
      if(kind==="season"){
        state.season=Number(value); state.episode=null; state.language=null; state.quality=null;
        draw("Choose an episode or another filter"); return;
      }
      if(kind==="episode"){state.episode=Number(value);}
      if(kind==="language"){state.language=value;}
      if(kind==="quality"){state.quality=value;}
      await checkSelection();
    });
  }

  async function checkSelection(){
    const result=mount.querySelector("#afResult"), status=mount.querySelector("#afStatus");
    if(status)status.textContent="Checking files…";
    const params={title:title.title,id:title.id};
    if(title.year!=null)params.year=String(title.year);
    if(state.season!=null)params.season=String(state.season);
    if(state.episode!=null)params.episode=String(state.episode);
    if(state.language)params.language=state.language;
    if(state.quality)params.quality=state.quality;
    try{
      const data=await API.get("/api/filter?"+new URLSearchParams(params));
      const file=data.file;
      if(status)status.textContent=`${data.count} matching file${data.count===1?"":"s"}`;
      if(result)result.innerHTML=`<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px 0">
        <span>${esc(file?.file_name||"Matching files found")}</span>
        ${file?.file_id?`<button type="button" class="primary" id="afPlay">▶ Play selected</button>`:""}
      </div>`;
      result.querySelector("#afPlay")?.addEventListener("click",()=>{
        const next={title:title.title,year:title.year??null,season:file.season??state.season,episode:file.episode??state.episode,variants:[file]};
        Player.open(title.id,title.type==="series"?`${title.title} • S${String(next.season||0).padStart(2,"0")} E${String(next.episode||0).padStart(2,"0")}`:title.title,[file],null,{title:title.title,type:title.type,year:title.year??null,season:next.season,episode:next.episode});
      });
    }catch(e){
      if(status)status.textContent="No matching files";
      if(result)result.innerHTML=`<div class="state empty" style="padding:12px 0">NO FILES WERE FOUND</div>`;
    }
  }

  draw();
}

async function renderGlobalAutoFilter(query, items, mount){
  const state={season:null,episode:null,language:null,quality:null};
  const first=items?.[0]||{};
  const isSeries=items?.some(x=>x.type==="series") || /(?:s\d{1,2}|season\s*\d+|e\d{1,3}|episode\s*\d+)/i.test(query);
  const parsedSeason=(query.match(/(?:s|season\s*)0*(\d{1,2})/i)||[])[1];
  const parsedEpisode=(query.match(/(?:e|episode\s*)0*(\d{1,3})/i)||[])[1];
  if(parsedSeason) state.season=Number(parsedSeason);
  if(parsedEpisode) state.episode=Number(parsedEpisode);

  // Keep the original Netflix-style button treatment. These are the same
  // categories exposed by the Auto Filter bot; the raw Mongo result set is
  // always the source of truth.
  const languages=["Malayalam","Tamil","English","Hindi","Telugu","Kannada","Gujarati","Marathi","Punjabi"];
  const qualities=["360P","480P","720P","1080P","1440P","2160P"];
  const seasons=Array.from({length:15},(_,i)=>i+1);
  const episodes=Array.from({length:30},(_,i)=>i+1);
  const esc=escapeHtml;

  const button=(kind,value,label=value,active=false)=>
    `<button type="button" class="afChoice${active?" active":""}" data-af-kind="${esc(kind)}" data-af-value="${esc(value)}">${esc(label)}</button>`;

  function detailMarkup(){
    const posterStyle=first.poster
      ? `background-image:linear-gradient(0deg,rgba(8,8,8,.98),rgba(8,8,8,.22)),url("${esc(first.poster)}")`
      : "";
    const typeLabel=isSeries?"SERIES":"MOVIE";
    const description=first.description||first.caption||"Your matching files are ready. Use the filters below to choose the exact release.";
    return `<div class="detail" style="${posterStyle}">
      <div>
        <span class="eyebrow">${typeLabel}</span>
        <h1>${esc(first.title||query)}</h1>
        <p>
          ${esc(description)}
          ${first.year?`<br>${esc(first.year)}`:""}
          ${first.rating?`<br>★ ${Number(first.rating).toFixed(1)}`:""}
        </p>
        <small style="color:#aaa">${items.length} result${items.length===1?"":"s"} found</small>
      </div>
    </div>`;
  }

  function draw(status=""){
    const selected=(kind,value)=>state[kind]===value;
    mount.innerHTML=`
      ${detailMarkup()}
      <section class="variantChoices" style="padding:18px 0 8px">
        <div class="episode" style="border-top:1px solid #242424">
          <span><strong>Results found</strong><small style="display:block;color:#888;margin-top:3px">Filters apply to the original matching files.</small></span>
          <strong id="globalAfStatus" style="white-space:nowrap">${esc(status||`${items.length} result${items.length===1?"":"s"} found`)}</strong>
        </div>

        <div class="choiceRow">
          <small>Language</small>
          ${languages.map(v=>button("language",v,v,selected("language",v))).join("")}
        </div>

        <div class="choiceRow">
          <small>Quality</small>
          ${qualities.map(v=>button("quality",v,v,selected("quality",v))).join("")}
        </div>

        ${isSeries?`<div class="choiceRow">
          <small>Season</small>
          ${seasons.map(v=>button("season",String(v),`Season ${v}`,selected("season",v))).join("")}
        </div>`:""}

        ${isSeries?`<div class="choiceRow">
          <small>Episode</small>
          ${episodes.map(v=>button("episode",String(v),`Episode ${String(v).padStart(2,"0")}`,selected("episode",v))).join("")}
        </div>`:""}

        <div id="globalAfResult"></div>
      </section>`;

    mount.querySelectorAll(".afChoice").forEach(btn=>btn.onclick=async()=>{
      const kind=btn.dataset.afKind;
      const value=btn.dataset.afValue;
      if(kind==="season") state.season=Number(value);
      if(kind==="episode") state.episode=Number(value);
      if(kind==="language") state.language=value;
      if(kind==="quality") state.quality=value;
      draw("Checking matching files…");
      await check();
    });
  }

  async function check(){
    const status=mount.querySelector("#globalAfStatus");
    const result=mount.querySelector("#globalAfResult");
    const params={q:query};
    if(state.season!=null)params.season=state.season;
    if(state.episode!=null)params.episode=state.episode;
    if(state.language)params.language=state.language;
    if(state.quality)params.quality=state.quality;

    try{
      const data=await API.get("/api/filter?"+new URLSearchParams(params));
      if(status)status.textContent=`${data.count} result${data.count===1?"":"s"} found`;

      if(!result)return;

      if(data.count===1 && data.file?.file_id){
        const file=data.file;
        const label=esc(file.file_name||"Selected file");
        result.innerHTML=`<div class="episode" style="margin-top:8px;gap:12px;flex-wrap:wrap">
          <button type="button" class="episodePlay" id="globalAfFile" style="flex:1;min-width:220px;text-align:left">${label}</button>
          <button type="button" class="primary" id="globalAfPlay">▶ Play</button>
        </div>`;

        const play=()=>Player.open(
          file.file_id,
          file.file_name,
          [file],
          null,
          {title:file.title,type:file.type,year:file.year,season:file.season,episode:file.episode}
        );
        result.querySelector("#globalAfFile").onclick=play;
        result.querySelector("#globalAfPlay").onclick=play;
      }else if(data.count>1){
        result.innerHTML=`<div class="state" style="padding:14px 2px">${data.count} files still match. Choose another language, quality, season or episode filter.</div>`;
      }else{
        result.innerHTML=`<div class="state empty" style="padding:14px 2px">NO FILES WERE FOUND</div>`;
      }
    }catch(e){
      if(status)status.textContent="0 results found";
      if(result)result.innerHTML=`<div class="state empty" style="padding:14px 2px">NO FILES WERE FOUND</div>`;
    }
  }

  draw();
}

async function showDetails(id,preferredSeason=null,preferredEpisode=null){
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
        if(expanded?.id===id) title=expanded;
      }catch(_){
        // Keep the already returned search/home object usable if the targeted
        // expansion is temporarily unavailable.
      }
    }else{
      title=await API.get(
        "/api/title/"+encodeURIComponent(id)
      );
    }

    if(!title){
      throw new Error("Title not found");
    }

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
          ${title.rating
            ?`<br>★ ${Number(title.rating).toFixed(1)}`
            :""
          }
        </p>
      </div>
    </div>`;

    if(title.type==="movie"){
      html+=`<div id="autoFilterPanel"></div>`;
      $("#detailBody").innerHTML=html;
      await renderAutoFilter(title,$("#autoFilterPanel"));
      return;
    }

    html+=`<div id="autoFilterPanel"></div>`;
    $("#detailBody").innerHTML=html;
    await renderAutoFilter(title,$("#autoFilterPanel"),preferredSeason,preferredEpisode);
    return;

    if(title.type==="movie"){
      const variants=Array.isArray(title.variants)?title.variants:[];

      html+=`<section class="variantChoices">
        <div class="episode"><span>Choose language, quality and source from Player Settings.</span><button class="primary" id="playMovie">▶ Play</button></div>
      </section>`;

      $("#detailBody").innerHTML=html;
      $("#playMovie").onclick=()=>Player.open(title.id,title.title,variants,null,{title:title.title,type:title.type,year:title.year??null});
      return;
    }

    const allSeasons=
      Array.isArray(title.seasons)
        ?title.seasons
        :[];
    const seasons=preferredSeason!=null
      ?allSeasons.filter(item=>item.season===preferredSeason)
      :allSeasons;

    if(!seasons.length){

      html+=
        '<div class="state empty">No episodes found for this series.</div>';

    }else{

      html+=seasons.map(
        (season,seasonIndex)=>`<section>
          <h2>Season ${season.season}</h2>

          ${(season.episodes||[]).map(
            (episode,episodeIndex)=>{
              const highlighted=preferredEpisode!=null && episode.episode===preferredEpisode;

              const nextEpisode=
                season.episodes?.[episodeIndex+1];

              const nextSeason=
                seasons[seasonIndex+1]?.episodes?.[0];

              const next=nextEpisode
                ?{
                    title:title.title,
                    season:season.season,
                    episode:nextEpisode.episode,
                    variants:nextEpisode.variants||[]
                  }
                :nextSeason
                  ?{
                      title:title.title,
                      season:seasons[seasonIndex+1].season,
                      episode:nextSeason.episode,
                      variants:nextSeason.variants||[]
                    }
                  :null;

              return `<div class="episode ${highlighted?"search-highlight":""}">
                <span>Episode ${episode.episode}${highlighted?" ← selected":""}</span>

                <button
                  class="primary episodePlay"
                  data-season="${season.season}"
                  data-episode="${episode.episode}">
                  ▶
                </button>
              </div>`;
            }
          ).join("")}

        </section>`
      ).join("");
    }

    $("#detailBody").innerHTML=html;

    $("#detailBody")
      .querySelectorAll(".episodePlay")
      .forEach(button=>{

        const seasonNumber=
          Number(button.dataset.season);

        const episodeNumber=
          Number(button.dataset.episode);

        const season=
          seasons.find(
            item=>item.season===seasonNumber
          );

        const episode=
          season?.episodes?.find(
            item=>item.episode===episodeNumber
          );

        const seasonIndex=
          seasons.findIndex(
            item=>item.season===seasonNumber
          );

        const episodeIndex=
          season?.episodes?.findIndex(
            item=>item.episode===episodeNumber
          )??-1;

        const nextEpisode=
          season?.episodes?.[episodeIndex+1];

        const nextSeason=
          seasons[seasonIndex+1]?.episodes?.[0];

        const next=nextEpisode
          ?{
              title:title.title,
              year:title.year??null,
              season:seasonNumber,
              episode:nextEpisode.episode,
              variants:nextEpisode.variants||[]
            }
          :nextSeason
            ?{
                title:title.title,
                year:title.year??null,
                season:seasons[seasonIndex+1].season,
                episode:nextSeason.episode,
                variants:nextSeason.variants||[]
              }
            :null;

        button.onclick=()=>Player.open(
          title.id,
          `${title.title} • S${String(seasonNumber).padStart(2,"0")} E${String(episodeNumber).padStart(2,"0")}`,
          episode?.variants||[],
          next,
          {title:title.title,type:"series",year:title.year??null,season:seasonNumber,episode:episodeNumber}
        );
      });

  }catch(error){
    alert(
      error.message||
      "Unable to open title"
    );
  }
}

let searchRequestController=null;
let searchSequence=0;

async function doSearch(){
  const query=$("#query").value.trim();
  const sequence=++searchSequence;

  if(!query){
    $("#results").innerHTML='<div class="state">Type a movie or series name.</div>';
    return;
  }

  state($("#results"),"Searching…");

  try{
    if(searchRequestController)searchRequestController.abort();
    searchRequestController=new AbortController();

    const data=await API.get(
      "/api/search?q="+encodeURIComponent(query),
      {signal:searchRequestController.signal}
    );

    if(sequence!==searchSequence)return;
    if(!Array.isArray(data.items))throw new Error(data.error||"Invalid search response");

    searchTitles=data.items;
    if(!data.items.length){
      state($("#results"),`No results found for “${query}”.`,"empty");
      return;
    }

    // IMPORTANT: do not render every raw Mongo file as a card. The raw files
    // remain the filter source, while the website keeps the original Netflix
    // presentation: one representative poster/description + result count +
    // cumulative Auto Filter buttons.
    const panel=document.createElement("div");
    await renderGlobalAutoFilter(query,data.items,panel);
    $("#results").innerHTML="";
    $("#results").appendChild(panel);
  }catch(error){
    if(error?.name==="AbortError" || sequence!==searchSequence)return;
    state($("#results"),`Search failed: ${error.message||"backend error"}`,"error");
  }
}

function showHome(){
  $("#search")?.classList.add("hidden");
  $("#details")?.classList.add("hidden");
  if(!$("#player")?.classList.contains("hidden")) Player.close();
  window.scrollTo({top:0,behavior:"smooth"});
}

$("#bottomHome").onclick=showHome;

$("#searchBtn").onclick=()=>{
  $("#search").classList.remove("hidden");
  $("#query").focus();
  doSearch();
};

$("#bottomSearch").onclick=()=>{
  $("#search").classList.remove("hidden");
  $("#query").focus();
  doSearch();
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

$("#query").oninput=()=>{
  clearTimeout(window.searchTimer);

  window.searchTimer=
    setTimeout(
      doSearch,
      650
    );
};

$("#query").onkeydown=event=>{
  if(event.key==="Enter"){
    clearTimeout(window.searchTimer);
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
