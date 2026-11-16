/* ores.js: Ore patches: the patch popup, the 'Choose ores & find patches' window, and copy-to-chat map pins.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- map position -> clickable pin in game chat ---------------- */
// Factorio turns [gps=x,y] (plus the surface name off Nauvis) in chat into a link that opens the map there.
function gpsTag(x,y){const sn=cur&&cur.index!==1&&cur.sname?","+cur.sname:"";return `[gps=${x},${y}${sn}]`;}
function gpsHtml(x,y){
  return `<button class="gps" data-gps="${esc(gpsTag(x,y))}" title="Copy a map link for game chat">📍 ${x}, ${y} <span>· copy for game chat</span></button>`;
}
document.addEventListener("click",async e=>{
  const b=e.target.closest(".gps");if(!b)return;
  const txt=b.dataset.gps;
  try{await navigator.clipboard.writeText(txt);toast("Copied. In game press T, paste and send: everyone gets a clickable map pin.");}
  catch(err){window.prompt("Copy this, then paste it in game chat (press T):",txt);}
});

/* ---------------- ore patches: popup + ore picker ---------------- */
let ORE_OFF=new Set();
try{ORE_OFF=new Set(JSON.parse(localStorage.getItem("f101-ores-off")||"[]"));}catch(e){}
const oreKey=p=>p.inf?"core-seam":p.name;
function saveOreOff(){try{localStorage.setItem("f101-ores-off",JSON.stringify([...ORE_OFF]));}catch(e){}}
const prow=(a,b)=>`<div class="row"><span>${a}</span><b>${b}</b></div>`;
const playH=h=>h<1?`${Math.max(1,Math.round(h*60))} min`:h<48?`${fmt(h,h<10?1:0)} h`:`${fmt(h/24,0)} days`;
// Room for drills on an ore patch: two rows of electric drills facing a belt use about 10.5 ore tiles per drill.
const TILES_PER_DRILL=10.5, DRILL_PM=30;  // electric mining drill, no modules: 0.5 ore/s
const drillRoom=p=>Math.max(1,Math.round(p.tiles/TILES_PER_DRILL));
function drillsRow(p,n){
  if(n==null)return prow(p.fluid?"Pumpjacks":p.inf?"Core miners":"Drills on it","…");
  if(p.fluid)return prow("Pumpjacks",`${n||"none"} of ${fmt(p.tiles)} well${p.tiles===1?"":"s"}`);
  if(p.inf)return prow("Core miners",n||"none");
  const room=drillRoom(p);
  return prow("Drills on it",!n?`none · room for ~${fmt(room)}`:n>=room*.95?`${fmt(n)} · fully covered`:`${fmt(n)} of ~${fmt(room)}`);
}
let PMORE=false;try{PMORE=localStorage.getItem("f101-pmore")==="1";}catch(e){}
// gx,gy: where the chat pin points (the clicked spot, or the patch centre)
function patchHtml(p,ha,gx=Math.floor(p.x),gy=Math.floor(p.y)){
  const what=p.fluid?"field":p.inf?"seam":"patch";
  const room=drillRoom(p), full=room*DRILL_PM;
  // Up front: only the total, like the game's map shows it. "Details" (remembers open/closed) holds three
  // labelled groups — the resource, mining right now (filled in by patchExtras), an estimate — then the chat pin.
  const main=ha?prow("Total",si(p.amount)):prow(p.fluid?"Wells":"Size",`${fmt(p.tiles)}${p.fluid?"":" tiles"}`);
  const sec=(title,body,note)=>body?`<div class="psec"><h4>${title}</h4>${body}${note?`<p class="pnote">${note}</p>`:""}</div>`:"";
  // pumpjack: 100% yield = 10 fluid/s (600/min) without modules
  const res=p.fluid?(ha&&p.yield!=null?prow("Yield per well",`${fmt(p.yield,0)}%`)+prow("Each pumpjack",`~${si(Math.round(p.yield*6/10)*10)}/min`):"")+prow("Wells",fmt(p.tiles))
    :p.inf?(ha&&p.yield!=null?prow("Yield",`${fmt(p.yield,0)}%`):"")+prow("Size",`${fmt(p.tiles)} tiles`)
    :prow("Size",`${fmt(p.tiles)} tiles`);
  const est=p.fluid?(ha&&p.yield!=null?prow("All wells pumped",`~${si(p.yield*6*p.tiles)}/min`):"")
    :!p.inf&&ha?prow("Fully covered",`~${si(full)}/min`)+prow("Would last",`~${playH(p.amount/full/60)}`):"";
  // small grey footnotes: only what you need to read the numbers
  const resNote=p.inf?"Never runs out. Mine it with a core miner (Core Mining research).":"";
  const estNote=p.fluid?"Pumpjacks without modules.":"Basic electric drills without modules.";
  return `<h3>${esc(p.label)}${p.inf?"":" "+what}</h3>`+main+
    `<details class="pmore"${PMORE?" open":""}><summary>Details</summary>`+
    sec(p.fluid?"Field":p.inf?"Seam":"Patch",res,resNote)+
    `<div class="psec"><h4>Mining now</h4><div class="pinfo">${drillsRow(p,p.drills)}</div></div>`+
    sec("Estimate",est,estNote)+
    gpsHtml(gx,gy)+
    `</details>`;
}
function drawPatchSel(p){
  const shape=(p.hull&&p.hull.length>2)?p.hull.map(q=>ll(q[0],q[1])):[ll(p.bbox[0],p.bbox[1]),ll(p.bbox[2],p.bbox[1]),ll(p.bbox[2],p.bbox[3]),ll(p.bbox[0],p.bbox[3])];
  L.polygon(shape,{color:"#000",weight:5,opacity:.6,fill:false,interactive:false}).addTo(selLayer);
  L.polygon(shape,{color:"#fff",weight:2,fillColor:p.color?"#"+p.color:"#fff",fillOpacity:.12,interactive:false}).addTo(selLayer);
}
async function patchExtras(p,pop){
  const el=pop.getElement();if(!el)return;
  const det=el.querySelector("details.pmore");
  if(det)det.addEventListener("toggle",()=>{PMORE=det.open;try{localStorage.setItem("f101-pmore",PMORE?"1":"0");}catch(e){}
    pop._updateLayout();pop._updatePosition();if(det.open)pop._adjustPan?.();});
  let d;try{d=await api(`/api/patchinfo?snap=${curSnap.id}&s=${cur.index}&id=${p.id}`);}catch(e){return;}
  const box=el.querySelector(".pinfo");if(!box||d.error)return;
  let h=drillsRow(p,d.drills);
  if(d.down_pct>=.5)h+=prow("Mined so far",`${fmt(d.down_pct,d.down_pct<10?1:0)}% since day ${d.since_day}`);
  if(d.per_hour!=null)h+=prow("Mined lately",`~${si(d.per_hour)}/h`)+prow("Lasts",`~${playH(d.hours_left)}`)+
    `<p class="pnote">Over the last ${fmt(d.rate_hours,1)} h of play.</p>`;
  box.innerHTML=h;
  pop._updateLayout();pop._updatePosition();  // resize around the new lines (update() would redraw the old text)
}
function showPatch(p){
  selLayer.clearLayers();
  const go=()=>{drawPatchSel(p);const pop=L.popup({maxWidth:280,autoPanPadding:[20,60]}).setLatLng(ll(p.x,p.y))
    .setContent(`<div class="pop">${patchHtml(p,p._amt)}</div>`).openOn(map);patchExtras(p,pop);};
  map.once("moveend",go);
  map.flyTo(ll(p.x,p.y),Math.max(map.getZoom(),p.tiles<60?0:-1));
}
function oreSheet(focus){
  const groups={};
  for(const p of PATCHES){const k=oreKey(p);(groups[k]=groups[k]||{k,label:p.inf?"Core seams":p.label,color:p.inf?"e040fb":p.color,fluid:p.fluid,inf:p.inf,ps:[]}).ps.push(p);}
  const gs=Object.values(groups).sort((a,b)=>a.label.localeCompare(b.label));
  const bigness=p=>p.amount||p.tiles;
  const bg=document.createElement("div");bg.className="sheet-bg";
  if(!gs.length){toast("No ores on this map yet.");return;}
  bg.innerHTML=`<div class="sheet bevel" role="dialog" aria-label="Ores">
    <h2>Ores on ${esc(cur?.name||"this map")}<button class="close" aria-label="Close">×</button></h2>
    <div class="muted small">Ticked ores get labels on the map. Tap a name to list its patches, biggest first, then tap one to go there.</div>
    <div class="orebtns"><button class="btn ghost mini" data-all="1">Show all</button><button class="btn ghost mini" data-all="0">Hide all</button></div>
    ${gs.map(g=>{const n=g.ps.length,w=g.fluid?"field":g.inf?"seam":"patch";
      return `<div class="oreg" data-k="${esc(g.k)}"><div class="orehead"><input type="checkbox"${ORE_OFF.has(g.k)?"":" checked"} aria-label="Show ${esc(g.label)} on the map">`+
        `<button class="orename"><i style="background:#${esc(g.color||"888")}"></i><span>${esc(g.label)} <span class="muted small">${n} ${w}${n===1?"":w==="patch"?"es":"s"}</span></span><span class="chev">▸</span></button></div><div class="orelist" hidden></div></div>`;}).join("")}
  </div>`;
  const close=()=>bg.remove();
  bg.onclick=e=>{if(e.target===bg)close();};
  bg.querySelector(".close").onclick=close;
  document.addEventListener("keydown",function k(e){if(e.key==="Escape"){close();document.removeEventListener("keydown",k);}});
  const apply=()=>{saveOreOff();buildLabels();};
  bg.querySelectorAll(".oreg").forEach(div=>{
    const k=div.dataset.k, g=groups[k], cb=div.querySelector("input"), list=div.querySelector(".orelist");
    cb.onchange=()=>{if(cb.checked)ORE_OFF.delete(k);else ORE_OFF.add(k);apply();};
    const open=()=>{
      div.classList.toggle("open");list.hidden=!div.classList.contains("open");
      if(list.hidden||list.childElementCount)return;
      const ps=[...g.ps].sort((a,b)=>bigness(b)-bigness(a)).slice(0,40);
      list.innerHTML=ps.map((p,i)=>`<button data-i="${i}"><span>${p._amt?amt(p)+(p.fluid?` · ${p.tiles} well${p.tiles===1?"":"s"}`:""):fmt(p.tiles)+" tiles"}</span>`+
        `<span class="dr${p.drills?" on":""}">${p.drills?`⛏ ${p.drills} ${p.fluid?"pumpjack":p.inf?"miner":"drill"}${p.drills===1?"":"s"}`:"untouched"}</span></button>`).join("")+
        (g.ps.length>40?`<div class="muted small">Biggest 40 of ${g.ps.length}.</div>`:"");
      list.querySelectorAll("button").forEach(b=>b.onclick=()=>{close();if(!$("lyPatches").checked){$("lyPatches").checked=true;$("lyPatches").dispatchEvent(new Event("change"));}
        if(ORE_OFF.has(k)){ORE_OFF.delete(k);apply();}showPatch(ps[+b.dataset.i]);});
    };
    div.querySelector(".orename").onclick=open;
    if(k===focus){open();setTimeout(()=>div.scrollIntoView({block:"start"}),0);}
  });
  bg.querySelectorAll("[data-all]").forEach(b=>b.onclick=()=>{
    const on=b.dataset.all==="1";ORE_OFF=on?new Set():new Set(gs.map(g=>g.k));
    bg.querySelectorAll(".orehead input").forEach(c=>c.checked=on);apply();});
  document.body.appendChild(bg);
}

