const Player={
  variants:[], current:null, titleId:null, variant:null,
  audio:null, subtitle:null, quality:null, menuSection:null,
  tracks:{audio_tracks:[],subtitle_tracks:[]}, subtitleUrl:null, switchBusy:false,

  async open(titleId,label,variants,next){
    this.titleId=String(titleId); this.variants=Array.isArray(variants)?variants.filter(v=>v?.file_id):[];
    this.current={label,next}; this.variant=null; this.audio=null; this.subtitle=null; this.quality=null;
    this.tracks={audio_tracks:[],subtitle_tracks:[]}; this.menuSection=null;
    const player=document.getElementById("player"), menu=document.getElementById("menu");
    player.classList.remove("hidden"); menu.classList.add("hidden");
    document.getElementById("nowPlaying").textContent=label||"Now Playing";
    this.render();
    const first=this.bestInitialVariant();
    if(!first){this.showError("No playable file version is available.");return;}
    try{await this.select(first,0,false)}catch(e){this.showError(e.message||"Unable to start playback.");return}
    await this.loadTracks(first);
    this.render();
    const nextButton=document.getElementById("next");
    if(next?.variants?.length){nextButton.classList.remove("hidden");nextButton.onclick=()=>this.open(titleId,`${next.title} • S${String(next.season).padStart(2,"0")} E${String(next.episode).padStart(2,"0")}`,next.variants,next.next||null)}
    else nextButton.classList.add("hidden");
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

  allAudioLanguages(){return [...new Set(this.variants.flatMap(v=>this.variantAudio(v)))].sort((a,b)=>a.localeCompare(b))},
  allSubtitles(){return [...new Set(this.variants.flatMap(v=>this.variantSubs(v)))].sort((a,b)=>a.localeCompare(b))},
  allQualities(){return [...new Set(this.variants.map(v=>v.quality).filter(Boolean))].sort((a,b)=>(Number(String(a).match(/\d+/)?.[0]||9999)-Number(String(b).match(/\d+/)?.[0]||9999)))},

  render(){
    const audio=[...new Set([...this.allAudioLanguages(),...(this.tracks.audio_tracks||[]).map(t=>t.language).filter(x=>x&&x!=="Unknown")])];
    const subs=[...new Set([...this.allSubtitles(),...(this.tracks.subtitle_tracks||[]).map(t=>t.language).filter(x=>x&&x!=="Unknown")])];
    const qualities=this.allQualities();
    const section=(key,label,values,current)=>!values.length?"":`<button class="settingRow" data-setting-section="${key}"><span><b>${label}</b><small>${playerEscape(current||"Not selected")}</small></span><span>›</span></button><div class="settingSubmenu ${this.menuSection===key?"":"hidden"}">${values.map(v=>`<button class="settingOption ${v===current?"active":""}" data-player-${key}="${playerEscape(v)}"><span>${playerEscape(v)}</span>${v===current?"✓":""}</button>`).join("")}</div>`;
    document.getElementById("menu").innerHTML=`<div class="settingsHead"><h3>Settings</h3><button id="closeSettings" aria-label="Close settings">×</button></div>${section("quality","Quality",qualities,this.quality)}${section("audio","Audio",audio,this.audio)}${section("subtitle","Subtitles",subs,this.subtitle)}`;
    document.querySelectorAll("[data-setting-section]").forEach(b=>b.onclick=()=>{this.menuSection=this.menuSection===b.dataset.settingSection?null:b.dataset.settingSection;this.render()});
    document.getElementById("closeSettings")?.addEventListener("click",()=>{this.menuSection=null;document.getElementById("menu").classList.add("hidden")});
    document.querySelectorAll("[data-player-quality]").forEach(b=>b.onclick=()=>this.choose("quality",b.dataset.playerQuality));
    document.querySelectorAll("[data-player-audio]").forEach(b=>b.onclick=()=>this.choose("audio",b.dataset.playerAudio));
    document.querySelectorAll("[data-player-subtitle]").forEach(b=>b.onclick=()=>this.choose("subtitle",b.dataset.playerSubtitle));
  },

  async loadTracks(variant){
    try{
      const token=await API.get("/api/stream-token/"+encodeURIComponent(variant.file_id));
      if(!token?.token)return;
      const data=await API.get("/api/tracks/"+encodeURIComponent(variant.file_id)+"?token="+encodeURIComponent(token.token));
      if(data?.available)this.tracks=data;
    }catch(_){/* filename metadata remains available */}
  },

  findVariantFor(key,value){
    const current=this.variant;
    const candidates=this.variants.filter(v=>{
      if(key==="quality")return v.quality===value;
      if(key==="audio")return this.variantAudio(v).includes(value);
      if(key==="subtitle")return this.variantSubs(v).includes(value);
      return false;
    });
    return candidates.sort((a,b)=>{
      const sameA=(current&&a.file_id===current.file_id)?1:0, sameB=(current&&b.file_id===current.file_id)?1:0;
      return (sameB-sameA)||(this.rankVariant(b)-this.rankVariant(a));
    })[0]||null;
  },

  async choose(key,value){
    if(this.switchBusy)return;
    const video=document.getElementById("video");
    const position=Number.isFinite(video.currentTime)?video.currentTime:0;
    const playing=!video.paused&&!video.ended;
    this.switchBusy=true; this.menuSection=null;
    try{
      if(key==="subtitle"){
        await this.selectSubtitle(value,position);
      }else if(key==="audio"){
        await this.selectAudio(value,position,playing);
      }else{
        const candidate=this.findVariantFor("quality",value);
        if(!candidate)throw new Error("That quality is not available.");
        await this.switchVariant(candidate,position,playing);
      }
      document.getElementById("menu").classList.add("hidden");
    }catch(e){this.showError(e.message||"Unable to switch this setting.")}
    finally{this.switchBusy=false}
  },

  async selectAudio(value,position,playing){
    // Prefer an embedded track in the current file: this is the closest browser
    // equivalent to VLC's audio-track switch and avoids changing the movie file.
    const embedded=(this.tracks.audio_tracks||[]).find(t=>t.language===value);
    if(embedded && this.variant){
      await this.switchEmbeddedAudio(embedded.track,position,playing);
      this.audio=value; this.render(); return;
    }
    const candidate=this.findVariantFor("audio",value);
    if(!candidate)throw new Error("That audio language is not available.");
    await this.switchVariant(candidate,position,playing);
  },

  async switchEmbeddedAudio(track,position,playing){
    const v=this.variant;if(!v)throw new Error("No active media file.");
    const data=await API.get("/api/stream-token/"+encodeURIComponent(v.file_id));
    if(!data?.token)throw new Error("Server did not return a stream token.");
    const source="/api/stream-compatible/"+encodeURIComponent(v.file_id)+"?token="+encodeURIComponent(data.token)+"&audio_track="+encodeURIComponent(track)+"&start="+encodeURIComponent(position.toFixed(3));
    await this.loadSource(source,0,playing,v,true);
  },

  async selectSubtitle(value,position){
    const video=document.getElementById("video");
    this.removeSubtitleTrack();
    const embedded=(this.tracks.subtitle_tracks||[]).find(t=>t.language===value);
    if(embedded && this.variant){
      const data=await API.get("/api/stream-token/"+encodeURIComponent(this.variant.file_id));
      if(!data?.token)throw new Error("Server did not return a stream token.");
      const url="/api/subtitle/"+encodeURIComponent(this.variant.file_id)+"?token="+encodeURIComponent(data.token)+"&subtitle_track="+encodeURIComponent(embedded.track);
      const response=await fetch(url); if(!response.ok)throw new Error("Unable to load this subtitle track.");
      const blob=await response.blob(); this.subtitleUrl=URL.createObjectURL(blob);
      const track=document.createElement("track"); track.kind="subtitles"; track.label=value; track.srclang=""; track.src=this.subtitleUrl; track.default=true; track.dataset.streamboxSubtitle="1";
      video.appendChild(track); track.track.mode="showing"; this.subtitle=value; this.render(); return;
    }
    const candidate=this.findVariantFor("subtitle",value);
    if(!candidate)throw new Error("That subtitle language is not available.");
    const playing=!video.paused&&!video.ended;
    await this.switchVariant(candidate,position,playing); this.subtitle=value;
  },

  removeSubtitleTrack(){
    document.querySelectorAll('#video track[data-streambox-subtitle="1"]').forEach(t=>t.remove());
    if(this.subtitleUrl){URL.revokeObjectURL(this.subtitleUrl);this.subtitleUrl=null}
  },

  needsCompatibility(v){
    const name=String(v?.file_name||"").toLowerCase(), mime=String(v?.mime_type||"").toLowerCase(), codec=String(v?.codec||"").toLowerCase();
    return !(/\.(mp4|m4v|webm)$/.test(name)||mime.includes("mp4")||mime.includes("webm"))||codec.includes("hevc")||codec.includes("h.265")||codec.includes("x265");
  },

  async switchVariant(v,position,playing){
    const old={variant:this.variant,src:document.getElementById("video").currentSrc};
    await this.select(v,position,playing);
    await this.loadTracks(v); this.render();
    return old;
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

  async loadSource(source,position,playing,v,absoluteStart){
    const video=document.getElementById("video");
    this.removeSubtitleTrack();
    video.pause(); video.removeAttribute("src"); video.load();
    const previous=this.variant;
    this.variant=v; this.quality=v.quality||null;
    this.audio=this.variantAudio(v)[0]||this.audio||null;
    this.subtitle=this.variantSubs(v)[0]||null;
    this.render(); video.src=source; video.load();
    try{
      await new Promise((resolve,reject)=>{let done=false;const ok=()=>{if(!done){done=true;resolve()}};const fail=()=>{if(!done){done=true;reject(new Error("Unable to load this media stream."))}};video.addEventListener("loadedmetadata",ok,{once:true});video.addEventListener("error",fail,{once:true});setTimeout(ok,12000)});
      if(!absoluteStart && position>0 && Number.isFinite(video.duration)){
        const target=Math.min(position,Math.max(0,video.duration-0.25));
        if(target>0){try{video.currentTime=target}catch(_){} await this.restorePosition(target)}
      }
      saveLocal(this.titleId,v.file_id,absoluteStart?position:(video.currentTime||position));
      this.clearError(); if(playing)await video.play().catch(()=>{});
    }catch(e){
      this.variant=previous;
      throw e;
    }
  },

  async restorePosition(target){
    const video=document.getElementById("video");
    for(let i=0;i<8;i++){
      if(Math.abs((video.currentTime||0)-target)<=1)return;
      try{if(video.seekable?.length)video.currentTime=Math.min(target,video.seekable.end(video.seekable.length-1));else video.currentTime=target}catch(_){}
      await new Promise(r=>setTimeout(r,180));
    }
  },

  download(){if(!this.variant?.file_id){this.showError("No playable file is selected.");return}API.get("/api/stream-token/"+encodeURIComponent(this.variant.file_id)).then(d=>{if(!d.token)throw new Error("Server did not return a download token.");location.href="/api/download/"+encodeURIComponent(this.variant.file_id)+"?token="+encodeURIComponent(d.token)}).catch(e=>this.showError(e.message||"Unable to start download."))},
  showError(message){let el=document.getElementById("playerError");if(!el){el=document.createElement("div");el.id="playerError";el.style.cssText="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:3;background:#111;padding:16px 20px;border:1px solid #333;border-radius:10px;max-width:min(90vw,520px);text-align:center;color:#fff";document.getElementById("player").appendChild(el)}el.textContent=message},
  clearError(){document.getElementById("playerError")?.remove()},
  close(){const video=document.getElementById("video");if(this.titleId)saveLocal(this.titleId,this.variant?.file_id,video.currentTime||0);this.removeSubtitleTrack();video.pause();video.removeAttribute("src");video.load();document.getElementById("player").classList.add("hidden");document.getElementById("menu").classList.add("hidden");this.clearError();this.menuSection=null}
};
function playerEscape(value){return String(value??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[m]))}
function saveLocal(id,file,position){if(!id)return;let data={};try{data=JSON.parse(localStorage.getItem("stream_progress")||"{}")||{}}catch(_){data={}}data[id]={file_id:file||null,position:Number(position)||0,updated:Date.now()};localStorage.setItem("stream_progress",JSON.stringify(data))}
