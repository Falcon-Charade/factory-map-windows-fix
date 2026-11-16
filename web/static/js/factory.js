/* factory.js: The Factory tab. A summary strip on top (each tile jumps to its card), then research and science,
   power and pollution, what's running short, production, base size, enemies and space.
   Loaded as a plain script after the ones before it in index.html; top-level names are shared between the files. */

/* ---------------- factory ---------------- */
const fmtW=w=>w>=1e9?fmt(w/1e9,2)+" GW":w>=1e6?fmt(w/1e6,1)+" MW":w>=1e3?fmt(w/1e3,0)+" kW":fmt(w,0)+" W";
const durMin=m=>m<1?"under a minute":m<90?`${Math.round(m)} min`:m<48*60?`${fmt(m/60,1)} h`:`${fmt(m/1440,1)} days`;
const signed=(n,f=si)=>(n>0?"+":n<0?"−":"")+f(Math.abs(n));
// A plain list: name, amount, and its share of the total (bars scaled to the biggest row said nothing).
function shares(rows,val,lab,total,head,title){
  total=total||rows.reduce((a,x)=>a+val(x),0)||1;
  const h=title?`<h3 class="minor lhead"><span>${title}</span><span class="cols">${head?`<span>${head}</span>`:""}<span class="pct">share</span></span></h3>`:"";
  return `${h}<dl class="kv shares">${rows.map(x=>`<dt>${esc(x.label)}</dt><dd><span class="num">${lab(x)}</span><span class="pct">${Math.round(val(x)/total*100)}%</span></dd>`).join("")}</dl>`;
}
const stat=(big,label,cls="")=>`<div><div class="big num ${cls}">${big}</div><div class="muted">${label}</div></div>`;

