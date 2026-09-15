const Player={
  variants:[], allVariants:[], current:null, titleId:null, titleName:null, titleType:null, titleYear:null,
  variant:null, audio:null, audioTrack:null, subtitle:null, subtitleTrack:null, quality:null, source:null,
  season:null, episode:null, menuSection:null,
  tracks:{audio_tracks:[],subtitle_tracks:[]}, subtitleUrl:null, switchBusy:false,
  selectedSettings:{},

  async open(titleId,label,variants,next,context={}){
    this.titleId=String(titleId);
    this.titleName=String(context.title||label||"").replace(/\s+•\s+S\d+\s+E\d+$/i,"").trim();
    this.titleType=context.type||"movie";
    this.titleYear=context.year??null;
    this.season=context.season??null;
    this.episode=context.episode??null;
    // Keep only the files already supplied for the item being opened.
    // Other variants are intentionally NOT loaded here. They are resolved
    // from MongoDB only after the user clicks a setting choice.
    this.variants=Array.isArray(variants)?variants.filter(v=>v?.file_id):[];
    this.allVariants=[...this.variants];
    this.current={label,next};
    this.variant=null; this.audio=null; this.audioTrack=null; this.subtitle=null; this.subtitleTrack=null; this.quality=null; this.source=null;
    // Only settings explicitly chosen by the user are search constraints.
    // The currently playing file supplies display values, but does not lock
    // future searches to those values.
    this.selectedSettings={};
    this.tracks={audio_tracks:[],subtitle_tracks:[]}; this.menuSection=null;

    const player=document.getElementById("player"), menu=document.getElementById("menu");
    player.classList.remove("hidden"); menu.classList.add("hidden");
    document.getElementById("nowPlaying").textContent=label||"Now Playing";
    const captionBox=document.getElementById("fileCaption");
    if(captionBox){captionBox.classList.add("hidden");captionBox.textContent="";}
    const captionToggle=document.getElementById("captionToggle");
    if(captionToggle){captionToggle.onclick=()=>captionBox?.classList.toggle("hidden");}
    this.render();

    const first=this.bestInitialVariant();
    if(!first){this.showError("No playable file version is available.");return;}
    try{
      await this.select(first,0,false);
      // The settings must start from the actual file that is playing.
      // These values are then sent back to MongoDB with every later click,
      // so choosing Tamil/720p/WEB-DL means "same title + those settings".
      this.setVariantSettings(first);
      await this.loadTracks(first);
      this.render();
    }catch(e){this.showError(e.message||"Unable to start playback.");return}

    const nextButton=document.getElementById("next");
    if(next?.variants?.length){
      nextButton.classList.remove("hidden");
      nextButton.onclick=()=>this.open(
        titleId,
        `${next.title} • S${String(next.season).padStart(2,"0")} E${String(next.episode).padStart(2,"0")}`,
        next.variants,next.next||null,
        {title:next.title,type:"series",year:next.year??this.titleYear,season:next.season,episode:next.episode}
      );
    }else nextButton.classList.add("hidden");
  },

  bestInitialVariant(){
    return [...this.variants].sort((a,b)=>this.rankVariant(b)-this.rankVariant(a))[0]||null;
  },
  rankVariant(v){
    const q=Number(String(v?.quality||"").match(/\d+/)?.[0]||0);
    const codec=String(v?.codec||"").toLowerCase();
    return q*10+(codec.includes("h.264")||codec.includes("avc")?2:0);
  },
  variantAudio(v){return Array.isArray(v?.audio_languages)?v.audio_languages.filter(Boolean):[]},
  variantSubs(v){return Array.isArray(v?.subtitle_languages)?v.subtitle_languages.filter(Boolean):[]},
  setVariantSettings(v){
    if(!v)return;
    this.variant=v;
    this.quality=v.quality&&String(v.quality).toLowerCase()!=="auto"?String(v.quality):null;
    this.source=v.source&&String(v.source).toLowerCase()!=="unknown"?String(v.source):null;
    this.audio=this.variantAudio(v)[0]||null;
    if(v.season!=null)this.season=Number(v.season);
    if(v.episode!=null)this.episode=Number(v.episode);
  },

  // These are UI choices, not database results. The site does not query the
  // database to build these lists. A click on one of them triggers /api/resolve.
  languageChoices(){return [
    "Hindi","Tamil","English","Telugu","Malayalam","Kannada","Bengali","Bangla",
    "Marathi","Punjabi","Gujarati","Bhojpuri","Korean","Spanish","French",
    "German","Chinese","Japanese","Urdu"
  ]},
  qualityChoices(){return ["360p","480p","720p","1080p","1440p","2160p"]},
  sourceChoices(){return [
    "WEB-DL","WEBRip","BluRay","BRRip","BDRip","HDRip","HDTV","DVDRip",
    "HDTC","HDTS","WEB-CAM","CAMRip","HDCAM","CAM","PreDB","Pre-DVD","WEB","REMUX"
  ]},
  seasonChoices(){return Array.from({length:20},(_,i)=>i+1)},
  episodeChoices(){return Array.from({length:50},(_,i)=>i+1)},
  allAudioLanguages(){return this.languageChoices()},
  allSubtitles(){return [...new Set((this.tracks.subtitle_tracks||[]).map(t=>t.language).filter(x=>x&&x!=="Unknown"))]},
  allQualities(){return this.qualityChoices()},
  allSources(){return this.sourceChoices()},
  allSeasons(){return this.seasonChoices()},
  episodesForSeason(_season){return this.episodeChoices()},

  render(){
    const audio=this.allAudioLanguages();
    const subs=this.allSubtitles();
    const embeddedAudio=(this.tracks.audio_tracks||[]).map(t=>t.language).filter(x=>x&&x!=="Unknown");
    const embeddedSubs=(this.tracks.subtitle_tracks||[]).map(t=>t.language).filter(x=>x&&x!=="Unknown");
    const qualities=this.allQualities(), sources=this.allSources(), seasons=this.allSeasons();
    const section=(key,label,values,current,formatter=x=>x)=>!values.length?"":`<button class="settingRow" data-setting-section="${key}"><span><b>${label}</b><small>${playerEscape(current??"Not selected")}</small></span><span>›</span></button><div class="settingSubmenu ${this.menuSection===key?"":"hidden"}">${values.map(v=>`<button class="settingOption ${String(v)===String(current)?"active":""}" data-player-${key}="${playerEscape(v)}"><span>${playerEscape(formatter(v))}</span>${String(v)===String(current)?"✓":""}</button>`).join("")}</div>`;

    let html=`<div class="settingsHead"><h3>Player settings</h3><button id="closeSettings" aria-label="Close settings">×</button></div>`;
    html+=section("audio","Language / Audio",audio,this.audio);
    html+=section("quality","Quality",qualities,this.quality);
    html+=section("source","Source / Release",sources,this.source);
    if(this.titleType==="series") html+=section("season","Season",seasons,this.season,v=>`S${String(v).padStart(2,"0")}`);
    if(this.titleType==="series") html+=section("episode","Episode",this.episodesForSeason(this.season),this.episode,v=>`E${String(v).padStart(2,"0")}`);
    if(embeddedAudio.length) html+=section("audioTrack","Audio Track",embeddedAudio,this.audioTrack);
    if(embeddedSubs.length) html+=section("subtitleTrack","Subtitle Track",embeddedSubs,this.subtitleTrack);
    if(subs.length) html+=section("subtitle","Subtitle",subs,this.subtitle);
    html+=`<button class="settingRow" id="speedSetting"><span><b>Playback speed</b><small id="speedValue">${playerEscape(document.getElementById("video").playbackRate||1)}×</small></span><span>›</span></button>`;
    html+=`<button class="settingRow" id="pipSetting"><span><b>Picture-in-picture</b><small>Where supported by your browser</small></span><span>▣</span></button>`;

    document.getElementById("menu").innerHTML=html;
    document.querySelectorAll("[data-setting-section]").forEach(b=>b.onclick=()=>{
      this.menuSection=this.menuSection===b.dataset.settingSection?null:b.dataset.settingSection; this.render();
    });
    document.getElementById("closeSettings")?.addEventListener("click",()=>{
      this.menuSection=null; document.getElementById("menu").classList.add("hidden");
    });
    document.getElementById("speedSetting")?.addEventListener("click",()=>{
      const video=document.getElementById("video");
      const speeds=[0.5,0.75,1,1.25,1.5,1.75,2];
      const i=Math.max(0,speeds.indexOf(video.playbackRate));
      video.playbackRate=speeds[(i+1)%speeds.length];
      this.render();
    });
    document.getElementById("pipSetting")?.addEventListener("click",async()=>{
      try{
        if(document.pictureInPictureElement) await document.exitPictureInPicture();
        else if(document.pictureInPictureEnabled) await document.getElementById("video").requestPictureInPicture();
        else this.showError("Picture-in-picture is not supported on this browser.");
      }catch(_){this.showError("Picture-in-picture is not available here.")}
    });
    document.querySelectorAll("[data-player-audio]").forEach(b=>b.onclick=()=>this.choose("audio",b.dataset.playerAudio));
    document.querySelectorAll("[data-player-quality]").forEach(b=>b.onclick=()=>this.choose("quality",b.dataset.playerQuality));
    document.querySelectorAll("[data-player-source]").forEach(b=>b.onclick=()=>this.choose("source",b.dataset.playerSource));
    document.querySelectorAll("[data-player-season]").forEach(b=>b.onclick=()=>this.choose("season",Number(b.dataset.playerSeason)));
    document.querySelectorAll("[data-player-episode]").forEach(b=>b.onclick=()=>this.choose("episode",Number(b.dataset.playerEpisode)));
    document.querySelectorAll("[data-player-audioTrack]").forEach(b=>b.onclick=()=>this.choose("audioTrack",b.dataset.playerAudioTrack));
    document.querySelectorAll("[data-player-subtitleTrack]").forEach(b=>b.onclick=()=>this.choose("subtitleTrack",b.dataset.playerSubtitleTrack));
    document.querySelectorAll("[data-player-subtitle]").forEach(b=>b.onclick=()=>this.choose("subtitle",b.dataset.playerSubtitle));
  },

  async loadTracks(variant){
    try{
      const token=await API.get("/api/stream-token/"+encodeURIComponent(variant.file_id));
      if(!token?.token)return;
      const data=await API.get("/api/tracks/"+encodeURIComponent(variant.file_id)+"?token="+encodeURIComponent(token.token));
      if(data?.available)this.tracks=data;
    }catch(_){}
  },

  findVariantFor(key,value){
    const current=this.variant;
    const pool=(key==="season"||key==="episode")?this.allVariants:this.variants;
    const candidates=pool.filter(v=>{
      if(key==="quality")return String(v.quality)===String(value);
      if(key==="source")return String(v.source)===String(value);
      if(key==="audio" || key==="subtitle")return (key==="audio"?this.variantAudio(v):this.variantSubs(v)).includes(value);
      if(key==="season")return v.season===Number(value);
      if(key==="episode")return v.season===this.season && v.episode===Number(value);
      return false;
    });
    return candidates.sort((a,b)=>{
      const sameA=current&&a.file_id===current.file_id?1:0, sameB=current&&b.file_id===current.file_id?1:0;
      return (sameB-sameA)||(this.rankVariant(b)-this.rankVariant(a));
    })[0]||null;
  },

  async resolveExact(extra={}){
    // Player Settings behaves like the main search engine: only values the
    // user has explicitly chosen are sent as constraints. Current playback
    // metadata is only a display/default value and is never used to silently
    // lock the next search to that variant.
    const selected=this.selectedSettings||{};
    const params=new URLSearchParams({
      title:this.titleName,
      type:this.titleType,
      ...(this.titleYear!=null?{year:String(this.titleYear)}:{}),
      ...(selected.season!=null?{season:String(selected.season)}:{}),
      ...(selected.episode!=null?{episode:String(selected.episode)}:{}),
      ...(selected.quality?{quality:String(selected.quality)}:{}),
      ...(selected.source?{source:String(selected.source)}:{}),
      ...(selected.audio?{audio:String(selected.audio)}:{}),
      ...(selected.subtitle?{subtitle:String(selected.subtitle)}:{}),
      ...Object.fromEntries(Object.entries(extra).filter(([,v])=>v!==null&&v!==undefined&&v!==""))
    });
    const data=await API.get("/api/resolve?"+params.toString());
    if(!data?.file?.file_id)throw new Error("The exact requested file is not available.");
    return data.file;
  },

  async choose(key,value){
    if(this.switchBusy)return;
    const video=document.getElementById("video");
    const position=Number.isFinite(video.currentTime)?video.currentTime:0;
    const playing=!video.paused&&!video.ended;
    const oldState={audio:this.audio,audioTrack:this.audioTrack,subtitleTrack:this.subtitleTrack,quality:this.quality,source:this.source,season:this.season,episode:this.episode,subtitle:this.subtitle,variant:this.variant,selectedSettings:{...this.selectedSettings}};
    this.switchBusy=true; this.menuSection=null;
    try{
      if(key==="audioTrack"){
        await this.selectEmbeddedAudio(value,position,playing);
      }else if(key==="subtitleTrack"){
        await this.selectEmbeddedSubtitle(value);
      }else if(key==="subtitle"){
        const embedded=(this.tracks.subtitle_tracks||[]).find(t=>t.language===value);
        if(embedded) await this.selectEmbeddedSubtitle(value);
        else { this.subtitle=value; this.selectedSettings.subtitle=String(value); await this.switchResolved(position,playing); }
      }else{
        if(key==="audio"){this.audio=String(value);this.selectedSettings.audio=String(value);}
        if(key==="quality"){this.quality=String(value);this.selectedSettings.quality=String(value);}
        if(key==="source"){this.source=String(value);this.selectedSettings.source=String(value);}
        if(key==="season") {
          this.season=Number(value);
          this.selectedSettings.season=Number(value);
          // Changing season starts a new season context; do not carry an
          // episode from the previously playing season into the new search.
          this.episode=null;
          delete this.selectedSettings.episode;
        }
        if(key==="episode"){this.episode=Number(value);this.selectedSettings.episode=Number(value);}
        await this.switchResolved(position,playing);
      }
      document.getElementById("menu").classList.add("hidden");
    }catch(e){
      Object.assign(this,oldState);
      this.selectedSettings={...oldState.selectedSettings};
      this.showError(e.message||"Unable to switch this setting.");
      this.render();
    }finally{this.switchBusy=false}
  },

  async switchResolved(position,playing){
    const file=await this.resolveExact();
    // The resolver returns one real database file only after the click.
    // Keep it as the current playback target; do not build a variant catalog.
    this.variants=[file];
    await this.select(file,position,playing);
    this.setVariantSettings(file);
    await this.loadTracks(file);
    this.render();
  },

  async selectEmbeddedAudio(value,position,playing){
    const embedded=(this.tracks.audio_tracks||[]).find(t=>t.language===value);
    if(!embedded||!this.variant)throw new Error("That audio track is not available in this file.");
    await this.switchEmbeddedAudio(embedded.track,position,playing);
    this.audioTrack=value; this.render();
  },

  async switchEmbeddedAudio(track,position,playing){
    const v=this.variant;
    const data=await API.get("/api/stream-token/"+encodeURIComponent(v.file_id));
    if(!data?.token)throw new Error("Server did not return a stream token.");
    const source="/api/stream-compatible/"+encodeURIComponent(v.file_id)+"?token="+encodeURIComponent(data.token)+"&audio_track="+encodeURIComponent(track)+"&start="+encodeURIComponent(position.toFixed(3));
    await this.loadSource(source,0,playing,v,true);
  },

  async selectEmbeddedSubtitle(value){
    const video=document.getElementById("video");
    this.removeSubtitleTrack();
    const embedded=(this.tracks.subtitle_tracks||[]).find(t=>t.language===value);
    if(!embedded||!this.variant)throw new Error("That subtitle track is not available in this file.");
    const data=await API.get("/api/stream-token/"+encodeURIComponent(this.variant.file_id));
    if(!data?.token)throw new Error("Server did not return a stream token.");
    const url="/api/subtitle/"+encodeURIComponent(this.variant.file_id)+"?token="+encodeURIComponent(data.token)+"&subtitle_track="+encodeURIComponent(embedded.track);
    const response=await fetch(url);
    if(!response.ok)throw new Error("Unable to load this subtitle track.");
    const blob=await response.blob();
    this.subtitleUrl=URL.createObjectURL(blob);
    const track=document.createElement("track");
    track.kind="subtitles"; track.label=value; track.srclang=""; track.src=this.subtitleUrl;
    track.default=true; track.dataset.streamboxSubtitle="1";
    video.appendChild(track); track.track.mode="showing"; this.subtitleTrack=value; this.render();
  },

  async select(v,position=0,playing=false){
    if(!v?.file_id)throw new Error("This file has no valid media ID.");
    const data=await API.get("/api/stream-token/"+encodeURIComponent(v.file_id));
    if(!data?.token)throw new Error("Server did not return a stream token.");
    const compatible=this.needsCompatibility(v);
    const endpoint=compatible?"/api/stream-compatible/":"/api/stream/";
    const source=endpoint+encodeURIComponent(v.file_id)+"?token="+encodeURIComponent(data.token)+(compatible&&position>0?"&start="+encodeURIComponent(position.toFixed(3)):"");
    await this.loadSource(source,position,playing,v,compatible&&position>0);
  },

  needsCompatibility(v){
    const name=String(v?.file_name||"").toLowerCase(), mime=String(v?.mime_type||"").toLowerCase(), codec=String(v?.codec||"").toLowerCase();
    return !(/\.(mp4|m4v|webm)$/.test(name)||mime.includes("mp4")||mime.includes("webm"))||codec.includes("hevc")||codec.includes("h.265")||codec.includes("x265");
  },

  async loadSource(source,position,playing,v,absoluteStart){
    const video=document.getElementById("video");
    this.removeSubtitleTrack();
    video.pause(); video.removeAttribute("src"); video.load();
    const previous=this.variant;
    this.variant=v;
    const captionBox=document.getElementById("fileCaption");
    if(captionBox){
      captionBox.textContent=String(v.caption||"").trim() || "No caption was stored for this file.";
      captionBox.classList.add("hidden");
    }
    this.quality=v.quality||this.quality||null;
    this.source=v.source||this.source||null;
    if(!this.audio)this.audio=this.variantAudio(v)[0]||null;
    this.render(); video.src=source; video.load();
    try{
      await new Promise((resolve,reject)=>{
        let done=false;
        const ok=()=>{if(!done){done=true;resolve()}};
        const fail=()=>{if(!done){done=true;reject(new Error("Unable to load this media stream."))}};
        video.addEventListener("loadedmetadata",ok,{once:true});
        video.addEventListener("error",fail,{once:true});
        setTimeout(ok,12000);
      });
      if(!absoluteStart&&position>0&&Number.isFinite(video.duration)){
        const target=Math.min(position,Math.max(0,video.duration-0.25));
        if(target>0){try{video.currentTime=target}catch(_){} await this.restorePosition(target)}
      }
      saveLocal(this.titleId,v.file_id,video.currentTime||position);
      this.clearError(); if(playing)await video.play().catch(()=>{});
    }catch(e){this.variant=previous;throw e}
  },

  async restorePosition(target){
    const video=document.getElementById("video");
    for(let i=0;i<8;i++){
      if(Math.abs((video.currentTime||0)-target)<=1)return;
      try{if(video.seekable?.length)video.currentTime=Math.min(target,video.seekable.end(video.seekable.length-1));else video.currentTime=target}catch(_){}
      await new Promise(r=>setTimeout(r,180));
    }
  },

  removeSubtitleTrack(){
    document.querySelectorAll('#video track[data-streambox-subtitle="1"]').forEach(t=>t.remove());
    if(this.subtitleUrl){URL.revokeObjectURL(this.subtitleUrl);this.subtitleUrl=null}
  },
  download(){
    if(!this.variant?.file_id){this.showError("No playable file is selected.");return}
    API.get("/api/stream-token/"+encodeURIComponent(this.variant.file_id)).then(d=>{
      if(!d.token)throw new Error("Server did not return a download token.");
      location.href="/api/download/"+encodeURIComponent(this.variant.file_id)+"?token="+encodeURIComponent(d.token);
    }).catch(e=>this.showError(e.message||"Unable to start download."));
  },
  showError(message){
    let el=document.getElementById("playerError");
    if(!el){el=document.createElement("div");el.id="playerError";el.style.cssText="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:3;background:#111;padding:16px 20px;border:1px solid #333;border-radius:10px;max-width:min(90vw,520px);text-align:center;color:#fff";document.getElementById("player").appendChild(el)}
    el.textContent=message;
    clearTimeout(this.errorTimer);
    this.errorTimer=setTimeout(()=>el.remove(),4000);
  },
  clearError(){document.getElementById("playerError")?.remove()},
  close(){
    const video=document.getElementById("video");
    if(this.titleId)saveLocal(this.titleId,this.variant?.file_id,video.currentTime||0);
    this.removeSubtitleTrack(); video.pause(); video.removeAttribute("src"); video.load();
    document.getElementById("player").classList.add("hidden"); document.getElementById("menu").classList.add("hidden");
    this.clearError(); this.menuSection=null;
  }
};
function playerEscape(value){return String(value??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function saveLocal(id,file,position){if(!id)return;let data={};try{data=JSON.parse(localStorage.getItem("stream_progress")||"{}")||{}}catch(_){data={}}data[id]={file_id:file||null,position:Number(position)||0,updated:Date.now()};localStorage.setItem("stream_progress",JSON.stringify(data))}

document.addEventListener("keydown",event=>{
  const player=document.getElementById("player");
  if(!player||player.classList.contains("hidden"))return;
  const video=document.getElementById("video");
  if(event.target?.matches?.("input,textarea,select"))return;
  if(event.key===" "){event.preventDefault();video.paused?video.play().catch(()=>{}):video.pause();}
  else if(event.key==="ArrowLeft"){event.preventDefault();video.currentTime=Math.max(0,(video.currentTime||0)-10);}
  else if(event.key==="ArrowRight"){event.preventDefault();video.currentTime=Math.min(video.duration||Infinity,(video.currentTime||0)+10);}
  else if(event.key==="m"){video.muted=!video.muted;}
  else if(event.key==="f"){if(document.fullscreenElement)document.exitFullscreen?.();else document.getElementById("video").requestFullscreen?.();}
});
