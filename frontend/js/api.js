const API={
  async get(url){
    const response=await fetch(url,{credentials:"same-origin"});
    let data=null;
    try{data=await response.json()}catch(_){}
    if(!response.ok){
      throw new Error(data?.error||data?.message||`Request failed (${response.status})`);
    }
    return data;
  }
};