function powerMax(pw){return pw&&pw.cap_w?pw.cap_w+(pw.solar_w||0):0;}
function powerLow(pw){return pw&&pw.probes&&pw.low>=Math.max(3,pw.probes*.25);}
function powerHtml(pw,hasRate){
  if(!pw)return "";
  const max=powerMax(pw), used=hasRate?pw.made_w:0, pct=max&&used?used/max*100:null;
  return `<div class="card bevel" id="powerCard">
    <h2>Power ${powerLow(pw)?'<span class="badbox">· low power</span>':""}</h2>
    <div class="stat-row">
      ${stat(used?fmtW(used):"—","consumed")}
      ${max?stat(used?fmtW(Math.max(0,max-used)):"—","spare"):""}
      ${max?stat(fmtW(max),`max${pw.solar_w?` <span title="${fmtW(pw.solar_w)} of it is solar, daytime only">(incl. solar)</span>`:""}`):""}
    </div>
    ${pct!=null?`<div class="bar inset" style="margin:2px 0 4px"><i style="width:${Math.min(100,pct)}%;background:${pct>90?"#e2493b":pct>70?"#f2a33b":"#f2d14b"}"></i><span>${Math.round(pct)}% of max</span></div>
    <div class="muted small">Max: every power plant of these kinds at full output, if fuelled. A best case, so spare may be less. Accumulators aren't counted.</div>`:""}
    ${pw.probes&&powerLow(pw)?`<div class="warnbox">${pw.low} of ${pw.probes} checked machines are short of power.</div>`:""}
    ${pw.sources.length&&used?`${shares(pw.sources,x=>x.w,x=>fmtW(x.w),pw.made_w,"power","Made by")}`:""}
    ${pw.consumers.length&&used?`${shares(pw.consumers.slice(0,6),x=>x.w,x=>fmtW(x.w),pw.used_w,"power","Biggest consumers")}`:""}
    <h3 class="minor">Consumed over time <span class="sub">dashed line: max</span></h3>
    <div class="chartbox"><canvas id="chPow"></canvas></div>
  </div>`;
}
function pollutionHtml(po,hasRate){
  if(!po)return "";
  const net=po.made_pm-po.absorbed_pm, live=hasRate&&po.made_pm;
  return `<div class="card bevel" id="polCard">
    <h2>Pollution</h2>
    <div class="stat-row">
      ${stat(live?si(po.made_pm):"—","made /min")}
      ${stat(live?si(po.absorbed_pm):"—","absorbed /min")}
      ${po.air!=null?stat(si(po.air),"in the air now"):""}
    </div>
    ${live?`<div style="margin-bottom:4px">${net>0?`Cloud <b class="badbox">growing</b> by ${si(net)}/min`:`Cloud <b class="okbox">shrinking</b> by ${si(-net)}/min`}</div>`:""}
    ${po.sources.length&&live?`${shares(po.sources.slice(0,6),x=>x.pm,x=>si(x.pm),po.made_pm,"per min","Biggest polluters")}`:""}
    ${po.absorbers.length&&live?`${shares(po.absorbers.slice(0,4),x=>x.pm,x=>si(x.pm),po.absorbed_pm,"per min","Absorbed by")}`:""}
    ${(po.other||[]).map(o=>`<h3 class="minor">${esc(o.label)} <span class="sub">other planets</span></h3>
      <dl class="kv"><dt>Made /min</dt><dd>${o.made_pm?si(o.made_pm):"—"}</dd><dt>Absorbed /min</dt><dd>${o.absorbed_pm?si(o.absorbed_pm):"—"}</dd>
      ${o.air!=null?`<dt>In the air now</dt><dd>${si(o.air)}</dd>`:""}${o.sources.map(x=>`<dt>${esc(x.label)}</dt><dd>${si(x.pm)}</dd>`).join("")}</dl>`).join("")}
    <button class="btn ghost" style="margin-top:10px" id="polMapBtn">Show the cloud on the map</button>
    <h3 class="minor">Made /min over time</h3>
    <div class="chartbox"><canvas id="chPol"></canvas></div>
  </div>`;
}
// weight of one spawn definition at evolution e (piecewise linear between its points, clamped at the ends)
function spawnWeight(pts,e){
  if(!pts.length)return 0;
  if(e<=pts[0][0])return pts[0][1];
  for(let i=1;i<pts.length;i++)if(e<=pts[i][0]){const [a,wa]=pts[i-1],[b,wb]=pts[i];return wa+(wb-wa)*(e-a)/(b-a||1);}
  return pts[pts.length-1][1];
}
// The evolution where a unit starts to spawn: just after its last zero-weight point (tables may begin with zeros).
function spawnStart(pts){
  const i=pts.findIndex(p=>p[1]>0);
  if(i<0)return null;
  return i===0?pts[0][0]:pts[i-1][0];
}
function evoHtml(ev,e){
  if(!ev||e==null)return "";
  const parts=[["Pollution",ev.pollution,"#9b7a3c"],["Time",ev.time,"#6b8fb3"],["Nests destroyed",ev.spawners,"#e2493b"]].filter(p=>p[1]>0);
  const sum=parts.reduce((a,p)=>a+p[1],0)||1;
  // what attacks are made of right now: both spawner types, each normalised, then averaged
  const mix={}, spawners=Object.values(ev.spawn||{});
  let next=null;
  for(const units of spawners){
    const w=units.map(u=>[u,spawnWeight(u.p,e)]), tot=w.reduce((a,x)=>a+x[1],0)||1;
    for(const [u,x] of w){if(x>0)mix[u.label]=(mix[u.label]||0)+x/tot/spawners.length;
      else{const at=spawnStart(u.p);if(at!=null&&at>e&&(!next||at<next[1]-1e-9))next=[[u.label],at];
        else if(next&&at!=null&&Math.abs(at-next[1])<1e-9&&!next[0].includes(u.label))next[0].push(u.label);}}
  }
  const mixRows=Object.entries(mix).sort((a,b)=>b[1]-a[1]).filter(x=>x[1]>=.005);
  return `<h3 class="minor">Evolution comes from</h3>
    <div class="stack inset">${parts.map(p=>`<i style="width:${p[1]/sum*100}%;background:${p[2]}" title="${p[0]}"></i>`).join("")}</div>
    <div class="legend">${parts.map(p=>`<span><i style="background:${p[2]}"></i>${p[0]} <b>${Math.round(p[1]/sum*100)}%</b></span>`).join("")}</div>
    ${mixRows.length?`${shares(mixRows.map(([l,x])=>({label:l,x})),r=>r.x,()=>"",1,"","Attacks are made of")}`:""}
    ${next?`<div class="muted small" style="margin-top:6px">${esc(next[0].map((l,i)=>(i?l.toLowerCase():l)+"s").join(" and "))} start showing up at ${Math.round(next[1]*100)}% evolution.</div>`:""}
`;
}

// Building counts arrive per entity type: give them names people know and merge the ones that belong together.
const COUNT_GROUPS={"_all":"Everything","transport-belt":"Belts","underground-belt":"Undergrounds","splitter":"Splitters",
  "loader":"Loaders","loader-1x1":"Loaders","inserter":"Inserters","assembling-machine":"Assemblers","furnace":"Furnaces",
  "mining-drill":"Miners","lab":"Labs","beacon":"Beacons","roboport":"Roboports","electric-pole":"Power poles",
  "solar-panel":"Solar panels","accumulator":"Accumulators","generator":"Steam engines & turbines","boiler":"Boilers",
  "reactor":"Reactors","burner-generator":"Burner generators","electric-energy-interface":"Power plants (modded)",
  "pipe":"Pipes","pipe-to-ground":"Pipes","pump":"Pumps","offshore-pump":"Offshore pumps","storage-tank":"Tanks",
  "straight-rail":"Rails","curved-rail-a":"Rails","curved-rail-b":"Rails","half-diagonal-rail":"Rails","legacy-straight-rail":"Rails",
  "legacy-curved-rail":"Rails","elevated-straight-rail":"Rails","elevated-curved-rail-a":"Rails","elevated-curved-rail-b":"Rails",
  "elevated-half-diagonal-rail":"Rails","rail-ramp":"Rails","rail-support":"Rails",
  "rail-signal":"Rail signals","rail-chain-signal":"Rail signals","train-stop":"Train stops","locomotive":"Locomotives",
  "cargo-wagon":"Cargo wagons","fluid-wagon":"Fluid wagons","artillery-wagon":"Artillery wagons",
  "container":"Chests","logistic-container":"Logistic chests","wall":"Walls","gate":"Gates","ammo-turret":"Gun turrets",
  "electric-turret":"Laser turrets","fluid-turret":"Flamethrowers","artillery-turret":"Artillery","radar":"Radars",
  "lamp":"Lamps","rocket-silo":"Rocket silos","spider-vehicle":"Spidertrons","car":"Cars & tanks",
  "constant-combinator":"Combinators","arithmetic-combinator":"Combinators","decider-combinator":"Combinators","selector-combinator":"Combinators",
  "power-switch":"Combinators","programmable-speaker":"Speakers","land-mine":"Land mines","simple-entity-with-force":"Other placed objects",
  "simple-entity-with-owner":"Other placed objects","heat-pipe":"Heat pipes","agricultural-tower":"Agricultural towers",
  "asteroid-collector":"Asteroid collectors","thruster":"Thrusters","cargo-landing-pad":"Landing pads","cargo-bay":"Cargo bays",
  "lightning-attractor":"Lightning rods","fusion-reactor":"Fusion reactors","fusion-generator":"Fusion generators"};
const humanType=t=>{const s=t.replace(/-/g," ");return s[0].toUpperCase()+s.slice(1);};
function groupCounts(c){
  const out={};
  for(const [k,v] of Object.entries(c||{})){const g=COUNT_GROUPS[k]||humanType(k);out[g]=(out[g]||0)+v;}
  return out;
}

const charts={};
// Hover: every line's value at the time under the mouse (not just the one nearest point, which the Max line
// usually won). Stepped lines show the value in force then (the last point at or before it).
if(window.Chart&&!Chart.Interaction.modes.atX)Chart.Interaction.modes.atX=function(chart,e,opts,useFinal){
  const pos=Chart.helpers.getRelativePosition(e,chart), out=[];
  if(pos.x<chart.chartArea.left||pos.x>chart.chartArea.right)return out;
  chart.data.datasets.forEach((ds,di)=>{
    const meta=chart.getDatasetMeta(di);if(meta.hidden||!chart.isDatasetVisible(di))return;
    let best=-1,bd=Infinity;
    meta.data.forEach((el,i)=>{
      const p=ds.data[i];if(!p||p.y==null)return;
      const x=useFinal?el.getProps(["x"],true).x:el.x;
      if(ds.stepped){if(x<=pos.x+1&&pos.x-x<bd){bd=pos.x-x;best=i;}}
      else{const d=Math.abs(x-pos.x);if(d<bd){bd=d;best=i;}}
    });
    if(best<0&&ds.stepped)best=0;
    if(best>=0)out.push({element:meta.data[best],datasetIndex:di,index:best});
  });
  return out;
};
function lineChart(id,datasets,yfmt,opts={}){
  const el=$(id);if(!el)return;
  if(charts[id]){charts[id].destroy();delete charts[id];}
  el.parentNode.querySelector(".nochart")?.remove();
  if(!datasets.some(d=>d.data.length>1)){
    el.style.display="none";el.parentNode.classList.add("nodata");
    el.insertAdjacentHTML("afterend",'<div class="nochart">📈 A graph appears here after about 20 minutes of play.</div>');
    return;
  }
  el.style.display="";el.parentNode.classList.remove("nodata");
  if(datasets.length===1&&datasets[0].fill==null)Object.assign(datasets[0],{fill:"origin",backgroundColor:datasets[0].borderColor+"22"});
  for(const d of datasets){  // gaps over 40 min mean nobody played: leave them empty
    if(d.stepped)continue;
    const out=[];
    for(const p of d.data){const q=out[out.length-1];if(q&&q.y!=null&&p.x-q.x>2400)out.push({x:(p.x+q.x)/2,y:null});out.push(p);}
    d.data=out;
  }
  charts[id]=new Chart(el,{type:"line",data:{datasets},options:{responsive:true,maintainAspectRatio:false,animation:false,parsing:false,
    interaction:{mode:"atX",intersect:false},
    plugins:{legend:{display:!!opts.legend,labels:{color:"#cfc8bc",boxWidth:10,font:{size:11}}},tooltip:{callbacks:{title:it=>clock(it.find(i=>!i.dataset.stepped)?.parsed.x??it[0].parsed.x),label:it=>`${it.dataset.label}: ${yfmt?yfmt(it.parsed.y):fmt(it.parsed.y,1)}`}}},
    scales:{x:{type:"linear",ticks:{color:"#8d877e",maxTicksLimit:6,callback:function(v){const span=this.chart.scales.x.max-this.chart.scales.x.min, d=new Date(v*1000);
      return span<2*86400?d.toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"}):d.toLocaleDateString([], {month:"short",day:"numeric"});}},grid:{color:"#ffffff10"}},
            y:{beginAtZero:opts.zero!==false,max:opts.max,ticks:{color:"#8d877e",callback:v=>yfmt?yfmt(v):si(v)},grid:{color:"#ffffff10"}}},
    elements:{point:{radius:0},line:{borderWidth:2,tension:.25}}}});
}
const PALETTE=["#ff9f1b","#6fbf5a","#58a6d6","#e2493b","#c77dff","#f2d14b","#4fd1c5","#ff7eb6","#a0a0a0","#9be15d","#7aa2ff","#ffb38a"];
let histRange="7d", itemSel="", itemLabel="", prodFilter="", countsAll=false, killsOpen=false;

// Research: what's being researched and how long it takes, what's queued, what finished lately, which packs labs eat.
let recentRes=null, recentResT=0;
function loadRecentResearch(){
  if(Date.now()-recentResT<60000)return;
  recentResT=Date.now();
  api("/api/events?types=research").then(d=>{recentRes=d.events.slice(0,5);const el=$("resRecent");if(el)el.innerHTML=recentHtml();}).catch(()=>{});
}
const recentHtml=()=>recentRes&&recentRes.length?`<h3 class="minor">Recently finished</h3><dl class="kv">${recentRes.map(e=>`<dt>${esc(e.label)}</dt><dd class="muted">${ago(e.ts)}</dd>`).join("")}</dl>`:"";
function researchHtml(hasRate){
  const r=OV.research, pct=Math.round((r.progress||0)*100), done=r.total?r.researched/r.total*100:0;
  const packs=OV.science.filter(s=>hasRate?s.used_pm>0:s.made>0), val=s=>hasRate?s.used_pm:s.made, mx=Math.max(1e-9,...packs.map(val));
  const cost=r.ing&&r.ing.length?`${fmt(r.units)} × ${r.ing.map(i=>`${i.a>1?i.a+" ":""}${esc(i.label)}`).join(" + ")}`:"";
  const eta=r.eta_min!=null?`About <b>${durMin(r.eta_min)}</b> left`:hasRate&&!OV.rates_old?`<span class="badbox">Stalled</span>: labs aren't getting science`:"";
  return `<div class="card bevel" id="resCard">
    <h2>Research <span class="sub">${fmt(r.researched)} of ${fmt(r.total)} done (${Math.round(done)}%)</span></h2>
    ${r.label?`<div class="resnow"><b>${esc(r.label)}</b><span class="num">${pct}%</span></div>
      <div class="bar inset"><i style="width:${pct}%"></i></div>
      ${eta||cost?`<div class="small" style="margin-top:4px">${eta}${eta&&cost?" · ":""}<span class="muted">${cost}</span></div>`:""}`
      :'<div class="warnbox">Nothing is being researched.</div>'}
    ${r.queue.length>1?`<h3 class="minor">Up next</h3><div class="queue">${r.queue.slice(1).map(q=>`<span>${esc(q.label)}</span>`).join("")}</div>`:""}
    <div id="resRecent">${recentHtml()}</div>
    ${packs.length?shares(packs,val,s=>hasRate?fmt(s.used_pm,1):si(s.made),0,hasRate?"per min":"total",hasRate?"Science packs used":"Science packs made")
      :`<h3 class="minor">Science packs</h3><div class="muted small">${OV.science.length?"Labs aren't using any science right now.":"No science made yet."}</div>`}
    <h3 class="minor">Science packs used over time</h3>
    <div class="chartbox"><canvas id="chSci"></canvas></div>
  </div>`;
}

