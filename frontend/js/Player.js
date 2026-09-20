const Player={
 async open(file,mode='watch',retryFn){
  if(!file?.file_id) throw new Error('No real media file was selected.');
  try{
   const d=await API.get(`/api/stream-token/${encodeURIComponent(file.file_id)}`);
   const video=document.getElementById('video');
   const src=`/api/stream/${encodeURIComponent(file.file_id)}?token=${encodeURIComponent(d.token)}`;
   if(mode==='download'){const a=document.createElement('a');a.href=`/api/download/${encodeURIComponent(file.file_id)}?token=${encodeURIComponent(d.token)}`;a.download=file.file_name||'download';document.body.appendChild(a);a.click();a.remove();return}
   if(!video) throw new Error('Player UI is unavailable.');
   video.src=src;video.load();document.getElementById('playerInfo').textContent=`${file.file_name||'Media'} • ${file.quality||''} ${file.languages?.join(', ')||''}`;document.getElementById('playerLayer')?.classList.remove('hidden');
   await video.play().catch(()=>{});
  }catch(e){
   if(e.status===403 && e.data?.verification_required){retryFn?.(e.data);return}
   if(e.status===403 && e.data?.download_blocked){throw new Error('Verification is required before download.')}
   throw e;
  }
 },
 close(){const v=document.getElementById('video');if(v){v.pause();v.removeAttribute('src');v.load()}document.getElementById('playerLayer')?.classList.add('hidden')}
};
