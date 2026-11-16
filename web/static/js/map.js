/* map.js: The Map tab: Leaflet map, map/timelapse picker, layers, live players, labels and their decluttering, overlays.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- map ---------------- */
let map=null, MAPS=null, cur=null, tileLayer=null, playing=null, curSnap=null, patchReq=0, PATCHES=[];
const vehLayer=L.layerGroup(), keyLayer=L.layerGroup(), defLayer=L.layerGroup(), roboLayer=L.layerGroup();
const nestLayer=L.layerGroup(), polLayer=L.layerGroup(), histLayer=L.layerGroup(), chgLayer=L.layerGroup();
const markers=L.layerGroup(), trainLayer=L.layerGroup(), dotLayer=L.layerGroup(), labelLayer=L.layerGroup(), attackLayer=L.layerGroup(), selLayer=L.layerGroup();
const ll=(x,y)=>L.latLng(-y,x);
const BLANK="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
const mobile=()=>matchMedia("(max-width:800px)").matches;
const touch=matchMedia("(pointer:coarse)").matches;
function mk(x,y,cls,size,html=""){  // a marker whose icon box (tap area) is bigger than what's drawn
  return L.marker(ll(x,y),{icon:L.divIcon({className:"mk",html:`<div class="${cls}">${html}</div>`,iconSize:[size,size]}),keyboard:false,riseOnHover:true});
}
function info(m,title,body){  // hover tooltip on desktop, tap popup everywhere
  if(!touch)m.bindTooltip(title,{direction:"top",offset:[0,-8]});
  m.bindPopup(`<div class="pop"><h3>${title}</h3>${body||""}</div>`,{maxWidth:260,autoPanPadding:[20,60]});
  return m;
}
const textOn=hex=>{const n=parseInt(hex||"ffffff",16);return ((n>>16)*299+((n>>8)&255)*587+(n&255)*114)/1000>140?"#000":"#fff";};
let follow=null;
// amount shown on labels, lists and search: the total, like the game's own map ("Crude oil 4.4M");
// the yield % of oil / mineral water / core seams is in the patch popup's Details
const amt=p=>si(p.amount);

