let maintenance=false;

async function req(url,options={}){
  const response=await fetch(url,{credentials:"same-origin",...options});
  if(response.status===401){
    location.href="/admin";
    return null;
  }
  const data=await response.json().catch(()=>({}));
  if(!response.ok)throw new Error(data.error||data.message||"Request failed");
  return data;
}

async function load(){
  const data=await req("/admin/api/status");
  if(!data)return;

  maintenance=Boolean(data.maintenance);
  document.getElementById("titles").textContent=data.titles;
  document.getElementById("movies").textContent=data.movies;
  document.getElementById("series").textContent=data.series;
  document.getElementById("state").textContent=maintenance
    ?"Website is OFFLINE — maintenance mode is ON."
    :"Website is ONLINE.";
  document.getElementById("toggle").textContent=maintenance
    ?"Turn website ON"
    :"Put website in maintenance";
}

document.getElementById("toggle").onclick=async()=>{
  const button=document.getElementById("toggle");
  button.disabled=true;
  try{
    await req("/admin/api/maintenance",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({maintenance:!maintenance})
    });
    await load();
  }catch(error){
    alert(error.message);
  }finally{
    button.disabled=false;
  }
};

document.getElementById("refresh").onclick=async()=>{
  const button=document.getElementById("refresh");
  button.disabled=true;
  try{
    await req("/admin/api/refresh",{method:"POST"});
    await load();
  }catch(error){
    alert(error.message);
  }finally{
    button.disabled=false;
  }
};

document.getElementById("logout").onclick=async()=>{
  try{await req("/admin/logout",{method:"POST"})}
  finally{location.href="/admin"}
};

load().catch(error=>{
  document.getElementById("state").textContent=error.message;
});
