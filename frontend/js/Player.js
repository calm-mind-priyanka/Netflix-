const Player={
 async open(file,mode='watch',retryFn){
  if(!file?.file_id) throw new Error('No real media file was selected.');
  try{
   const d=await API.get(`/api/stream-token/${encodeURIComponent(file.file_id)}`);
   const token=encodeURIComponent(d.token);
   if(mode==='download'){
    // The token endpoint performs the verification/premium access check first.
    // Once it succeeds, the download URL is safe to navigate to without a
    // second browser-side gate that would hide a JSON 403 response.
    const a=document.createElement('a');
    a.href=`/api/download/${encodeURIComponent(file.file_id)}?token=${token}`;
    a.download=file.file_name||'download'; a.rel='noopener';
    document.body.appendChild(a); a.click(); a.remove(); return;
   }
   const video=document.getElementById('video');
   if(!video) throw new Error('Player UI is unavailable.');
   video.src=`/api/stream/${encodeURIComponent(file.file_id)}?token=${token}`;
   video.dataset.fileId=file.file_id; video.load();
   document.getElementById('playerInfo').textContent=`${file.file_name||'Media'} • ${file.quality||'Auto'}${file.languages?.length?` • ${file.languages.join(', ')}`:''}`;
   document.getElementById('playerLayer')?.classList.remove('hidden');
   await video.play().catch(()=>{});
  }catch(e){
   if(e.status===403 && e.data?.verification_required){retryFn?.(e.data);return;}
   throw e;
  }
 },
 close(){const v=document.getElementById('video');if(v){v.pause();v.removeAttribute('src');v.load()}document.getElementById('playerLayer')?.classList.add('hidden')}
};