async function onMapClick(e){
  if(!curSnap)return;
  $("tip").style.opacity=0;
  const x=Math.floor(e.latlng.lng), y=Math.floor(-e.latlng.lat);
  const px=matchMedia("(pointer:coarse)").matches?16:8;  // fingers need more slack than a mouse
  const r=Math.max(2,Math.min(48,Math.round(px/Math.pow(2,map.getZoom()))));
  let d;try{d=await api(`/api/at?snap=${curSnap.id}&s=${cur.index}&x=${x}&y=${y}&r=${r}`);}catch(err){return;}
  selLayer.clearLayers();
  const rows=[];
  if(d.entity){
    rows.push(`<h3>${esc(d.entity.label)}</h3><div class="muted small">${d.entity.kind==="enemy"?"Enemy base":esc((d.entity.type||"").replace(/-/g," "))}</div>`);
    const b=d.entity.bbox;L.rectangle(L.latLngBounds(ll(b[0],b[1]),ll(b[2],b[3])),{color:"#ff9f1b",weight:2,fill:false,interactive:false}).addTo(selLayer);
  }
  if(d.patch){
    rows.push((rows.length?"<hr>":"")+patchHtml(d.patch,d.has_amounts,x,y));  // its Details hold the chat pin
    drawPatchSel(d.patch);
  }
  if(!rows.length){map.closePopup();return;}  // plain ground: nothing worth a popup
  if(!d.patch)rows.push(gpsHtml(x,y));
  const pop=L.popup({maxWidth:280,autoPanPadding:[20,60]}).setLatLng(e.latlng).setContent(`<div class="pop">${rows.join("")}</div>`).openOn(map);
  if(d.patch)patchExtras(d.patch,pop);
}
function togglePlay(){
  if(playing){clearInterval(playing);playing=null;$("play").textContent="▶";return;}
  let i=0;$("play").textContent="❚❚";setSnap(0);$("snap").value=0;
  playing=setInterval(()=>{i++;if(i>=cur.snapshots.length){togglePlay();return;}$("snap").value=i;setSnap(i);},900);
}
function saveHash(){
  if(!map||!cur||view!=="map"||!map._loaded)return;
  const c=map.getCenter();
  history.replaceState(null,"",`#map/${cur.index}/${Math.round(c.lng)}/${Math.round(-c.lat)}/${map.getZoom()}`);
}
let LIVE=null;
async function loadLive(){try{LIVE=await api("/api/live");renderAlert();renderLive();surfaceLabels();}catch(e){}}
// Space Age platforms move: keep "at Nauvis" / "travelling Nauvis → Vulcanus" in the map picker current
function surfaceLabels(){
  const sel=$("surface");if(!sel||!MAPS)return;
  for(const o of sel.options){
    const s=MAPS.surfaces.find(x=>x.index===+o.value), at=LIVE.surfaces?.[o.value]?.at;
    if(s)o.textContent=`${s.name}${s.kind?` · ${s.kind}`:""}${at?` · ${at}`:""}`;
  }
}
// the red banner in the header; each kind can be turned off on the Players tab (PREFS)
function renderAlert(){
  const act=PREFS.alertAttack?(LIVE.attacks||[]).filter(a=>a.active).sort((a,b)=>b.lost-a.lost):[];
  const el=$("alert");
  if(!act.length&&LIVE.power_low&&PREFS.alertPower){
    $("alertText").textContent="Low power: machines are slowing down";
    el.style.display="inline-flex";
    el.onclick=()=>{show("factory");setTimeout(()=>$("powerCard")?.scrollIntoView({behavior:"smooth",block:"start"}),300);};
    return;
  }
  if(!act.length){el.style.display="none";return;}
  const a=act[0];
  $("alertText").textContent=`Under attack${a.near?" near "+a.near:""}${LIVE.surfaces[a.s]?.name&&a.s!==1?" on "+LIVE.surfaces[a.s].name:""} · ${a.lost} lost`+(act.length>1?` (+${act.length-1})`:"");
  el.style.display="inline-flex";
  el.onclick=()=>{show("map");setTimeout(()=>{if(!map||!MAPS)return;if(cur?.index!==a.s&&MAPS.surfaces.some(s=>s.index===a.s)){$("surface").value=a.s;selectSurface(a.s,false);}map.flyTo(ll(a.x,a.y),0);},300);};
}
let lastLabelKey="";
// live markers are kept and moved (not rebuilt) so an open popup survives the 5 s refresh
const KEEP={};
function sync(layer,name,items,keyOf,build){
  const old=KEEP[name]||new Map(), next=new Map();
  for(const it of items){
    const k=keyOf(it), [latlng,html,size,popup,tip,z]=build(it);
    let m=old.get(k);
    if(!m){
      m=L.marker(latlng,{icon:L.divIcon({className:"mk",html,iconSize:Array.isArray(size)?size:[size,size]}),keyboard:false,riseOnHover:true,zIndexOffset:z||0});
      m._html=html;m.bindPopup(popup,{maxWidth:260,autoPanPadding:[20,60]});
      if(tip&&!touch)m.bindTooltip(tip,{direction:"top",offset:[0,-10]});
      m.addTo(layer);
    }else{
      if((name==="trains"||name==="vehicles")&&!map._animatingZoom){const el=m.getElement();if(el)el.style.transition="transform 4.8s linear";}  // glide between 5 s updates
      m.setLatLng(latlng);
      if(m._html!==html){m._html=html;m.setIcon(L.divIcon({className:"mk",html,iconSize:Array.isArray(size)?size:[size,size]}));}
      if(typeof popup==="string"&&m.getPopup().getContent()!==popup)m.setPopupContent(popup);
      if(tip&&m.getTooltip())m.setTooltipContent(tip);
    }
    old.delete(k);next.set(k,m);
  }
  for(const m of old.values())layer.removeLayer(m);
  KEEP[name]=next;
}
function playerPopup(n){
  const box=document.createElement("div");box.className="pop";
  const p=LIVE.players.find(q=>q.n===n);
  if(!p){box.textContent=`${n} went offline`;return box;}
  box.innerHTML=`<h3>${esc(p.n)}</h3>`+
    `<div class="row"><span>Where</span><b>${Math.round(p.x)}, ${Math.round(p.y)}</b></div>`+
    (p.vl?`<div class="row"><span>In</span><b>${esc(p.vl)}</b></div>`:"")+(p.dead?`<div class="muted small">Dead / respawning</div>`:"")+
    `<button class="${follow===p.n?"on":""}">${follow===p.n?"Following · tap to stop":"Follow on map"}</button>`;
  box.querySelector("button").onclick=()=>{follow=follow===p.n?null:p.n;map.closePopup();renderLive();if(follow)map.panTo(ll(p.x,p.y));};
  return box;
}
function renderLive(){
  if(!map||!cur||!LIVE)return;
  attackLayer.clearLayers();
  for(const a of (LIVE.attacks||[]).filter(a=>a.s===cur.index)){
    L.marker(ll(a.x,a.y),{icon:L.divIcon({className:"atkmark"+(a.active?"":" old"),iconSize:null}),keyboard:false})
      .bindPopup(`<div class="pop"><h3>${a.active?"Under attack":"Recent attack"}</h3>${a.near?`<div class="row"><span>Near</span><b>${esc(a.near)}</b></div>`:""}<div class="row"><span>Lost</span><b>${a.lost}</b></div><div class="muted small">First loss: ${esc(a.what_label)}</div></div>`).addTo(attackLayer);
  }
  if(follow&&!LIVE.players.some(p=>p.n===follow))follow=null;
  sync(trainLayer,"trains",LIVE.trains.filter(t=>t.s===cur.index),t=>t.i,t=>{
    const state=t.m?"m":t.sp>0?"mv":t.st?"st":"wait";
    const what={m:"Driven by hand",mv:"Moving",st:"Stopped at "+esc(t.st||"a station"),wait:"Stopped: waiting at a signal, blocked or no path"}[state];
    if(map.getZoom()<1)return [ll(t.x,t.y),`<div class="trn mini ${state}"></div>`,touch?[30,24]:[22,16],
      `<div class="pop"><h3>Train · ${t.n} cars</h3><div class="row"><span>State</span><b>${what}</b></div><div class="row"><span>Speed</span><b>${t.sp} km/h</b></div></div>`,
      `${what} · ${t.n} cars${t.sp?` · ${t.sp} km/h`:""}`];
    return [ll(t.x,t.y),`<div class="trn ${state}"><svg viewBox="0 0 16 12" aria-hidden="true"><path fill="currentColor" d="M1 2h9l3 3v4H1zM3 3.5v2h3v-2zm4.5 0v2h2.2l-1.6-2z"/><circle cx="3.5" cy="10.3" r="1.4" fill="currentColor"/><circle cx="10" cy="10.3" r="1.4" fill="currentColor"/></svg><span>${t.n}</span></div>`,[44,28],
      `<div class="pop"><h3>Train · ${t.n} cars</h3><div class="row"><span>State</span><b>${what}</b></div><div class="row"><span>Speed</span><b>${t.sp} km/h</b></div></div>`,
      `${what} · ${t.n} cars${t.sp?` · ${t.sp} km/h`:""}`];});
  sync(vehLayer,"vehicles",(LIVE.vehicles||[]).filter(v=>v.s===cur.index),v=>v.u,v=>{
    const spider=v.t==="spider", named=v.l?esc(v.l):"";
    return [ll(v.x,v.y),`<div class="veh${spider?" spider":""}${v.d?" driven":""}">${spider?'<svg viewBox="0 0 16 12" aria-hidden="true"><circle cx="8" cy="5" r="3" fill="currentColor"/><path stroke="currentColor" stroke-width="1.3" fill="none" d="M5.5 4L1 1.5M5.5 6L1 10M10.5 4L15 1.5M10.5 6L15 10M6.5 7.5L4 11.5M9.5 7.5L12 11.5"/></svg>':'<svg viewBox="0 0 16 12" aria-hidden="true"><path fill="currentColor" d="M3 4l2-3h6l2 3h1.5v4H1.5V4zm2.4 0h5.2L9.4 2H6.6z"/><circle cx="4.5" cy="9.5" r="1.6" fill="currentColor"/><circle cx="11.5" cy="9.5" r="1.6" fill="currentColor"/></svg>'}${named?`<span>${named}</span>`:""}</div>`,[named?90:40,26],
      `<div class="pop"><h3>${esc(v.label)}</h3><div class="row"><span>Type</span><b>${spider?"Spidertron-type":"Car / tank"}</b></div>${v.d?`<div class="row"><span>Driver</span><b>${esc(v.d)}</b></div>`:`<div class="muted small">Parked · ${v.x}, ${v.y}</div>`}</div>`,
      `${esc(v.label)}${v.d?" · "+esc(v.d):" · parked"}`];});
  const here=LIVE.players.filter(p=>p.s===cur.index);
  sync(markers,"players",here,p=>p.n,p=>{const c=p.c||"ffffff";return [ll(p.x,p.y),
    `<div class="pl${p.dead?" dead":""}${follow===p.n?" follow":""}"><b style="background:#${esc(c)};color:${textOn(c)}">${esc((p.n||"?")[0].toUpperCase())}</b><span>${esc(p.n)}${p.vl?" · "+esc(p.vl):""}</span></div>`,
    44,()=>playerPopup(p.n),null,1000];});
  const fp=here.find(p=>p.n===follow);
  if(fp&&!map.getBounds().pad(-.25).contains(ll(fp.x,fp.y)))map.panTo(ll(fp.x,fp.y));
  // tags/stations change rarely: only rebuild labels when they did
  const key=JSON.stringify([LIVE.tags,LIVE.stations,cur.index]);
  if(key!==lastLabelKey){lastLabelKey=key;buildLabels();}
  $("pBox").hidden=!LIVE.players.length;
  $("plist").innerHTML=LIVE.players.map(p=>`<button data-s="${p.s}" data-x="${p.x}" data-y="${p.y}"><span><span class="dot on"></span> ${esc(p.n)}</span><span class="muted small">${p.s===cur.index?"":esc(LIVE.surfaces[p.s]?.name||"")}</span></button>`).join("");
  $("plist").querySelectorAll("[data-x]").forEach(b=>b.onclick=()=>{
    if(+b.dataset.s!==cur.index&&MAPS.surfaces.some(s=>s.index===+b.dataset.s)){$("surface").value=b.dataset.s;selectSurface(+b.dataset.s,false);}
    map.flyTo(ll(+b.dataset.x,+b.dataset.y),Math.max(map.getZoom(),0));if(mobile())$("mapctl").classList.add("closed");
  });
}
