/* boot.js: Start-up: opens the tab from the link, and the timers that keep the page up to date.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- boot ---------------- */
Chart.defaults.font.family=getComputedStyle(document.body).fontFamily;
show((location.hash.slice(1).split("/")[0])||"map");
loadOverview();
setInterval(loadOverview,30000);
setInterval(loadLive,5000);loadLive();
setInterval(refreshMaps,15000);  // the live map gets a new version about once a minute: show it soon after
document.addEventListener("visibilitychange",()=>{if(!document.hidden)refreshMaps();});  // back from another tab or a sleeping phone
setInterval(mapAge,5000);
setInterval(()=>{if(view==="events"&&evTypes===""&&lastId){api("/api/events?types=").then(d=>{const top=document.querySelector("#feed li");if(d.events.length&&top&&+d.events[0].id>(+top.dataset.id||0)){loadEvents(true);}}).catch(()=>{});}},20000);
window.addEventListener("hashchange",()=>{
  const h=location.hash.slice(1).split("/");
  if(h[0]!==view)show(h[0]);
  if(h[0]==="map"&&h.length>=5&&map&&MAPS){
    const si=+h[1];
    if(cur?.index!==si&&MAPS.surfaces.some(s=>s.index===si)){$("surface").value=si;selectSurface(si,false);}
    map.setView(ll(+h[2],+h[3]),+h[4]);
  }
});