/* ---------------- screenshot: save the current view (layers, labels, popups) as a PNG ---------------- */
// Uses vendor/html-to-image.js. Map tiles come from this site, so the browser allows copying them into the picture.
// Started from the "Save this view as a picture" button at the bottom of the Layers panel.
let shooting=false;
async function shotMap(){
  if(shooting||!window.htmlToImage)return;
  shooting=true;$("shotBtn").disabled=true;toast("Taking the picture…");
  const el=$("map");
  try{
    const canvas=await htmlToImage.toCanvas(el,{pixelRatio:Math.min(2,window.devicePixelRatio||1),
      backgroundColor:getComputedStyle(el).backgroundColor||"#111",
      filter:n=>!(n.classList&&n.classList.contains("leaflet-control-container"))});  // no zoom buttons in the picture
    // caption in the corner: server · map · day
    const day=curSnap?.day, cap=[document.title.split(" · ")[0],cur?.name,day?"day "+day:""].filter(Boolean).join(" · ");
    const ctx=canvas.getContext("2d"),k=canvas.width/el.clientWidth;
    ctx.font=`600 ${13*k}px ${getComputedStyle(document.body).fontFamily}`;
    const w=ctx.measureText(cap).width;
    ctx.fillStyle="#000a";ctx.fillRect(8*k,canvas.height-30*k,w+16*k,22*k);
    ctx.fillStyle="#fff";ctx.fillText(cap,16*k,canvas.height-14*k);
    const a=document.createElement("a");
    a.href=canvas.toDataURL("image/png");
    a.download=(cap.replace(/[^\w\- ]+/g,"").trim().replace(/\s+/g,"-")||"map")+".png";
    document.body.appendChild(a);a.click();a.remove();
    toast("Picture saved.");
  }catch(e){toast("Couldn't take the picture in this browser.");}
  shooting=false;$("shotBtn").disabled=false;
}
async function initMap(){
  if(map)return;
  map=L.map("map",{crs:L.CRS.Simple,zoomControl:true,attributionControl:false,minZoom:-6,maxZoom:3,zoomSnap:.5,preferCanvas:true,tap:true});
  // the panel can be hidden on any screen; desktop remembers the choice (phones always start with it closed)
  const PKEY="f101-panel-hidden";
  let hid=false;try{hid=localStorage.getItem(PKEY)==="1";}catch(e){}
  if(mobile()||hid)$("mapctl").classList.add("closed");
  const syncBtn=()=>{const c=$("mapctl").classList.contains("closed");$("ctlbtn").textContent=c?"☰ Layers":"✕ Hide";$("ctlbtn").title=(c?"Show":"Hide")+" the layers panel (key: L)";};
  const togglePanel=()=>{$("mapctl").classList.toggle("closed");syncBtn();
    if(!mobile())try{localStorage.setItem(PKEY,$("mapctl").classList.contains("closed")?"1":"0");}catch(e){}};
  new MutationObserver(syncBtn).observe($("mapctl"),{attributes:true,attributeFilter:["class"]});
  syncBtn();
  document.addEventListener("keydown",e=>{
    if(e.key.toLowerCase()!=="l"||e.ctrlKey||e.metaKey||e.altKey||view!=="map"||/input|select|textarea/i.test(e.target.tagName))return;
    togglePanel();
  });
  $("shotBtn").onclick=()=>{if(mobile())$("mapctl").classList.add("closed");setTimeout(shotMap,mobile()?350:0);};
  const LKEY="f101-layers-v3";  // each viewer's own layer choices (per device)
  try{const saved=JSON.parse(localStorage.getItem(LKEY)||"{}");for(const [id,v] of Object.entries(saved)){const el=$(id);if(el&&id.startsWith("ly"))el.checked=!!v;}}catch(e){}
  document.querySelectorAll('#mapctl input[id^="ly"]').forEach(el=>el.addEventListener("change",()=>{
    try{const o={};document.querySelectorAll('#mapctl input[id^="ly"]').forEach(x=>o[x.id]=x.checked);localStorage.setItem(LKEY,JSON.stringify(o));}catch(e){}
  }));
  for(const [l,id] of [[dotLayer],[trainLayer,"lyTrains"],[vehLayer,"lyVeh"],[attackLayer,"lyAttacks"],[selLayer],[labelLayer],[markers,"lyPlayers"]])
    if(!id||$(id).checked)l.addTo(map);
  $("ctlbtn").onclick=togglePanel;
  map.on("zoomend",()=>{applyZoomStyle();buildLabels();renderLive();});
  map.on("zoomstart",()=>{vehLayer.eachLayer(m=>{const el=m.getElement();if(el)el.style.transition="";});trainLayer.eachLayer(m=>{const el=m.getElement();if(el)el.style.transition="";});});
  map.on("moveend",saveHash);  // panning never changes which labels show: overlaps only change when zooming
  map.on("mousemove",e=>{$("coords").textContent=`${Math.floor(e.latlng.lng)}, ${Math.floor(-e.latlng.lat)}`;});
  // wait out a possible double-tap/double-click (that's a zoom, not a selection)
  let clickT=0;
  map.on("dragstart",()=>{if(follow){follow=null;renderLive();}});
  map.on("click",e=>{clearTimeout(clickT);clickT=setTimeout(()=>onMapClick(e),300);});
  map.on("dblclick zoomstart",()=>clearTimeout(clickT));
  if(touch)$("tip").textContent="Tap anything for details";
  setTimeout(()=>{$("tip").style.opacity=0;},7000);
  try{MAPS=await api("/api/maps");}catch(e){MAPS={surfaces:[]};}
  if(!MAPS.surfaces.length){$("map").innerHTML='<div class="empty">No map yet. It appears a few minutes after someone starts playing.</div>';return;}
  const sel=$("surface");
  sel.innerHTML=MAPS.surfaces.map(s=>`<option value="${s.index}">${esc(s.name)}${s.kind?` · ${esc(s.kind)}`:""}</option>`).join("");
  sel.parentNode.hidden=MAPS.surfaces.length<2;
  const h=location.hash.split("/");
  const want=h[0]==="#map"&&h[1]?+h[1]:MAPS.surfaces[0].index;
  sel.value=MAPS.surfaces.some(s=>s.index===want)?want:MAPS.surfaces[0].index;
  sel.onchange=()=>selectSurface(+sel.value,true);
  const pos=h[0]==="#map"&&h.length>=5&&h.slice(2,5).every(v=>v!==""&&isFinite(+v));
  if(pos)map.setView(ll(+h[2],+h[3]),+h[4]);  // a shared/refreshed link: view first, Leaflet needs it
  selectSurface(+sel.value,!pos);
  $("snap").oninput=()=>setSnap(+$("snap").value);
  $("play").onclick=togglePlay;
  for(const id of ["lyTags","lyPatches","lyStations","lyDead"])$(id).onchange=buildLabels;
  $("oreChoose").onclick=()=>{if(mobile())$("mapctl").classList.add("closed");oreSheet();};
  $("lyPlayers").onchange=e=>e.target.checked?markers.addTo(map):markers.remove();
  $("lyTrains").onchange=e=>e.target.checked?trainLayer.addTo(map):trainLayer.remove();
  $("lyAttacks").onchange=e=>e.target.checked?attackLayer.addTo(map):attackLayer.remove();
  $("lyPol").onchange=()=>loadOverlay("pol");
  $("lyHist").onchange=()=>loadOverlay("hist");
  $("lyNests").onchange=()=>loadOverlay("nests");
  // on phones the menu covers the map: close it when an overlay is switched on so the result is visible
  for(const id of ["lyPol","lyHist","lyNests","lyDef","lyRobo","lyChg"])$(id).addEventListener("change",e=>{if(e.target.checked&&mobile())$("mapctl").classList.add("closed");});
  $("lyDef").onchange=()=>loadOverlay("def");
  $("lyRobo").onchange=()=>loadOverlay("robo");
  $("lyKey").onchange=()=>loadOverlay("key");
  $("lyVeh").onchange=e=>e.target.checked?vehLayer.addTo(map):vehLayer.remove();
  // these two only look different below / above zoom -1 (short or full labels), so only redraw when that flips
  let wasFar=map.getZoom()<-1;
  map.on("zoomend",()=>{
    const far=map.getZoom()<-1;
    if(far===wasFar)return;
    wasFar=far;
    if($("lyKey").checked)loadOverlay("key",true);
    if($("lyNests").checked)loadOverlay("nests",true);
  });
  $("lyChg").onchange=()=>loadOverlay("chg");
  setupSearch();
  applyZoomStyle();loadLive();
}
/* overlays: pollution cloud, attack history, new builds (loaded when switched on) */
const heat=t=>{  // 0..1 -> brown/yellow -> red, like the in-game pollution view
  const a=[[122,90,32],[212,160,40],[214,56,44]], k=t<.5?0:1, f=t<.5?t*2:(t-.5)*2;
  return `rgb(${a[k].map((v,i)=>Math.round(v+(a[k+1][i]-v)*f)).join(",")})`;
};
const DRAWN={};
// true when this layer already shows exactly this data (at this level of detail); otherwise clears it for a redraw
function unchanged(kind,d,si){
  const sig=si+"|"+(map.getZoom()<-1)+"|"+JSON.stringify(kind==="def"?d.turrets:kind==="robo"?d.roboports:kind==="key"?d.key:d);
  const L_={nests:nestLayer,def:defLayer,robo:roboLayer,key:keyLayer}[kind];
  if(DRAWN[kind]===sig&&L_.getLayers().length)return true;
  L_.clearLayers();DRAWN[kind]=sig;return false;
}
async function loadOverlay(kind,quiet){
  const toast=quiet?()=>{}:window.toast;
  if(!map||!cur)return;
  const L_={pol:[polLayer,"lyPol"],hist:[histLayer,"lyHist"],chg:[chgLayer,"lyChg"],nests:[nestLayer,"lyNests"],
            def:[defLayer,"lyDef"],robo:[roboLayer,"lyRobo"],key:[keyLayer,"lyKey"]}[kind];
  const [layer,box]=L_;
  if(!$(box).checked){layer.clearLayers();DRAWN[kind]=null;layer.remove();return;}
  // these come with every new map version (once a minute while people play): only redraw when they changed
  const keepTill=["nests","def","robo","key"].includes(kind);
  if(!keepTill){layer.clearLayers();DRAWN[kind]=null;}
  layer.addTo(map);
  const si=cur.index;
  try{
    if(kind==="pol"){
      const d=await api(`/api/pollution?s=${si}`);
      if(!$(box).checked||cur.index!==si)return;
      const lm=Math.log(1+Math.max(1,d.max));
      for(const [cx,cy,v] of d.chunks){
        const t=Math.log(1+v)/lm;
        L.rectangle(L.latLngBounds(ll(cx*32,cy*32),ll(cx*32+32,cy*32+32)),{stroke:false,fillColor:heat(t),fillOpacity:.25+.4*t,interactive:true})
          .bindTooltip(`Pollution: ${fmt(v)}`,{sticky:true}).addTo(layer);
      }
      for(const [x,y,v] of d.nests){
        L.marker(ll(x,y),{icon:L.divIcon({className:"nest",iconSize:null}),keyboard:false,zIndexOffset:500})
          .bindPopup(`<div class="pop"><h3>Nest in the pollution cloud</h3><div class="muted small">Pollution is reaching it right now. ${Math.round(x)}, ${Math.round(y)}</div></div>`).addTo(layer);
      }
      if(!d.chunks.length)toast("No pollution data yet. It's measured every 5 minutes while someone plays.");
    }else if(kind==="nests"){
      const d=await snapApi(`/api/nests?s=${si}&snap=${curSnap.id}&t=${Math.floor(Date.now()/300000)}`);  // pollution part refreshes every 5 min
      if(!$(box).checked||cur.index!==si||unchanged(kind,d,si))return;
      const z=map.getZoom();
      // Space Age demolisher territories: the explored chunks of each, drawn as merged strips per chunk row
      for(const t of d.territories||[]){
        const who=t.units.map(([l,n])=>n>1?`${n}× ${esc(l)}`:esc(l)).join(", ")||"a demolisher";
        const pop=`<div class="pop"><h3>Demolisher territory</h3><div class="muted small">${who}. Attacks anything built inside.</div>`+
          `</div>`;
        const rows={};
        for(const [cx,cy] of t.chunks)(rows[cy]=rows[cy]||[]).push(cx);
        for(const [cy,xs] of Object.entries(rows)){
          xs.sort((a,b)=>a-b);
          for(let i=0;i<xs.length;){
            let j=i;while(j+1<xs.length&&xs[j+1]===xs[j]+1)j++;
            L.rectangle([ll(xs[i]*32,+cy*32),ll(xs[j]*32+32,+cy*32+32)],{stroke:false,fillColor:"#c2410c",fillOpacity:.22}).bindPopup(pop).addTo(layer);
            i=j+1;
          }
        }
        const m=L.marker(ll(t.x,t.y),{icon:L.divIcon({className:"basebadge",html:`<span>${esc(t.units[0]?.[0]||"Demolisher")}</span>`,iconSize:[0,0]}),keyboard:false,zIndexOffset:-100}).bindPopup(pop).addTo(layer);
        m._pri=60;
      }
      for(const b of d.bases){
        const hot=b.polluted, col=hot?"#ff3b2b":"#e2493b";
        const pop=`<div class="pop"><h3>Enemy base${hot?" · in your pollution":""}</h3>`+
          `<div class="row"><span>Nests</span><b>${b.nests}</b></div><div class="row"><span>Worms</span><b>${b.worms}</b></div><hr>`+
          b.kinds.map(([l,n])=>`<div class="row"><span>${esc(l)}</span><b>${n}</b></div>`).join("")+
          `<div class="muted small">${hot?"Pollution is reaching it right now. ":""}${Math.round(b.x)}, ${Math.round(b.y)}</div></div>`;
        if(b.hull&&b.hull.length>2)
          L.polygon(b.hull.map(q=>ll(q[0],q[1])),{color:col,weight:hot?2.5:1.5,dashArray:hot?null:"5 4",fillColor:col,fillOpacity:hot?.28:.14}).bindPopup(pop).addTo(layer);
        if(z>=-1)for(const [x,y,nest] of b.pts)
          L.circleMarker(ll(x,y),{radius:nest?4:2.5,color:"#000",weight:1,fillColor:nest?"#ff6a5a":"#c9a227",fillOpacity:1,interactive:false}).addTo(layer);
        const txt=z<-1?`${b.nests||b.worms}`:`${b.nests} nest${b.nests===1?"":"s"}${b.worms?` · ${b.worms} worm${b.worms===1?"":"s"}`:""}`;
        const m=L.marker(ll(b.x,b.y),{icon:L.divIcon({className:"basebadge"+(hot?" hot":""),html:`<span title="${b.nests} nests, ${b.worms} worms">${txt}</span>`,iconSize:[0,0]}),keyboard:false,zIndexOffset:hot?-50:-100})
          .bindPopup(pop).addTo(layer);
        m._pri=(hot?66:46)+Math.min(30,b.nests)/10;  // bases in the pollution beat stations; others beat ore labels; bigger first
      }
      declutter();
      const hotN=d.bases.filter(b=>b.polluted).length;
      const view=d.bases.filter(b=>map.getBounds().contains(ll(b.x,b.y)));
      const nt=(d.territories||[]).length, terr=nt?`${nt} demolisher ${nt===1?"territory":"territories"}`:"";
      toast(d.bases.length?`${d.bases.length} enemy bases${d.has_pollution?`, ${hotN} in your pollution`:""}${terr?", "+terr:""}${view.length?"":". None in view: zoom out to see them."}`:
            terr?`No enemy bases here, but ${terr}.`:"No enemy bases on the explored map.");
    }else if(kind==="def"||kind==="robo"||kind==="key"){
      const d=await snapApi(`/api/structures?s=${si}&snap=${curSnap.id}`);
      if(!$(box).checked||cur.index!==si||unchanged(kind,d,si))return;
      if(kind==="def"){
        for(const [x,y,r,k] of d.turrets){
          L.circle(ll(x,y),{radius:r,stroke:k==="art",color:"#ff9f1b",weight:1,dashArray:"6 5",fillColor:k==="art"?"#ff9f1b":"#ffb347",fillOpacity:k==="art"?.04:.12,interactive:false}).addTo(layer);
        }
        toast(d.turrets.length?`${fmt(d.turrets.length)} turrets. Darker means more turrets cover that spot.`:"No turrets on this map.");
      }else if(kind==="robo"){
        for(const [x,y,lr,cr] of d.roboports){
          L.rectangle(L.latLngBounds(ll(x-cr,y-cr),ll(x+cr,y+cr)),{stroke:false,fillColor:"#6fbf5a",fillOpacity:.1,interactive:false}).addTo(layer);
          L.rectangle(L.latLngBounds(ll(x-lr,y-lr),ll(x+lr,y+lr)),{color:"#ff9f1b",weight:1,fillColor:"#ff9f1b",fillOpacity:.1,interactive:false}).addTo(layer);
        }
        toast(d.roboports.length?`${fmt(d.roboports.length)} roboports. Green: robots build here. Orange: logistics.`:"No roboports on this map yet.");
      }else{
        const z=map.getZoom();
        for(const k of d.key){
          const imp=({"Rocket silo":90,"Space elevator":88,"Cargo rocket pad":86,"Reactor":80,"Core miner":75,"Landing pad":72,"Labs":68}[k.label]||65);
          const txt=z<-1?k.icon+(k.n>1?" "+k.n:""):`${k.icon} ${esc(k.label)}${k.n>1?` ×${k.n}`:""}`;
          const m=L.marker(ll(k.x,k.y),{icon:L.divIcon({className:"kb",html:`<span>${txt}</span>`,iconSize:[0,0]}),keyboard:false,zIndexOffset:400})
            .bindPopup(`<div class="pop"><h3>${k.icon} ${esc(k.label)}${k.n>1?` ×${k.n}`:""}</h3><div class="muted small">${Math.round(k.x)}, ${Math.round(k.y)}</div></div>`).addTo(layer);
          m._pri=imp+Math.min(20,k.n)/25;  // more important first, then bigger groups
        }
        declutter();
      }
    }else if(kind==="hist"){
      const d=await api(`/api/attackmap?s=${si}`);
      if(!$(box).checked||cur.index!==si)return;
      for(const [x,y,n,last] of d.cells){
        L.circle(ll(x,y),{radius:16+Math.sqrt(n)*12,color:"#e2493b",weight:1,dashArray:"4 3",fillColor:"#e2493b",fillOpacity:Math.min(.5,.12+n*.03)})
          .bindPopup(`<div class="pop"><h3>${n} attack${n>1?"s":""} here</h3><div class="muted small">Last one ${ago(last)}</div></div>`).addTo(layer);
      }
      if(!d.cells.length)toast("No attacks recorded yet.");
    }else{
      const d=await api(`/api/changes?s=${si}&snap=${curSnap.id}`);
      if(!$(box).checked||cur.index!==si)return;
      const mx=Math.max(1,...d.chunks.map(c=>c[2]));
      for(const [cx,cy,ad,rm] of d.chunks){
        if(ad<3&&rm<3)continue;
        const t=Math.min(1,Math.sqrt(ad/mx));
        L.rectangle(L.latLngBounds(ll(cx*32,cy*32),ll(cx*32+32,cy*32+32)),{color:ad>=rm?"#6fbf5a":"#e2493b",weight:1,fillColor:ad>=rm?"#6fbf5a":"#e2493b",fillOpacity:.15+.45*t})
          .bindTooltip(`${ad?`+${fmt(ad)} built`:""}${ad&&rm?" · ":""}${rm?`−${fmt(rm)} removed`:""}`,{sticky:true}).addTo(layer);
      }
      const prev=cur.snapshots.find(x=>x.id===d.prev);
      toast(prev?`Changes since day ${prev.day}`:"Nothing to compare with yet.");
    }
  }catch(e){}
}
let toastT=0;
window.toast=function(msg){const t=$("tip");t.textContent=msg;t.style.opacity=1;clearTimeout(toastT);toastT=setTimeout(()=>{t.style.opacity=0;},4000);}

