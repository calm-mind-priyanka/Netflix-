const API={
  async request(url,options={}){
    const response=await fetch(url,{credentials:"same-origin",...options});
    let data=null; try{data=await response.json()}catch(_){}
    if(!response.ok){
      const err=new Error(data?.error||data?.message||`Request failed (${response.status})`);
      err.status=response.status; err.data=data||{}; throw err;
    }
    return data;
  },
  async get(url,options={}){return this.request(url,options)},
  async post(url,body){return this.request(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})}
};
