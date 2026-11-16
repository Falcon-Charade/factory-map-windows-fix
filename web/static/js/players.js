/* players.js: The Players tab: who is online, play time and deaths.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- players ---------------- */
const PSTATS=[["b","Builder","buildings placed",fmt],["k","Slayer","enemies killed",fmt],["c","Crafter","items hand-crafted",si],
  ["m","Deconstructor","things mined",fmt],["w","Walker","walked",v=>fmt(v/1000,1)+" km"],["dr","Driver","driven",v=>fmt(v/1000,1)+" km"]];
function renderPlayers(){
  const g=$("players");
  if(!OV||!OV.ok){g.innerHTML='<div class="card bevel wide empty">No player data yet.</div>';return;}
  const ps=OV.players;
  const live=n=>(LIVE?.players||[]).find(p=>p.n===n);
  const boards=PSTATS.map(([k,title,what,f])=>[title,ps.filter(p=>p.st[k]>0).sort((a,b)=>b.st[k]-a.st[k]).slice(0,3).map(p=>[p.name,f(p.st[k])]),what]).filter(b=>b[1].length);
  const shame=ps.filter(p=>p.deaths>0).sort((a,b)=>b.deaths-a.deaths).slice(0,3).map(p=>[p.name,"☠ "+fmt(p.deaths)]);
  if(shame.length)boards.push(["Hall of shame",shame,"deaths"]);
  g.innerHTML=`<div class="card bevel wide">
    <h2>Players <span class="sub">${OV.online.length?`${OV.online.length} online`:"nobody online"}</span></h2>
    <div class="plist2"><div class="prow colh"><span class="nm"></span><span class="h">played</span><span class="ls">last seen</span></div>${ps.map((p,i)=>`<button class="prow" data-i="${i}">
      <span class="nm"><span class="dot ${p.online?"on":""}"></span><span class="t">${esc(p.name)}</span></span>
      <span class="h">${p.hours<1?Math.round(p.hours*60)+" min":fmt(p.hours,1)+" h"}</span>
      <span class="ls">${p.online?"online":ago(p.last_seen)}</span></button>`).join("")}</div></div>
    ${boards.length?`<div class="card bevel wide"><h2>Leaderboards <span class="sub">since the mod started counting</span></h2>
      <div class="lb">${boards.map(([t,rows,what])=>`<div class="inset"><h3>${esc(t)} <span class="muted small">${esc(what)}</span></h3><ol>${rows.map(([n,v])=>`<li><span>${esc(n)}</span><b class="num">${v}</b></li>`).join("")}</ol></div>`).join("")}</div></div>`:""}`+
    settingsHtml()+footHtml();
  g.querySelectorAll(".prow").forEach(b=>b.onclick=()=>playerSheet(ps[+b.dataset.i]));
  g.querySelectorAll("input[data-pref]").forEach(i=>i.onchange=()=>{setPref(i.dataset.pref,i.checked);if(LIVE)renderAlert();});
}
// Settings card: the visitor's own switches, plus what the server admin chose to share (read-only)
function settingsHtml(){
  const sh=OV.share||{}, atkOff=sh.attacks===false;
  const sw=(k,label,hint,off)=>`<label class="pref"><input type="checkbox" data-pref="${k}" ${PREFS[k]&&!off?"checked":""} ${off?"disabled":""}>
    <span><b>${label}</b><br><span class="muted small">${hint}</span></span></label>`;
  const srv=(on,txt)=>`<li>${on===false?"✗":"✓"} ${txt}${on===false?" <span class=\"muted small\">(turned off)</span>":""}</li>`;
  return `<div class="card bevel wide"><details class="fold"><summary>Settings</summary>
    <h3 class="minor">On this browser</h3>
    ${sw("alertAttack","“Under attack” banner","Red notice at the top when enemies are destroying buildings.",atkOff)}
    ${sw("alertPower","“Low power” banner","Notice when machines slow down for lack of electricity.")}
    <h3 class="minor">Shared by this server</h3>
    <ul class="srvshare">${srv(sh.chat,"Chat in the event log")}${srv(sh.positions,"Where players are on the map")}${srv(sh.attacks,"Enemy attacks")}</ul>
    <p class="muted small">The server admin sets these in the game: Settings → Mod settings → Map.</p></details></div>`;
}
function playerSheet(p){
  const lp=(LIVE?.players||[]).find(x=>x.n===p.name);
  const st=p.st||{};
  const cells=[["Played",p.hours<1?Math.round(p.hours*60)+" min":fmt(p.hours,1)+" h"],["Deaths",fmt(p.deaths)],
    ...PSTATS.filter(([k])=>st[k]).map(([k,,lab,f])=>[lab[0].toUpperCase()+lab.slice(1),f(st[k])])];
  const bg=document.createElement("div");bg.className="sheet-bg";
  bg.innerHTML=`<div class="sheet bevel" role="dialog" aria-label="${esc(p.name)}">
    <h2><span class="dot ${p.online?"on":""}"></span>${esc(p.name)}${p.admin?' <span class="muted small">admin</span>':""}<button class="close" aria-label="Close">×</button></h2>
    <div class="muted small">${p.online?`Online now${p.surface?" on "+esc(p.surface):""}`:`Last seen ${ago(p.last_seen)}`}</div>
    <div class="stats">${cells.map(([l,v])=>`<div class="inset"><span class="muted small">${esc(l)}</span><b>${v}</b></div>`).join("")}</div>
    ${lp?`<button class="btn" id="goPlayer">Show on map</button>`:`<div class="muted small">Show on map works while they're online.</div>`}
  </div>`;
  const close=()=>bg.remove();
  bg.onclick=e=>{if(e.target===bg)close();};
  bg.querySelector(".close").onclick=close;
  document.addEventListener("keydown",function k(e){if(e.key==="Escape"){close();document.removeEventListener("keydown",k);}});
  document.body.appendChild(bg);
  if(lp)$("goPlayer").onclick=()=>{close();goToPlayer(p.name);};
}
function goToPlayer(name){
  show("map");
  setTimeout(()=>{
    const p=(LIVE?.players||[]).find(x=>x.n===name);if(!p||!map||!MAPS)return;
    if(cur?.index!==p.s&&MAPS.surfaces.some(s=>s.index===p.s)){$("surface").value=p.s;selectSurface(p.s,false);}
    map.flyTo(ll(p.x,p.y),Math.max(map.getZoom(),0));
    map.once("moveend",()=>{renderLive();KEEP.players?.get(name)?.openPopup();});
  },350);
}
