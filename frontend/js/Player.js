const Player={
  variants:[],
  current:null,
  titleId:null,
  variant:null,
  lang:null,
  quality:null,
  audio:null,
  menuSection:null,

  async open(titleId,label,variants,next){
    this.titleId=String(titleId);
    this.variants=Array.isArray(variants)?variants.filter(item=>item?.file_id):[];
    this.current={label,next};
    this.variant=null;
    this.lang=null;
    this.quality=null;
    this.audio=null;
    this.menuSection=null;

    const player=document.getElementById("player");
    const menu=document.getElementById("menu");
    const video=document.getElementById("video");

    player.classList.remove("hidden");
    menu.classList.add("hidden");
    document.getElementById("nowPlaying").textContent=label||"Now Playing";
    this.render();

    const first=this.variants[0];
    if(!first){
      this.showError("No playable file version is available.");
      document.getElementById("next").classList.add("hidden");
      return;
    }

    try{await this.select(first,0,false)}
    catch(error){this.showError(error.message||"Unable to start playback.");return}

    if(next&&Array.isArray(next.variants)&&next.variants.length){
      const nextButton=document.getElementById("next");
      nextButton.classList.remove("hidden");
      nextButton.onclick=()=>this.open(
        titleId,
        `${next.title} • S${String(next.season).padStart(2,"0")} E${String(next.episode).padStart(2,"0")}`,
        next.variants,
        next.next||null
      );
    }else document.getElementById("next").classList.add("hidden");

    video.onplay=()=>this.clearError();
  },

  render(){
    const languages=[...new Set(this.variants.flatMap(item=>Array.isArray(item.languages)?item.languages:[item.language]).filter(Boolean).filter(value=>value!=="Unknown"))];
    const audios=[...new Set(this.variants.flatMap(item=>Array.isArray(item.audio)?item.audio:[]).filter(Boolean).filter(value=>value!=="Unknown"))];
    const qualities=[...new Set(this.variants.map(item=>item.quality).filter(Boolean))];

    const section=(key,label,values,current)=>{
      if(!values.length)return "";
      const active=current||"Not selected";
      return `<button class="settingRow" data-setting-section="${key}" aria-expanded="${this.menuSection===key}"><span><b>${label}</b><small>${playerEscape(active)}</small></span><span>›</span></button>
      <div class="settingSubmenu ${this.menuSection===key?"":"hidden"}" data-setting-options="${key}">
        ${values.map(value=>`<button class="settingOption ${value===current?"active":""}" data-player-${key}="${playerEscape(value)}"><span>${playerEscape(value)}</span>${value===current?"✓":""}</button>`).join("")}
      </div>`;
    };

    document.getElementById("menu").innerHTML=`<div class="settingsHead"><h3>Settings</h3><button id="closeSettings" aria-label="Close settings">×</button></div>
      ${section("language","Language",languages,this.lang)}
      ${section("audio","Audio",audios,this.audio)}
      ${section("quality","Quality",qualities,this.quality)}`;

    document.querySelectorAll("[data-setting-section]").forEach(button=>{
      button.onclick=()=>{
        this.menuSection=this.menuSection===button.dataset.settingSection?null:button.dataset.settingSection;
        this.render();
      };
    });
    document.getElementById("closeSettings")?.addEventListener("click",()=>{
      this.menuSection=null;
      document.getElementById("menu").classList.add("hidden");
    });
    document.querySelectorAll("[data-player-language]").forEach(button=>button.onclick=()=>this.choose("language",button.dataset.playerLanguage));
    document.querySelectorAll("[data-player-audio]").forEach(button=>button.onclick=()=>this.choose("audio",button.dataset.playerAudio));
    document.querySelectorAll("[data-player-quality]").forEach(button=>button.onclick=()=>this.choose("quality",button.dataset.playerQuality));
  },

  matchesLanguage(variant,value){return (Array.isArray(variant?.languages)?variant.languages:[variant?.language]).includes(value)},
  matchesAudio(variant,value){return (Array.isArray(variant?.audio)?variant.audio:[]).includes(value)},

  async choose(key,value){
    const desired={language:this.lang,quality:this.quality,audio:this.audio};
    desired[key]=value;
    const exact=this.variants.find(variant=>
      (!desired.language||this.matchesLanguage(variant,desired.language))&&
      (!desired.quality||variant.quality===desired.quality)&&
      (!desired.audio||this.matchesAudio(variant,desired.audio))
    );
    const candidate=exact||this.variants.find(variant=>key==="language"?this.matchesLanguage(variant,value):key==="quality"?variant.quality===value:this.matchesAudio(variant,value));
    if(!candidate){this.showError("That option is not available for this file.");return}

    const video=document.getElementById("video");
    const position=Number.isFinite(video.currentTime)?video.currentTime:0;
    const wasPlaying=!video.paused&&!video.ended;
    this.lang=(Array.isArray(candidate.languages)?candidate.languages:[candidate.language]).find(Boolean)||null;
    this.quality=candidate.quality||null;
    this.audio=(Array.isArray(candidate.audio)?candidate.audio:[]).find(value=>value!=="Unknown")||null;
    this.menuSection=null;

    try{
      await this.select(candidate,position,wasPlaying);
      document.getElementById("menu").classList.add("hidden");
    }catch(error){this.showError(error.message||"Unable to switch file version.")}
  },

  needsCompatibility(variant){
    const name=String(variant?.file_name||"").toLowerCase();
    const mime=String(variant?.mime_type||"").toLowerCase();
    const codec=String(variant?.codec||"").toLowerCase();
    const audio=(Array.isArray(variant?.audio)?variant.audio:[]).join(" ").toLowerCase();
    const nonMp4=!(/\.(mp4|m4v|webm)$/.test(name)||mime.includes("mp4")||mime.includes("webm"));
    const h265=codec.includes("h.265")||codec.includes("hevc")||codec.includes("x265");
    const incompatibleAudio=!audio.includes("aac")&&audio!==""&&/(ddp|eac3|ac3|dts|atmos)/i.test(audio);
    return nonMp4||h265||incompatibleAudio;
  },

  async select(variant,requestedPosition=0,wasPlaying=false){
    if(!variant?.file_id)throw new Error("This file has no valid media ID.");
    const video=document.getElementById("video");
    const position=Math.max(0,Number.isFinite(requestedPosition)?requestedPosition:(Number.isFinite(video.currentTime)?video.currentTime:0));
    const sameFile=this.variant?.file_id===variant.file_id && video.currentSrc;
    if(sameFile){
      if(position>0&&Math.abs(video.currentTime-position)>1)video.currentTime=position;
      if(wasPlaying)video.play().catch(()=>{});
      return;
    }

    const data=await API.get("/api/stream-token/"+encodeURIComponent(variant.file_id));
    if(!data.token)throw new Error("Server did not return a stream token.");
    const endpoint=this.needsCompatibility(variant)?"/api/stream-compatible/":"/api/stream/";
    const source=endpoint+encodeURIComponent(variant.file_id)+"?token="+encodeURIComponent(data.token);

    this.variant=variant;
    const languages=Array.isArray(variant.languages)?variant.languages:[variant.language];
    this.lang=languages.find(Boolean)||null;
    this.quality=variant.quality||null;
    this.audio=(Array.isArray(variant.audio)?variant.audio:[]).find(value=>value!=="Unknown")||null;
    this.render();

    video.pause();
    video.src=source;
    video.load();

    await new Promise((resolve,reject)=>{
      let settled=false;
      const done=()=>{if(settled)return;settled=true;resolve()};
      const fail=()=>{if(settled)return;settled=true;reject(new Error("Unable to load this media stream."))};
      video.addEventListener("loadedmetadata",done,{once:true});
      video.addEventListener("error",fail,{once:true});
      setTimeout(()=>{if(!settled)done()},12000);
    });

    if(position>0 && Number.isFinite(video.duration) && position<video.duration){
      try{video.currentTime=position}catch(_){ }
    }
    saveLocal(this.titleId,variant.file_id,Math.min(position,Number.isFinite(video.duration)?video.duration:position));
    this.clearError();
    if(wasPlaying)video.play().catch(()=>{});
  },

  download(){
    if(!this.variant?.file_id){this.showError("No playable file is selected.");return}
    API.get("/api/stream-token/"+encodeURIComponent(this.variant.file_id)).then(data=>{
      if(!data.token)throw new Error("Server did not return a download token.");
      location.href="/api/download/"+encodeURIComponent(this.variant.file_id)+"?token="+encodeURIComponent(data.token);
    }).catch(error=>this.showError(error.message||"Unable to start download."));
  },

  showError(message){
    let element=document.getElementById("playerError");
    if(!element){element=document.createElement("div");element.id="playerError";element.style.cssText="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:3;background:#111;padding:16px 20px;border:1px solid #333;border-radius:10px;max-width:min(90vw,520px);text-align:center;color:#fff";document.getElementById("player").appendChild(element)}
    element.textContent=message;
  },
  clearError(){document.getElementById("playerError")?.remove()},
  close(){
    const video=document.getElementById("video");
    if(this.titleId)saveLocal(this.titleId,this.variant?.file_id,video.currentTime||0);
    video.pause();video.removeAttribute("src");video.load();
    document.getElementById("player").classList.add("hidden");
    document.getElementById("menu").classList.add("hidden");
    this.clearError();this.menuSection=null;
  }
};

function playerEscape(value){return String(value??"").replace(/[&<>"']/g,match=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[match]))}
function saveLocal(id,file,position){
  if(!id)return;
  let data={};
  try{data=JSON.parse(localStorage.getItem("stream_progress")||"{}")||{}}catch(_){data={}}
  data[id]={file_id:file||null,position:Number(position)||0,updated:Date.now()};
  localStorage.setItem("stream_progress",JSON.stringify(data));
}
