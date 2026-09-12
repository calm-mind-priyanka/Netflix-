const API={async get(url){const r=await fetch(url);if(!r.ok)throw new Error(await r.text());return r.json();}};
