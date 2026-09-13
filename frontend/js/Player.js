const Player={
  variants:[],
  current:null,
  titleId:null,
  variant:null,
  lang:null,
  quality:null,
  audio:null,

  async open(titleId,label,variants,next){
    this.titleId=String(titleId);
    this.variants=Array.isArray(variants)?variants.filter(item=>item?.file_id):[];
    this.current={label,next};
    this.variant=null;
    this.lang=null;
    this.quality=null;
    this.audio=null;

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

    try{
      await this.select(first);
    }catch(error){
      this.showError(error.message||"Unable to start playback.");
      return;
    }

    if(next&&Array.isArray(next.variants)&&next.variants.length){
      const nextButton=document.getElementById("next");
      nextButton.classList.remove("hidden");
      nextButton.onclick=()=>this.open(
        titleId,
        `${next.title} • S${String(next.season).padStart(2,"0")} E${String(next.episode).padStart(2,"0")}`,
        next.variants,
        next.next||null
      );
    }else{
      document.getElementById("next").classList.add("hidden");
    }

    video.onplay=()=>this.clearError();
  },

  render(){
    const languages=[...new Set(this.variants.flatMap(item=>Array.isArray(item.languages)?item.languages:[item.language]).filter(Boolean).filter(value=>value!=="Unknown"))];
    const audios=[...new Set(this.variants.flatMap(item=>Array.isArray(item.audio)?item.audio:[]).filter(Boolean).filter(value=>value!=="Unknown"))];
    const qualities=[...new Set(this.variants.map(item=>item.quality).filter(Boolean))];

    document.getElementById("language").innerHTML=languages.map(
      language=>`<button data-player-lang="${playerEscape(language)}">${playerEscape(language)}</button>`
    ).join("");
    document.getElementById("audio").innerHTML=audios.map(
      audio=>`<button data-player-audio="${playerEscape(audio)}">${playerEscape(audio)}</button>`
    ).join("");
    document.getElementById("quality").innerHTML=qualities.map(
      quality=>`<button data-player-quality="${playerEscape(quality)}">${playerEscape(quality)}</button>`
    ).join("");

    document.querySelectorAll("[data-player-lang]").forEach(button=>{
      button.onclick=()=>this.choose("language",button.dataset.playerLang);
    });
    document.querySelectorAll("[data-player-audio]").forEach(button=>{
      button.onclick=()=>this.choose("audio",button.dataset.playerAudio);
    });
    document.querySelectorAll("[data-player-quality]").forEach(button=>{
      button.onclick=()=>this.choose("quality",button.dataset.playerQuality);
    });
  },

  matchesLanguage(variant,value){
    return (Array.isArray(variant?.languages)?variant.languages:[variant?.language]).includes(value);
  },

  matchesAudio(variant,value){
    return (Array.isArray(variant?.audio)?variant.audio:[]).includes(value);
  },

  async choose(key,value){
    const desired={
      language:this.lang,
      quality:this.quality,
      audio:this.audio,
    };
    desired[key]=value;

    const exact=this.variants.find(variant=>
      (!desired.language||this.matchesLanguage(variant,desired.language))&&
      (!desired.quality||variant.quality===desired.quality)&&
      (!desired.audio||this.matchesAudio(variant,desired.audio))
    );

    // Never invent a combination. If the requested combination does not exist,
    // choose a real variant matching the newly selected option and reset the
    // other filters to that variant's actual metadata.
    const candidate=exact||this.variants.find(variant=>{
      if(key==="language")return this.matchesLanguage(variant,value);
      if(key==="quality")return variant.quality===value;
      return this.matchesAudio(variant,value);
    });

    if(!candidate){
      this.showError("That option is not available for this file.");
      return;
    }

    this.lang=(Array.isArray(candidate.languages)?candidate.languages:[candidate.language]).find(Boolean)||null;
    this.quality=candidate.quality||null;
    this.audio=(Array.isArray(candidate.audio)?candidate.audio:[]).find(value=>value!=="Unknown")||null;

    try{
      await this.select(candidate);
    }catch(error){
      this.showError(error.message||"Unable to switch file version.");
    }
  },

  needsCompatibility(variant){
    const name=String(variant?.file_name||"").toLowerCase();
    const mime=String(variant?.mime_type||"").toLowerCase();
    const codec=String(variant?.codec||"").toLowerCase();
    const audio=(Array.isArray(variant?.audio)?variant.audio:[]).join(" ").toLowerCase();
    const nonMp4=!(/\.(mp4|m4v|webm)$/.test(name)||mime.includes("mp4")||mime.includes("webm"));
    const h265=codec.includes("h.265")||codec.includes("hevc")||codec.includes("x265");
    const browserAudio=audio.includes("aac")||audio==="";
    const incompatibleAudio=!browserAudio&&/(ddp|eac3|ac3|dts|atmos)/i.test(audio);
    return nonMp4||h265||incompatibleAudio;
  },

  async select(variant){
    if(!variant?.file_id)throw new Error("This file has no valid media ID.");

    const video=document.getElementById("video");
    const position=Number.isFinite(video.currentTime)?video.currentTime:0;
    const data=await API.get(
      "/api/stream-token/"+encodeURIComponent(variant.file_id)
    );

    if(!data.token)throw new Error("Server did not return a stream token.");

    const endpoint=this.needsCompatibility(variant)
      ?"/api/stream-compatible/"
      :"/api/stream/";
    const source=endpoint+encodeURIComponent(variant.file_id)
      +"?token="+encodeURIComponent(data.token);

    video.src=source;
    video.load();

    video.addEventListener("loadedmetadata",()=>{
      if(position>0){
        try{video.currentTime=position}catch(_){ }
      }
      this.clearError();
      video.play().catch(()=>{});
    },{once:true});

    this.variant=variant;
    const languages=Array.isArray(variant.languages)?variant.languages:[variant.language];
    this.lang=languages.find(Boolean)||null;
    this.quality=variant.quality||null;
    this.audio=(Array.isArray(variant.audio)?variant.audio:[]).find(value=>value!=="Unknown")||null;
    saveLocal(this.titleId,variant.file_id,position);
  },

  download(){
    if(!this.variant?.file_id){
      this.showError("No playable file is selected.");
      return;
    }

    API.get(
      "/api/stream-token/"+encodeURIComponent(this.variant.file_id)
    ).then(data=>{
      if(!data.token)throw new Error("Server did not return a download token.");
      location.href="/api/download/"
        +encodeURIComponent(this.variant.file_id)
        +"?token="+encodeURIComponent(data.token);
    }).catch(error=>{
      this.showError(error.message||"Unable to start download.");
    });
  },

  showError(message){
    let element=document.getElementById("playerError");
    if(!element){
      element=document.createElement("div");
      element.id="playerError";
      element.style.cssText="position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);z-index:3;background:#111;padding:16px 20px;border:1px solid #333;border-radius:10px;max-width:min(90vw,520px);text-align:center;color:#fff";
      document.getElementById("player").appendChild(element);
    }
    element.textContent=message;
  },

  clearError(){
    document.getElementById("playerError")?.remove();
  },

  close(){
    const video=document.getElementById("video");
    if(this.titleId){
      saveLocal(this.titleId,this.variant?.file_id,video.currentTime||0);
    }
    video.pause();
    video.removeAttribute("src");
    video.load();
    document.getElementById("player").classList.add("hidden");
    document.getElementById("menu").classList.add("hidden");
    this.clearError();
  }
};

function playerEscape(value){
  return String(value??"").replace(/[&<>"']/g,match=>({
    "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
  }[match]));
}

function saveLocal(id,file,position){
  if(!id)return;
  let data={};
  try{data=JSON.parse(localStorage.getItem("stream_progress")||"{}")||{}}
  catch(_){data={}}

  data[id]={
    file_id:file||null,
    position:Number(position)||0,
    updated:Date.now()
  };
  localStorage.setItem("stream_progress",JSON.stringify(data));
}
