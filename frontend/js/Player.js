const Player={
  variants:[],
  current:null,
  titleId:null,
  variant:null,
  lang:null,
  quality:null,

  async open(titleId,label,variants,next){
    this.titleId=String(titleId);
    this.variants=Array.isArray(variants)?variants:[];
    this.current={label,next};
    this.variant=null;
    this.lang=null;
    this.quality=null;

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
    const languages=[...new Set(
      this.variants.map(item=>item.language).filter(Boolean)
    )];
    const qualities=[...new Set(
      this.variants.map(item=>item.quality).filter(Boolean)
    )];

    document.getElementById("audio").innerHTML=languages.map(
      language=>`<button data-lang="${esc(language)}">${esc(language)}</button>`
    ).join("");

    document.getElementById("quality").innerHTML=qualities.map(
      quality=>`<button data-q="${esc(quality)}">${esc(quality)}</button>`
    ).join("");

    document.querySelectorAll("[data-lang]").forEach(button=>{
      button.onclick=()=>this.choose("language",button.dataset.lang);
    });
    document.querySelectorAll("[data-q]").forEach(button=>{
      button.onclick=()=>this.choose("quality",button.dataset.q);
    });
  },

  async choose(key,value){
    let language=this.lang||this.variants[0]?.language;
    let quality=this.quality||null;

    if(key==="language")language=value;
    else quality=value;

    let variant=this.variants.find(item=>
      item.language===language&&(!quality||item.quality===quality)
    );

    if(!variant&&key==="quality"){
      variant=this.variants.find(item=>item.quality===quality);
    }
    if(!variant){
      variant=this.variants.find(item=>item.language===language)||this.variants[0];
    }

    this.lang=variant?.language||language||null;
    this.quality=variant?.quality||quality||null;

    try{
      await this.select(variant);
    }catch(error){
      this.showError(error.message||"Unable to switch file version.");
    }
  },

  async select(variant){
    if(!variant?.file_id)throw new Error("This file has no valid media ID.");

    const video=document.getElementById("video");
    const position=Number.isFinite(video.currentTime)?video.currentTime:0;
    const data=await API.get(
      "/api/stream-token/"+encodeURIComponent(variant.file_id)
    );

    if(!data.token)throw new Error("Server did not return a stream token.");

    const source="/api/stream/"+encodeURIComponent(variant.file_id)
      +"?token="+encodeURIComponent(data.token);

    video.src=source;
    video.load();

    video.addEventListener("loadedmetadata",()=>{
      if(position>0){
        try{video.currentTime=position}catch(_){}
      }
      this.clearError();
      video.play().catch(()=>{});
    },{once:true});

    this.variant=variant;
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

function esc(value){
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