// Running short: how much of what's used is made, how much more is needed, and whether it lasts.
function shortKind(x){
  if(!x.long)return "";
  return x.long.used_pm>x.long.made_pm*1.05?"lasting":"blip";
}
function sortedShort(){
  const rank={lasting:0,"":1,blip:2};
  return (OV.shortages||[]).slice().sort((a,b)=>rank[shortKind(a)]-rank[shortKind(b)]||(b.used_pm-b.made_pm)-(a.used_pm-a.made_pm));
}
function shortHtml(hasRate){
  const list=sortedShort();
  const row=x=>{
    const k=shortKind(x), miss=x.used_pm-x.made_pm, cover=x.used_pm?Math.min(1,x.made_pm/x.used_pm):0, L=x.long;
    const need=x.made_pm<=0?`<b>Not being made</b> right now: everything used comes from storage.`:
      x.used_pm/x.made_pm>=1.5?`To keep up, make <b>${fmt(x.used_pm/x.made_pm,1)}×</b> as much (+${fmt(miss,1)}/min).`:`To keep up, make <b>${Math.round((x.used_pm/x.made_pm-1)*100)}%</b> more (+${fmt(miss,1)}/min).`;
    const longTxt=!L?"":k==="lasting"?`Also short over the last ${fmt(L.hours,1)} h of play.`
      :"";
    return `<div class="srow ${k}" data-item="${esc(x.name)}" data-kind="${x.kind}" data-label="${esc(x.label)}">
      <div class="shead"><b>${esc(x.label)}</b>${x.kind==="f"?' <span class="muted small">fluid</span>':""}
        ${k==="lasting"?'<span class="tag bad">lasting</span>':""}
        <span class="num smiss">−${fmt(miss,1)}/min</span></div>
      <div class="bar inset" title="Share of what's used that is being made"><i style="width:${cover*100}%"></i><span>${fmt(x.made_pm,1)} of ${fmt(x.used_pm,1)} /min made</span></div>
      <div class="small">${need}</div>
      ${longTxt?`<div class="small muted">${longTxt}</div>`:""}
    </div>`;
  };
  const real=list.filter(x=>shortKind(x)!=="blip");  // a dip that's fine over a longer time isn't a problem
  if(!hasRate||!real.length)return "";
  return `<div class="card bevel wide" id="shortCard">
    <h2>Running short <span class="sub">used faster than made, so it comes out of storage</span></h2>
    <div class="slist">${real.map(row).join("")}</div>
  </div>`;
}