/* search: tags, stations, ore patches on this surface */
function setupSearch(){
  const inp=$("mapSearch"), box=$("sres");
  const run=()=>{
    const q=inp.value.trim().toLowerCase();
    if(!q){box.hidden=true;return;}
    const res=[];
    for(const t of (LIVE?.tags||[]))if(t.s===cur.index&&t.text&&t.text.toLowerCase().includes(q))res.push([t.text,"Tag",t.x,t.y,1]);
    for(const st of (LIVE?.stations||[]))if(st.s===cur.index&&st.n&&st.n.toLowerCase().includes(q))res.push([st.n,"Station",st.x,st.y,1]);
    const ores=PATCHES.filter(p=>p.label.toLowerCase().includes(q)).sort((a,b)=>b.amount-a.amount).slice(0,8);
    for(const p of ores)res.push([p.label,`${p._amt?amt(p):fmt(p.tiles)+" tiles"} · ${p.fluid?"field":p.inf?"seam":"patch"}`,p.x,p.y,0]);
    box.innerHTML=res.slice(0,20).map((r,i)=>`<button data-i="${i}"><span>${esc(r[0])}</span><span class="muted small">${esc(r[1])}</span></button>`).join("")||'<div class="muted small" style="padding:8px 10px">Nothing found</div>';
    box.hidden=false;
    box.querySelectorAll("button").forEach(b=>b.onclick=()=>{
      const r=res[+b.dataset.i];box.hidden=true;inp.blur();
      if(mobile())$("mapctl").classList.add("closed");
      map.flyTo(ll(r[2],r[3]),Math.max(map.getZoom(),r[4]?1:-1));
    });
  };
  inp.oninput=run;inp.onfocus=run;
  inp.onkeydown=e=>{if(e.key==="Enter"){box.querySelector("button")?.click();}if(e.key==="Escape"){box.hidden=true;inp.blur();}};
  document.addEventListener("click",e=>{if(!e.target.closest(".search"))box.hidden=true;});
}

