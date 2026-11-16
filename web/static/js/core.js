/* core.js: Shared helpers (formatting, API calls), tab switching, the header and the footer.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(n,d=0)=>n==null?"—":Number(n).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:0});
const si=n=>n==null?"—":n>=1e9?(n/1e9).toFixed(1)+"G":n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e4?(n/1e3).toFixed(1)+"k":fmt(n,n<10?1:0);
function dur(s){s=Math.max(0,s);if(s<60)return Math.round(s)+"s";if(s<3600)return Math.round(s/60)+"m";if(s<86400)return (s/3600).toFixed(s<36000?1:0)+"h";return Math.round(s/86400)+"d";}
const ago=ts=>ts?dur(Date.now()/1000-ts)+" ago":"—";
const clock=ts=>{const d=new Date(ts*1000),n=new Date();return d.toDateString()===n.toDateString()?d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}):d.toLocaleDateString([], {month:"short",day:"numeric"})+" "+d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});};
async function api(p){const r=await fetch(p,{cache:"no-store"});if(!r.ok)throw new Error(r.status);return r.json();}
const SNAPC=new Map();  // per-snapshot overlay data never changes, so zooming doesn't re-download it
async function snapApi(p){if(!SNAPC.has(p)){if(SNAPC.size>24)SNAPC.delete(SNAPC.keys().next().value);SNAPC.set(p,api(p).catch(e=>{SNAPC.delete(p);throw e;}));}return SNAPC.get(p);}

/* ---------------- viewer settings (this browser only, Players tab) ---------------- */
// Each visitor's own choices, kept in localStorage. Defaults: everything on.
const PREF_DEFAULTS={alertAttack:true,alertPower:true};
let PREFS={...PREF_DEFAULTS};
try{Object.assign(PREFS,JSON.parse(localStorage.getItem("fm-prefs")||"{}"));}catch(e){}
function setPref(k,v){PREFS[k]=v;try{localStorage.setItem("fm-prefs",JSON.stringify(PREFS));}catch(e){}}

/* ---------------- tabs ---------------- */
let view="";
function show(v){
  if(!["map","factory","events","players"].includes(v))v="map";
  view=v;
  document.querySelectorAll(".view").forEach(e=>e.classList.toggle("active",e.id==="v-"+v));
  document.querySelectorAll("nav button").forEach(b=>b.classList.toggle("active",b.dataset.view===v));
  if(v==="map"){initMap();setTimeout(()=>{map&&map.invalidateSize();refreshMaps();},50);}
  if(v==="events"&&!feedLoaded)loadEvents(true);
  if(v==="factory")renderFactory();
  if(v==="players")renderPlayers();
  if(!location.hash.startsWith("#"+v))history.replaceState(null,"","#"+v);
}
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>show(b.dataset.view));

function footHtml(){
  const parts=[];
  if(FOOT.copyright)parts.push(`<span>© ${esc(FOOT.copyright)}</span>`);
  if(FOOT.version)parts.push(`<span>${esc(FOOT.mod)} v${esc(FOOT.version)}</span>`);
  if(/^https?:\/\//.test(FOOT.source||""))parts.push(`<span><a href="${esc(FOOT.source)}" target="_blank" rel="noopener">Source on GitHub</a></span>`);
  return parts.length?`<footer class="sitefoot">${parts.join("")}</footer>`:"";
}

/* ---------------- overview / header ---------------- */
let OV=null;
async function loadOverview(){
  try{OV=await api("/api/overview");}catch(e){OV=null;}
  const st=$("status");
  if(!OV||!OV.ok){st.innerHTML='<span class="dot off"></span>no data yet';return;}
  const n=OV.online.length;
  st.innerHTML=OV.live
    ?(n?`<span class="dot on"></span>${n} online`:'<span class="dot idle"></span>running')
    :'<span class="dot idle"></span>paused';
  st.title=OV.live?"Server is running":"The server pauses when nobody is playing";
  const pos=$("posOff");if(pos)pos.hidden=OV.share?.positions!==false;  // the admin turned position sharing off
  $("hstats").innerHTML=`Day <b>${fmt(OV.day)}</b>`+
    (OV.research.label?` · 🔬 <b>${esc(OV.research.label)}</b> ${Math.round((OV.research.progress||0)*100)}%`:"");
  if(view==="factory")renderFactory();
  if(view==="players")renderPlayers();
}
