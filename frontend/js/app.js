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
  return `<article class="card" data-id="${escapeHtml(title.id)}" data-season="${escapeHtml(season)}" data-episode="${escapeHtml(episode)}">
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
    $("#results").innerHTML=
      '<div class="state">Type a movie or series name.</div>';

    return;
  }

  state(
    $("#results"),
    "Searching…"
  );

  try{

    if(searchRequestController){
      searchRequestController.abort();
    }
    searchRequestController=new AbortController();

    const data=await API.get(
      "/api/search?q="+
      encodeURIComponent(query),
      {signal:searchRequestController.signal}
    );

    if(sequence!==searchSequence){
      return;
    }

    if(!Array.isArray(data.items)){
      throw new Error(
        data.error||
        "Invalid search response"
      );
    }

    searchTitles=data.items;

    if(!data.items.length){

      state(
        $("#results"),
        `No results found for “${query}”.`,
        "empty"
      );

      return;
    }

    $("#results").innerHTML=
      data.items.map(card).join("");

    bind();

  }catch(error){

    if(error?.name==="AbortError" || sequence!==searchSequence){
      return;
    }

    state(
      $("#results"),
      `Search failed: ${error.message||"backend error"}`,
      "error"
    );
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
