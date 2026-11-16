/* events.js: The Events tab: the event log with filters and paging.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- events ---------------- */
let feedLoaded=false, evTypes="", lastId=0;
const ICON={attack:"⚠",chat:"💬",death:"☠",research:"🔬",first:"★",join:"→",leave:"←",rocket:"🚀",cargo_rocket:"🚀",surface:"🪐",snapshot:"🗺"};
// Factorio rich text in chat: [gps=x,y,surface] becomes a map pin, [item=iron-plate] a name, colour tags go
function surfaceIndex(name){
  if(!name)return 1;
  for(const [i,s] of Object.entries(OV?.surfaces||{}))if(s.name===name||s.name?.toLowerCase()===name.toLowerCase())return i;
  return 1;
}
function chatHtml(m){
  return esc(m).replace(/\[gps=(-?[\d.]+),(-?[\d.]+)(?:,([^\]]+))?\]/g,(_,x,y,sf)=>
      `<a class="pin" href="#map/${surfaceIndex(sf)}/${Math.round(x)}/${Math.round(y)}/1" title="Show on the map">📍${Math.round(x)}, ${Math.round(y)}${sf&&sf!=="nauvis"?" "+sf:""}</a>`)
    .replace(/\[(?:item|entity|fluid|recipe|technology|virtual-signal|tile|planet|space-location|quality)=([^\],]+)[^\]]*\]/g,(_,n)=>`<b>${n.replace(/-/g," ")}</b>`)
    .replace(/\[\/?(?:color|font)[^\]]*\]/g,"");
}
function evText(e){
  const who=n=>`<span class="who">${esc(n)}</span>`;
  switch(e.type){
    case "chat":return `${who(e.player)}: <span class="msg">${chatHtml(e.message)}</span>`;
    case "join":return `${who(e.player)} joined the game`;
    case "leave":return `${who(e.player)} left the game${e.reason&&e.reason!=="quit"?` <span class="muted">(${esc(e.reason.replace(/_/g," "))})</span>`:""}`;
    case "death":return `${who(e.player)} died${e.killer?` to ${who(e.killer)}`:e.cause_label?` to <b>${esc(e.cause_label)}</b>`:""}${e.surface_name?` <span class="muted">on ${esc(e.surface_name)}</span>`:""}`;
    case "research":return `Research complete: <b>${esc(e.label)}</b>`;
    case "first":return `First ever <b>${esc(e.label)}</b> ${e.kind==="f"?"produced":"crafted"}`;
    case "rocket":return `Rocket launched${e.surface_name?` from ${esc(e.surface_name)}`:""}`;
    case "cargo_rocket":return `Cargo rocket launched${e.from?` from <b>${esc(e.from)}</b>`:""}${e.to?` to <b>${esc(e.to)}</b>`:""}`;
    case "surface":return `New surface: <b>${esc(e.name)}</b>`;
    case "attack":return `<b style="color:#ff7b6b">Attack</b>${e.near?` near <b>${esc(e.near)}</b>`:""}: ${esc(e.what_label)} destroyed${e.cause_label?` by ${esc(e.cause_label)}`:""}${e.surface_name&&e.surface!==1?` <span class="muted">on ${esc(e.surface_name)}</span>`:""} <a href="#map/${e.surface}/${e.x}/${e.y}/0" class="small">map</a>`;
    case "snapshot":return `Map snapshot captured <span class="muted">(${fmt(e.chunks)} chunks)</span>`;
    default:return esc(e.type);
  }
}
// Day headers instead of a date on every row; a player's messages sent close together share one row.
let feedDay="", feedChat=null;
const dayName=ts=>{const d=new Date(ts*1000),n=new Date(),y=new Date(n-864e5);
  return d.toDateString()===n.toDateString()?"Today":d.toDateString()===y.toDateString()?"Yesterday":d.toLocaleDateString([], {weekday:"short",month:"short",day:"numeric"});};
const hhmm=ts=>new Date(ts*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"});
function feedRows(events){
  let h="";
  for(const e of events){
    const day=dayName(e.ts);
    if(day!==feedDay){feedDay=day;feedChat=null;h+=`<li class="day">${day}</li>`;}
    if(e.type==="chat"&&feedChat&&feedChat.player===e.player&&feedChat.ts-e.ts<180){
      feedChat.parts.unshift(chatHtml(e.message));feedChat.ts=e.ts;continue;   // older message goes first in the row
    }
    if(feedChat){h=h.replace(`<!--chat${feedChat.id}-->`,feedChat.parts.join(" "));feedChat=null;}
    if(e.type==="chat"){
      feedChat={id:e.id,player:e.player,ts:e.ts,parts:[chatHtml(e.message)]};
      h+=`<li class="ev-chat" data-id="${e.id}"><span class="t">${hhmm(e.ts)}</span><span class="ic">${ICON.chat}</span><span><span class="who">${esc(e.player)}</span>: <span class="msg"><!--chat${e.id}--></span></span></li>`;
      continue;
    }
    h+=`<li class="ev-${esc(e.type)}" data-id="${e.id}"><span class="t">${hhmm(e.ts)}</span><span class="ic">${ICON[e.type]||"·"}</span><span>${evText(e)}</span></li>`;
  }
  if(feedChat){h=h.replace(`<!--chat${feedChat.id}-->`,feedChat.parts.join(" "));}
  return h;
}
async function loadEvents(reset){
  if(reset){lastId=0;$("feed").innerHTML="";feedDay="";}
  feedChat=null;  // a new page of events starts its own rows
  feedLoaded=true;
  let d;try{d=await api(`/api/events?types=${evTypes}${lastId?"&before="+lastId:""}`);}catch(e){return;}
  if(!d.events.length&&reset)$("feed").innerHTML='<li class="muted" style="display:block">Nothing here yet.</li>';
  $("feed").insertAdjacentHTML("beforeend",feedRows(d.events));
  if(d.events.length)lastId=d.events[d.events.length-1].id;
  $("more").style.display=d.events.length<100?"none":"block";
}
$("more").onclick=()=>loadEvents(false);
document.querySelectorAll("#evFilter button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#evFilter button").forEach(x=>x.classList.toggle("active",x===b));
  evTypes=b.dataset.t;loadEvents(true);
});