// One tile of the summary strip; clicking it scrolls to the card with the details.
const tile=(target,label,big,sub,cls="")=>`<button class="ovt inset ${cls}" data-go="${target}"><span class="muted small">${label}</span><b class="num">${big}</b><span class="small">${sub}</span></button>`;
function summaryHtml(hasRate){
  const r=OV.research, pw=OV.power, po=OV.pollution, max=powerMax(pw);
  const sciTotal=OV.science.reduce((a,s)=>a+(s.used_pm||0),0);
  const used=hasRate&&pw?pw.made_w:0, pct=max&&used?Math.round(used/max*100):null;
  const net=po&&hasRate&&po.made_pm?po.made_pm-po.absorbed_pm:null;
  return `<div class="wide ovstrip">
    ${tile("resCard","Researching",r.label?`${Math.round((r.progress||0)*100)}%`:"Idle",r.label?esc(r.label):`${fmt(r.researched)} of ${fmt(r.total)} done`,r.label?"":"warn")}
    ${tile("resCard","Science",hasRate?fmt(sciTotal,0):"—",hasRate?"packs used /min":"—",hasRate&&!sciTotal&&r.label?"warn":"")}
    ${pw?tile("powerCard","Power",used?fmtW(used):"—",pct!=null?`${pct}% of ${fmtW(max)} max`:"consumed",powerLow(pw)||pct>90?"bad":""):""}
    ${po?tile("polCard","Pollution",net==null?"—":signed(net)+"/min",net==null?"cloud":net>0?"cloud growing":"cloud shrinking",net>0?"warn":""):""}
    ${(()=>{const real=sortedShort().filter(x=>shortKind(x)!=="blip");
      if(!hasRate)return tile("prodCard","Running short","—","—");
      return real.length?tile("shortCard","Running short",fmt(real.length),esc(real.slice(0,2).map(x=>x.label).join(", ")),real.some(x=>shortKind(x)==="lasting")?"bad":"warn")
        :tile("prodCard","Running short","None","everything keeps up");})()}
    ${tile("enemyCard","Evolution",OV.evolution!=null?(OV.evolution*100).toFixed(1)+"%":"—",`${si(OV.kills.killed)} enemies killed`)}
  </div>`;
}