function applyZoomStyle(){
  const z=map.getZoom();
  map.getContainer().style.setProperty("--fs",Math.max(10,Math.min(15,12+z*1.5))+"px");
}
function selectSurface(idx,fit){
  cur=MAPS.surfaces.find(s=>s.index===idx);
  const n=cur.snapshots.length;
  $("snap").max=n-1;$("snap").value=n-1;$("tlBox").hidden=n<2;
  setSnap(n-1);
  if(fit){const b=cur.snapshots[n-1].bounds;map.fitBounds(L.latLngBounds(ll(b[0],b[1]),ll(b[2],b[3])).pad(-.15));}
  renderLive();saveHash();
  loadOverlay("pol",true);loadOverlay("hist",true);
}
function setSnap(i){
  const s=cur.snapshots[i];
  const layer=L.tileLayer(`/tiles/${s.id}/${cur.index}/{z}/{x}_{y}.png`,{tileSize:256,minNativeZoom:s.minz,maxNativeZoom:s.maxz,
    minZoom:-6,maxZoom:3,noWrap:true,errorTileUrl:BLANK,keepBuffer:4,
    bounds:L.latLngBounds(ll(s.bounds[0],s.bounds[1]),ll(s.bounds[2],s.bounds[3]))});
  const old=tileLayer;tileLayer=layer;layer.addTo(map);layer.bringToBack();
  if(old)layer.once("load",()=>map.removeLayer(old));
  $("snapLabel").textContent=`day ${s.day}`;
  curSnap=s;mapAge();
  $("snapAge").title=s.live?"The map keeps itself up to date while people play (new builds show up within a few minutes)"
    :"How the map looked at that time";
  curSnap=s;selLayer.clearLayers();loadPatches(s);
  $("lyChgL").hidden=i<1;if(i<1)$("lyChg").checked=false;
  loadOverlay("chg",true);loadOverlay("nests",true);loadOverlay("def",true);loadOverlay("robo",true);loadOverlay("key",true);
}
// New map versions (the live map publishes one about once a minute while people play): follow along when
// looking at the newest one; when scrubbing the timelapse, stay on the same frame.
async function refreshMaps(){
  if(!map||!cur||!curSnap||playing||view!=="map")return;
  let m;try{m=await api("/api/maps");}catch(e){return;}
  const nc=m.surfaces.find(s=>s.index===cur.index);
  if(!nc||!nc.snapshots.length)return;
  // don't swap the map under an open popup (try next time), unless the version shown has been deleted meanwhile
  if(map._popup&&map.hasLayer(map._popup)&&nc.snapshots.some(s=>s.id===curSnap.id))return;
  const lastId=a=>a.snapshots[a.snapshots.length-1].id;
  const atEnd=curSnap===cur.snapshots[cur.snapshots.length-1];
  const changed=lastId(nc)!==lastId(cur);
  MAPS=m;cur=nc;
  const n=cur.snapshots.length;
  $("snap").max=n-1;$("tlBox").hidden=n<2;
  let i=atEnd?n-1:cur.snapshots.findIndex(s=>s.id===curSnap.id);
  if(i<0)i=n-1;
  $("snap").value=i;
  if(changed&&(atEnd||cur.snapshots[i].id!==curSnap.id))setSnap(i);
  else{curSnap=cur.snapshots[i];mapAge();}
}
// "Map updated 35s ago" in the corner: counts up by itself between checks for a new map
function mapAge(){
  if(!curSnap)return;
  $("snapAge").textContent=curSnap.live?`Map updated ${ago(curSnap.ts)}`:`Map: ${ago(curSnap.ts)}`;
}
async function loadPatches(s){
  const req=++patchReq;
  let d;try{d=await api(`/api/patches?snap=${s.id}&s=${cur.index}`);}catch(e){return;}
  if(req!==patchReq)return;
  PATCHES=d.patches.map(p=>Object.assign(p,{_amt:d.has_amounts}));
  buildLabels();
}

