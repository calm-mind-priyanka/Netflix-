let titles=[];
const $=selector=>document.querySelector(selector);

const esc=value=>String(value??"").replace(/[&<>"']/g,match=>({
  "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
}[match]));

function poster(title){
  return title.poster
    ? `style="background-image:url('${esc(title.poster)}')"`
    : "";
}

function card(title){
  return `<article class="card" data-id="${esc(title.id)}">
    <div class="poster" ${poster(title)}>
      ${title.poster?"":"🎬"}<span class="cardShade"></span>
    </div>
    <strong>${esc(title.title)}</strong>
    <small>${title.type==="series"?"Series":"Movie"}${title.year?" • "+title.year:""}</small>
  </article>`;
}

function state(element,message,kind="loading"){
  if(element) element.innerHTML=`<div class="state ${kind}">${esc(message)}</div>`;
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
    $("#movies").innerHTML=titles.filter(item=>item.type==="movie").slice(0,24).map(card).join("")
      ||'<div class="state empty">No movies found.</div>';
    $("#series").innerHTML=titles.filter(item=>item.type==="series").slice(0,24).map(card).join("")
      ||'<div class="state empty">No series found.</div>';

    bind();

    if(titles[0]){
      const hero=titles[0];
      $("#hero").style.backgroundImage=hero.backdrop
        ? `linear-gradient(90deg,rgba(5,5,5,.98),rgba(5,5,5,.55),rgba(5,5,5,.15)),url('${esc(hero.backdrop)}')`
        : "linear-gradient(90deg,#080808,#171717)";

      $("#hero").innerHTML=`<div class="heroText">
        <span class="eyebrow">STREAMBOX ORIGINAL CATALOG</span>
        <h1>${esc(hero.title)}</h1>
        <p>${esc(hero.description||"Discover and stream your next movie or series.")}</p>
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
  }catch(error){
    const message=error.message||"Unable to load the catalog.";
    ["latest","movies","series"].forEach(id=>state($("#"+id),`Catalog error: ${message}`,"error"));
    $("#hero").style.backgroundImage="linear-gradient(90deg,#080808,#171717)";
    $("#hero").innerHTML=`<div class="heroText">
      <span class="eyebrow">STREAMBOX</span>
      <h1>Catalog unavailable</h1>
      <p>The website is running, but the movie database could not be read. Check the backend/API status.</p>
      <button class="primary" id="retryCatalog">Retry</button>
    </div>`;
    $("#retryCatalog").onclick=load;
  }
}

function bind(){
  document.querySelectorAll(".card").forEach(element=>{
    element.onclick=()=>showDetails(element.dataset.id);
  });
}

function renderContinue(){
  let progress={};
  try{progress=JSON.parse(localStorage.getItem("stream_progress")||"{}")||{}}
  catch(_){progress={}}

  const ids=Object.keys(progress)
    .sort((a,b)=>(progress[b].updated||0)-(progress[a].updated||0))
    .map(id=>titles.find(title=>title.id===id))
    .filter(Boolean);

  if(!ids.length)return;
  $("#continue").classList.remove("hidden");
  $("#continueRow").innerHTML=ids.slice(0,12).map(card).join("");
  bind();
}

async function showDetails(id){
  try{
    const title=titles.find(item=>item.id===id)||await API.get("/api/title/"+encodeURIComponent(id));
    if(!title)throw new Error("Title not found");

    $("#details").classList.remove("hidden");

    let html=`<div class="detail" ${poster(title)}>
      <div>
        <span class="eyebrow">${title.type==="series"?"SERIES":"MOVIE"}</span>
        <h1>${esc(title.title)}</h1>
        <p>${esc(title.description||"")}${title.rating?`<br>★ ${Number(title.rating).toFixed(1)}`:""}</p>
      </div>
    </div>`;

    if(title.type==="movie"){
      html+=`<div class="episode">
        <span>Available versions</span>
        <button class="primary" id="playMovie">▶ Play</button>
      </div>`;
      $("#detailBody").innerHTML=html;
      $("#playMovie").onclick=()=>Player.open(
        title.id,title.title,title.variants||[],null
      );
      return;
    }

    const seasons=Array.isArray(title.seasons)?title.seasons:[];
    if(!seasons.length){
      html+='<div class="state empty">No episodes found for this series.</div>';
    }else{
      html+=seasons.map((season,seasonIndex)=>`<section>
        <h2>Season ${season.season}</h2>
        ${(season.episodes||[]).map((episode,episodeIndex)=>{
          const nextEpisode=season.episodes?.[episodeIndex+1];
          const nextSeason=seasons[seasonIndex+1]?.episodes?.[0];
          const next=nextEpisode
            ? {title:title.title,season:season.season,episode:nextEpisode.episode,variants:nextEpisode.variants||[]}
            : nextSeason
              ? {title:title.title,season:seasons[seasonIndex+1].season,episode:nextSeason.episode,variants:nextSeason.variants||[]}
              : null;

          return `<div class="episode">
            <span>Episode ${episode.episode}</span>
            <button class="primary episodePlay"
              data-season="${season.season}"
              data-episode="${episode.episode}">▶</button>
          </div>`;
        }).join("")}
      </section>`).join("");
    }

    $("#detailBody").innerHTML=html;
    $("#detailBody").querySelectorAll(".episodePlay").forEach(button=>{
      const seasonNumber=Number(button.dataset.season);
      const episodeNumber=Number(button.dataset.episode);
      const season=seasons.find(item=>item.season===seasonNumber);
      const episode=season?.episodes?.find(item=>item.episode===episodeNumber);
      const seasonIndex=seasons.findIndex(item=>item.season===seasonNumber);
      const episodeIndex=season?.episodes?.findIndex(item=>item.episode===episodeNumber)??-1;
      const nextEpisode=season?.episodes?.[episodeIndex+1];
      const nextSeason=seasons[seasonIndex+1]?.episodes?.[0];
      const next=nextEpisode
        ? {title:title.title,season:seasonNumber,episode:nextEpisode.episode,variants:nextEpisode.variants||[]}
        : nextSeason
          ? {title:title.title,season:seasons[seasonIndex+1].season,episode:nextSeason.episode,variants:nextSeason.variants||[]}
          : null;

      button.onclick=()=>Player.open(
        title.id,
        `${title.title} • S${String(seasonNumber).padStart(2,"0")} E${String(episodeNumber).padStart(2,"0")}`,
        episode?.variants||[],
        next
      );
    });
  }catch(error){
    alert(error.message||"Unable to open title");
  }
}

async function doSearch(){
  const query=$("#query").value.trim();
  if(!query){
    $("#results").innerHTML='<div class="state">Type a movie or series name.</div>';
    return;
  }

  state($("#results"),"Searching…");

  try{
    const data=await API.get("/api/search?q="+encodeURIComponent(query));
    if(!Array.isArray(data.items)){
      throw new Error(data.error||"Invalid search response");
    }
    if(!data.items.length){
      state($("#results"),`No results found for “${query}”.`,"empty");
      return;
    }
    $("#results").innerHTML=data.items.map(card).join("");
    bind();
  }catch(error){
    state($("#results"),`Search failed: ${error.message||"backend error"}`,"error");
  }
}

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
$("#closeSearch").onclick=()=>$("#search").classList.add("hidden");
$("#backDetails").onclick=()=>$("#details").classList.add("hidden");
$("#settings").onclick=()=>$("#menu").classList.toggle("hidden");
$("#download").onclick=()=>Player.download();
$("#closePlayer").onclick=()=>Player.close();
$("#query").oninput=()=>{
  clearTimeout(window.searchTimer);
  window.searchTimer=setTimeout(doSearch,250);
};
$("#query").onkeydown=event=>{
  if(event.key==="Enter")doSearch();
};

load();