function renderFactory(){
  const g=$("factory");
  if(!OV||!OV.ok){g.innerHTML='<div class="card bevel wide empty">No stats yet. They appear about a minute after the server starts with the mod.</div>';return;}
  if(document.activeElement&&document.activeElement.id==="itemSearch")return;  // don't redraw under someone typing
  const r=OV.research, pct=Math.round((r.progress||0)*100);
  const hasRate=!!OV.rate_window_min;
  const mins=Math.min(10,Math.max(1,Math.round(OV.rate_window_min||0)));
  const rateNote=!hasRate?"Per-minute numbers show up after a few minutes of play.":
    !OV.live?"Nobody is playing, so the server is paused. Per-minute numbers are from the last time someone played.":
    OV.rates_old?"Server just started. Per-minute numbers are from before, until there are a few minutes of new data.":
    `Per-minute numbers are averaged over the last ${mins} minutes.`;
  const sciVal=s=>hasRate?s.used_pm:s.made, maxSci=Math.max(1e-9,...OV.science.map(sciVal));
  const counts=groupCounts(OV.counts), then=groupCounts(OV.counts_then), hadThen=(OV.counts_then||{})._all!=null;  // only compare with a sample counted the same way
  const countRows=Object.entries(counts).filter(([k])=>k!=="Everything").sort((a,b)=>b[1]-a[1]);
  const shown=countsAll?countRows:countRows.slice(0,15);
  const se=OV.se, short=OV.shortages||[];
  const q=prodFilter.toLowerCase(), prodRows=OV.top.filter(t=>!q||t.label.toLowerCase().includes(q));
  g.innerHTML=`
  <div class="wide factbar"><span>${rateNote}</span><label>All graphs on this tab show <select id="histRange"><option value="24h">the last 24 hours</option><option value="7d">the last 7 days</option><option value="30d">the last 30 days</option><option value="all">all time</option></select></label></div>
  ${summaryHtml(hasRate)}
  ${shortHtml(hasRate)}
  ${researchHtml(hasRate)}
  ${powerHtml(OV.power,hasRate)}
  ${pollutionHtml(OV.pollution,hasRate)}
  <div class="card bevel" id="enemyCard">
    <h2>Enemies</h2>
    <div class="stat-row">
      ${stat(OV.evolution!=null?(OV.evolution*100).toFixed(1)+"%":"—","evolution"+((OV.evo_planets||[]).length>1?" (Nauvis)":""))}
      ${stat(si(OV.kills.killed),"killed")}
      ${stat(si(OV.kills.lost),'<span title="Buildings, robots and players destroyed">we lost</span>')}
    </div>
    ${(OV.evo_planets||[]).length>1?`<h3 class="minor">Evolution per planet</h3><dl class="kv">${OV.evo_planets.map(p=>`<dt>${esc(p.name)}</dt><dd>${(p.evo*100).toFixed(1)}%</dd>`).join("")}</dl>`:""}
    ${evoHtml(OV.evo,OV.evolution)}
    ${OV.kills.top.length?`<details class="dmore" id="killsMore"${killsOpen?" open":""}><summary><span>Killed so far</span><span class="dmeta">by kind of enemy</span><span class="dbtn">${killsOpen?"Hide":"Show all"}</span></summary><dl class="kv">${OV.kills.top.map(k=>`<dt>${esc(k.label)}</dt><dd>${fmt(k.n)}</dd>`).join("")}</dl></details>`:""}
    <h3 class="minor">Evolution over time</h3>
    <div class="chartbox"><canvas id="chEvo"></canvas></div>
  </div>
  <div class="card bevel wide" id="prodCard">
    <h2>Production <span class="sub">click a row for its graph</span></h2>
    <div class="toolbar">
      <input id="itemSearch" list="itemList" type="search" placeholder="Filter, or pick any item or fluid…" style="flex:1;min-width:200px" value="${esc(prodFilter)}"><datalist id="itemList"></datalist>
    </div>
    <div id="itemChartBox" style="display:${itemSel?"block":"none"}">
      <h3 class="minor"><span id="itemTitle">${esc(itemLabel)}</span> <span class="sub"><b style="color:#6fbf5a">made</b> vs <b style="color:#ff9f1b">used</b>, per min</span> <button class="linkbtn" id="itemClose">close</button></h3>
      <div class="chartbox"><canvas id="chItem"></canvas></div>
    </div>
    <div style="max-height:440px;overflow:auto;margin-top:10px"><table class="prod"><thead><tr><th>Item</th><th class="r">Made /min</th><th class="r">Used /min</th><th class="r">Net /min</th></tr></thead>
    <tbody>${prodRows.map(t=>{const n=t.made_pm-t.used_pm, rel=Math.abs(n)/Math.max(t.made_pm,t.used_pm,1e-9);
      return `<tr data-item="${esc(t.name)}" data-kind="${t.kind}" data-label="${esc(t.label)}" style="cursor:pointer"><td>${esc(t.label)}${t.kind==="f"?' <span class="muted small">fluid</span>':""}</td><td class="r">${si(t.made_pm)}</td><td class="r">${si(t.used_pm)}</td><td class="r ${rel<.02?"muted":n>0?"okbox":"badbox"}">${rel<.02?"±0":signed(n)}</td></tr>`;}).join("")
      ||`<tr><td colspan="4" class="muted">${OV.top.length?"No match in the busiest items. Pick it from the list to see its graph.":"Rates appear after a few minutes of play."}</td></tr>`}</tbody></table></div>
  </div>
  <div class="card bevel wide" id="baseCard">
    <h2>Base size <span class="sub">${fmt(counts.Everything)} things built</span></h2>
    <div class="counts">${shown.map(([k,v])=>{const d=hadThen?v-(then[k]??0):0;return `<div class="inset"><span class="muted small">${esc(k)}</span><b class="num">${fmt(v)}</b>${d?`<span class="d ${d<0?"neg":""}">${d>0?"+":"−"}${fmt(Math.abs(d))} in 24 h</span>`:""}</div>`;}).join("")}</div>
    ${countRows.length>15?`<button class="linkbtn" id="countsAll" style="margin-top:8px">${countsAll?"Show fewer":`Show all ${countRows.length}`}</button>`:""}
    <h3 class="minor">Things built over time</h3>
    <div class="chartbox"><canvas id="chBuild"></canvas></div>
  </div>
  <div class="card bevel wide">
    <h2>Space</h2>
    ${(OV.rockets||se?.satellites||se?.cargo_rockets)?`<div class="stat-row">
      ${se?stat(fmt(se.satellites),"satellites")+stat(fmt(se.cargo_rockets),"cargo rockets"):stat(fmt(OV.rockets),"rockets launched")}
      ${se?stat(fmt(se.zones_known),"places discovered"):""}
    </div>`:`<div class="muted" style="margin-bottom:10px">Not in space yet.${se?" Launching a satellite reveals other planets and moons.":""}</div>`}
    <h3 class="minor">Built on</h3>
    <dl class="kv">${Object.values(OV.surfaces).map(s=>`<dt>${esc(s.name)}</dt><dd class="muted">${esc(s.kind||"")}</dd>`).join("")}</dl>
  </div>`;
  $("histRange").value=histRange;
  $("histRange").onchange=e=>{histRange=e.target.value;loadFactoryCharts();};
  g.querySelectorAll(".ovt").forEach(b=>b.onclick=()=>$(b.dataset.go)?.scrollIntoView({behavior:"smooth",block:"start"}));
  g.querySelectorAll("tr[data-item],.srow[data-item],a.dip").forEach(tr=>tr.onclick=ev=>{ev.preventDefault();
    pickItem(tr.dataset.kind,tr.dataset.item,tr.dataset.label);
    if(tr.closest("#shortCard"))$("prodCard").scrollIntoView({behavior:"smooth",block:"start"});
  });
  $("itemClose").onclick=()=>{itemSel="";itemLabel="";$("itemChartBox").style.display="none";};
  $("killsMore")&&($("killsMore").ontoggle=e=>{killsOpen=e.target.open;const b=e.target.querySelector(".dbtn");if(b)b.textContent=killsOpen?"Hide":"Show all";});
  $("countsAll")&&($("countsAll").onclick=()=>{countsAll=!countsAll;renderFactory();});
  const inp=$("itemSearch");
  inp.oninput=()=>{
    prodFilter=inp.value;
    const it=(window.ITEMS||[]).find(i=>i.label===inp.value);
    if(it){pickItem(it.kind,it.name,it.label);return;}
    const q=inp.value.toLowerCase();
    g.querySelectorAll("table.prod tr[data-item]").forEach(tr=>tr.hidden=!!q&&!tr.dataset.label.toLowerCase().includes(q));
  };
  if(prodFilter)inp.oninput();
  const fillList=items=>{$("itemList").innerHTML=items.map(i=>`<option value="${esc(i.label)}">`).join("");};
  if(window.ITEMS)fillList(window.ITEMS);
  else api("/api/items").then(d=>{window.ITEMS=d.items;fillList(d.items);}).catch(()=>{});
  $("polMapBtn")&&($("polMapBtn").onclick=()=>{show("map");setTimeout(()=>{if(!$("lyPol").checked){$("lyPol").checked=true;loadOverlay("pol");}},400);});
  loadFactoryCharts();
  loadRecentResearch();
}
function pickItem(kind,name,label){
  itemSel=kind+":"+name;itemLabel=label||name;
  $("itemTitle").textContent=itemLabel;
  loadItemChart();
}
async function loadFactoryCharts(){
  try{
    const h=await api(`/api/history?science=1&range=${histRange}`);
    const sci={};  // all packs together: one line is easier to read than one per pack
    for(const sr of Object.values(h.series))for(const p of sr.points)sci[p[0]]=(sci[p[0]]||0)+p[2];
    lineChart("chSci",[{label:"Science packs used /min",data:Object.entries(sci).map(([x,y])=>({x:+x,y})).sort((a,b)=>a.x-b.x),borderColor:"#ff9f1b"}]);
    lineChart("chBuild",[{label:"Things built",data:h.samples.filter(s=>s.buildings!=null).map(s=>({x:s.ts,y:s.buildings})),borderColor:"#58a6d6",stepped:true,fill:false}]);
    api(`/api/history?kind=p&names=_total&range=${histRange}`).then(p=>{const s=p.series._total;if(s)lineChart("chPol",[
      {label:"Pollution made /min",data:s.points.map(x=>({x:x[0],y:x[1]})),borderColor:"#b07a2a"}]);}).catch(()=>{});
    api(`/api/history?power=1&range=${histRange}`).then(p=>{const s=p.series._power;if(!s)return;
      const max=p.samples.filter(x=>x.pmax).map(x=>({x:x.ts,y:x.pmax})), now=powerMax(OV.power);
      if(max.length&&now)max.push({x:Math.max(Date.now()/1000,s.points.length?s.points[s.points.length-1][0]:0),y:now});  // the max holds until something changes it
      lineChart("chPow",[{label:"Consumed",data:s.points.map(x=>({x:x[0],y:x[1]/60})),borderColor:"#f2d14b",fill:"origin",backgroundColor:"#f2d14b22"},
        ...(max.length?[{label:"Max",data:max,borderColor:"#8e9aa3",borderDash:[6,4],borderWidth:1.5,stepped:true,tension:0}]:[])],fmtW);}).catch(()=>{});
    lineChart("chEvo",[{label:"Evolution",data:h.samples.filter(s=>s.evo!=null).map(s=>({x:s.ts,y:s.evo*100})),borderColor:"#e2493b",stepped:true,fill:false}],v=>(Number.isInteger(v)?v:v.toFixed(2))+"%",{max:100});  // scale: 0%…100%, pointing at the line: exact value  // evolution always runs 0–100%
  }catch(e){}
  if(itemSel)loadItemChart();
}
async function loadItemChart(){
  const [kind,name]=[itemSel.slice(0,1),itemSel.slice(2)];
  $("itemChartBox").style.display="block";
  try{
    const h=await api(`/api/history?kind=${kind}&names=${encodeURIComponent(name)}&range=${histRange}`);
    const s=h.series[name];
    lineChart("chItem",[{label:"Made /min",data:s.points.map(p=>({x:p[0],y:p[1]})),borderColor:"#6fbf5a"},
                        {label:"Used /min",data:s.points.map(p=>({x:p[0],y:p[2]})),borderColor:"#ff9f1b"}]);
  }catch(e){}
}