/* labels: [priority, min zoom, html, class, x, y] -> decluttered so the important ones win */
let LABELS=new Map();
function buildLabels(){
  if(!map||!cur)return;
  dotLayer.clearLayers();
  const z=map.getZoom(), L_=[];
  if(($("lyTags").checked||$("lyDead").checked)&&LIVE){
    for(const t of (LIVE.tags||[]).filter(t=>t.s===cur.index)){
      if(t.corpse){
        if(!$("lyDead").checked)continue;
        const pin=info(mk(t.x+.5,t.y+.5,"g-dead",touch?30:22,"☠"),`${esc(t.text||"Someone")}'s corpse`,
          `<div class="muted small">${esc(t.text||"A player")} died here. Their items are on the body. ${Math.round(t.x)}, ${Math.round(t.y)}</div>`).addTo(dotLayer);
        L_.push([50,-99,`<span>☠ ${esc(t.text||"Someone")}'s body</span>`,"tag dead",t.x+.5,t.y+.5,pin]);
        continue;
      }
      if(!$("lyTags").checked||t.auto)continue;  // SE's own core-seam/planet tags: the Ores layer and surface menu cover them
      const pin=info(mk(t.x+.5,t.y+.5,"g-tag"+(t.icon_only?" minor":""),touch?32:22),esc(t.text||"Map tag"),
        `<div class="muted small">Map tag${t.by?` by ${esc(t.by)}`:""} · ${Math.round(t.x)}, ${Math.round(t.y)}</div>`).addTo(dotLayer);
      L_.push([t.icon_only?5:100,-99,`<span>${esc(t.text||"📍")}</span>`,"tag"+(t.icon_only?" minor":""),t.x+.5,t.y+.5,pin]);
    }
  }
  if($("lyStations").checked&&LIVE){
    for(const st of (LIVE.stations||[]).filter(t=>t.s===cur.index)){
      const pin=info(mk(st.x+.5,st.y+.5,"g-st",touch?28:18).setZIndexOffset(-700),esc(st.n),`<div class="muted small">Train stop · ${Math.round(st.x)}, ${Math.round(st.y)}</div>`).addTo(dotLayer);
      L_.push([60,-99,`<span>${esc(st.n)}</span>`,"st",st.x+.5,st.y+.5,pin]);
    }
  }
  if($("lyPatches").checked){
    for(const p of PATCHES){
      if(ORE_OFF.has(oreKey(p)))continue;
      // everything shows when there's room; where labels would overlap, the bigger patch wins
      const pri=p.inf?9:p.fluid?10+Math.min(40,p.tiles*2):10+Math.min(40,Math.log10(Math.max(1,p.amount))*4);
      const sw=p.color?`<i style="background:#${esc(p.color)}"></i>`:"";
      L_.push([pri,-99,`<span>${sw}${esc(p.label)}${p._amt?`<small>${amt(p)}</small>`:""}</span>`,"patch",p.x,p.y]);
    }
  }
  // labels that are still wanted are kept as they are (rebuilding thousands on every zoom is slow on big maps)
  const next=new Map();
  for(const [pri,minz,html,cls,x,y,pin] of L_){
    if(z<minz){if(pin)pin.remove();continue;}
    let k=`${cls}|${pri}|${x}|${y}|${html}`;
    while(next.has(k))k+="+";  // two identical labels on one spot each get their own
    let m=LABELS.get(k);
    if(m)LABELS.delete(k);
    else{
      // tag names sit above all pins, so a neighbour's pin never covers them; other names stay under the pins
      m=L.marker(ll(x,y),{icon:L.divIcon({className:"lbl "+cls,html,iconSize:[0,0],iconAnchor:[0,0]}),interactive:false,keyboard:false,zIndexOffset:pri>=100?900:-500});
      m._pri=pri;m.addTo(labelLayer);
    }
    m._pin=pin;
    next.set(k,m);
  }
  for(const m of LABELS.values())labelLayer.removeLayer(m);
  LABELS=next;
  declutter();
}
let declTimer=0;
// Runs right where labels are (re)built, in the same step, so a label is never drawn before its place is decided.
function declutter(){
  cancelAnimationFrame(declTimer);
  {
    const ms=[];
    for(const lay of [labelLayer,keyLayer,nestLayer])lay.eachLayer(m=>{const el=m._pri!=null&&m.getElement&&m.getElement();if(el)ms.push([m._pri,el,m._pin&&m._pin.getElement()]);});
    ms.sort((a,b)=>b[0]-a[0]);
    // reset, then measure everything in one go (one layout pass, not one per label)
    for(const [,el,pin] of ms){el.classList.remove("hide","under");el.firstElementChild.style.translate="";if(pin)pin.classList.remove("pinhide");}
    const rects=ms.map(([,el,pin])=>[el.firstElementChild.getBoundingClientRect(),pin&&(pin.firstElementChild||pin).getBoundingClientRect()]);
    // Decided for the whole map at once (not just what's on screen), so panning never changes anything.
    // Most important first. A name that would overlap first tries a spot just above / below / right / left.
    // Still no room: zoomed in it shows anyway (faded, underneath); only in the far-out overview does it hide.
    const allowOverlap=map.getZoom()>=0;
    // placed boxes go in a coarse screen grid, so each check only looks at its neighbours (not every label so far)
    const G=128, grid=()=>new Map(), kept=grid(), pins=grid();
    const cells=(q,f)=>{for(let gx=Math.floor((q.left-3)/G);gx<=Math.floor((q.right+3)/G);gx++)for(let gy=Math.floor((q.top-2)/G);gy<=Math.floor((q.bottom+2)/G);gy++)if(f(gx+","+gy))return true;return false;};
    const put=(g,q)=>cells(q,c=>{(g.get(c)||g.set(c,[]).get(c)).push(q);});
    const over=(q,g)=>cells(q,c=>(g.get(c)||[]).some(k=>q.left<k.right+3&&q.right>k.left-3&&q.top<k.bottom+2&&q.bottom>k.top-2));
    ms.forEach(([pri,el,pin],i)=>{
      const [r,pr]=rects[i];
      if(!r.width)return;
      const blocked=q=>over(q,kept)||(pri<100&&over(q,pins));  // pins of shown tags push other things aside, never another tag
      let at=null;
      if(!blocked(r))at=[0,0];
      else{
        // Step aside, but never detach from the spot it names: a moved name keeps its edge on that spot
        // (slides right/left/up/down by half its size). Station names flip to the other side of their square;
        // tag names only step up, staying above their pin.
        const w=r.width,h=r.height, cl=el.classList;
        const moves=pri>=100?[[0,-(h+3)]]
          :cl.contains("st")?[[-(w+18),0],[0,-(h/2+2)],[0,h/2+2]]
          :[[w/2-6,0],[-(w/2-6),0],[0,-(h/2-3)],[0,h/2-3]];
        for(const [dx,dy] of moves){
          const q={left:r.left+dx,right:r.right+dx,top:r.top+dy,bottom:r.bottom+dy};
          if(!blocked(q)){at=[dx,dy];break;}
        }
      }
      if(at){
        if(at[0]||at[1])el.firstElementChild.style.translate=`${at[0]}px ${at[1]}px`;
        put(kept,{left:r.left+at[0],right:r.right+at[0],top:r.top+at[1],bottom:r.bottom+at[1]});
        if(pr)put(pins,pr);
      }else if(allowOverlap)el.classList.add("under");
      else{el.classList.add("hide");if(pin)pin.classList.add("pinhide");}
    });
  }
}
