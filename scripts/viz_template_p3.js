/* Part 3: interaction + structured views.
   8 first-class views (Overview/Endpoints/Data/Dependencies/Symbols/Flows/
   Graph/Issues); tables sortable + filterable + CSV/Markdown copy-out +
   inspector/editor links. Graph: hover full paths, edge click, crumbs with
   back/forward, keyboard (? overlay), URL-addressable ?view= + ?node=?mode=.
   Canvas + DPR handling kept; SVG export kept; no main-thread block >100ms
   (rebuild is rAF-chunked, draw is viewport-culled). */
let drag=null, moved=false;
cv.addEventListener("mousedown",e=>{drag={x:e.offsetX,y:e.offsetY,cx:S.cam.x,cy:S.cam.y};moved=false;cv.style.cursor="grabbing";});
window.addEventListener("mouseup",e=>{
  cv.style.cursor="grab";
  if(drag&&!moved){
    const r=cv.getBoundingClientRect();
    pick(e.clientX-r.left, e.clientY-r.top, e);
  }
  drag=null;
});
cv.addEventListener("mousemove",e=>{
  if(drag){
    const dx=e.offsetX-drag.x, dy=e.offsetY-drag.y;
    if(Math.abs(dx)+Math.abs(dy)>3) moved=true;
    S.cam.x=drag.cx-dx/S.cam.k; S.cam.y=drag.cy-dy/S.cam.k;
    draw();
  } else {
    const r=cv.getBoundingClientRect();
    const h=hit(e.clientX-r.left, e.clientY-r.top);
    const nh=h?h.id:null;
    if(nh!==S.hover){
      S.hover=nh;
      S.hoverPath = nh ? pathSet(nh) : null; // hover: full in/out paths
      draw();
    }
    cv.style.cursor=nh?"pointer":"grab";
  }
});
cv.addEventListener("mouseleave",()=>{ S.hover=null; S.hoverPath=null; hideEdgePop(); draw(); });
cv.addEventListener("wheel",e=>{e.preventDefault();zoom(e.deltaY<0?1.15:1/1.15,e.offsetX,e.offsetY);},{passive:false});
function zoom(f,px,py){
  const r=cv.getBoundingClientRect();
  px=px??r.width/2; py=py??r.height/2;
  const [wx,wy]=s2w(px,py);
  S.cam.k=Math.min(6,Math.max(0.08,S.cam.k*f));
  S.cam.x=wx-(px-cv.width/DPR/2)/S.cam.k;
  S.cam.y=wy-(py-cv.height/DPR/2)/S.cam.k;
  draw();
}
function hit(px,py){
  let best=null,bd=1e9;
  S.nodes.forEach(x=>{
    const [sx,sy]=w2s(x._x,x._y);
    const d=Math.hypot(sx-px,sy-py);
    const r=Math.max(8,x._r*S.cam.k+4);
    if(d<r&&d<bd){bd=d;best=x;}
  });
  return best;
}
function hitEdge(px,py){
  // clickable edges: distance point-to-segment < 6px
  let best=null,bd=6;
  S.edges.forEach(e=>{
    const a=S.byId[e.s],b=S.byId[e.t];
    if(!a||!b) return;
    const [x1,y1]=w2s(a._x,a._y),[x2,y2]=w2s(b._x,b._y);
    const dx=x2-x1, dy=y2-y1, L2=dx*dx+dy*dy;
    if(!L2) return;
    let t=((px-x1)*dx+(py-y1)*dy)/L2; t=Math.max(0,Math.min(1,t));
    const d=Math.hypot(px-(x1+t*dx), py-(y1+t*dy));
    if(d<bd){bd=d;best={e,px,py};}
  });
  return best;
}
function pick(px,py,ev){
  const hc=hitContainer(px,py);
  if(hc){  // fold the module: members leave the layout (never a fade)
    S.folded[hc.g]=true; S.sel=null;
    pushHist(); rebuild(); syncURL(); return;
  }
  const h=hit(px,py);
  if(h){
    if(h.kind==="__group__"){ S.group=h.id; S.sel=null; pushHist(); rebuild(); syncURL(); return; }
    select(h.id, true); return;
  }
  const he=hitEdge(px,py); // edge click -> details popover + inspector
  if(he){ selectEdge(he.e, he.px, he.py); return; }
  if(!ev||!ev.shiftKey){
    S.sel=null; S.edgeSel=null; hideEdgePop();
    S.group=S.mode==="architecture"?S.group:null;
    pushHist(); rebuild(); syncURL();
  }
}
function select(id, push){
  S.sel=id; S.edgeSel=null; hideEdgePop();
  const n=NODES[id];
  if(push&&n){ pushHist(); }
  // rebuild() is async (veil + rAF): center on the node's last known
  // position from the shared object; the rebuilt view re-fits + draws.
  if(n&&isFinite(n._x)&&isFinite(n._y)){S.cam.x=n._x;S.cam.y=n._y;if(S.cam.k<0.9)S.cam.k=0.9;}
  setView("graph");
  rebuild();
  syncURL();
}
function selectEdge(e, px, py){
  S.edgeSel = {s:e.s, t:e.t, type:e.type, conf:e.conf, file:e.file, line:e.line, ev:e.ev};
  S.sel = null;
  pushHist(); draw(); renderEdgeInsp(e); showEdgePop(e, px, py); syncURL();
}
function showEdgePop(e, px, py){
  const p=$("edgepop");
  const a=NODES[e.s], b=NODES[e.t];
  p.innerHTML="<b>"+esc(e.type)+"</b> <span class='pill "+esc(e.conf||"MEDIUM")+"'>"+esc(e.conf||"MEDIUM")+"</span><br>"+
    esc(a?a.label:e.s)+" → "+esc(b?b.label:e.t)+
    (e.file?"<br><span style='font-family:var(--mono);font-size:12px;color:var(--dim)'>"+esc(e.file)+(e.line?":"+e.line:"")+"</span>":"");
  const r=$("graphpane").getBoundingClientRect();
  p.style.left=Math.min(r.width-330,(px||60)+14)+"px";
  p.style.top=Math.max(30,(py||60)-10)+"px";
  p.style.display="block";
}
function hideEdgePop(){ const p=$("edgepop"); if(p) p.style.display="none"; }
function renderEdgeInsp(e){
  const el=$("insp");
  const a=NODES[e.s], b=NODES[e.t];
  let h="<h2>"+esc(e.type)+"</h2><div class='sub'>edge · "+esc(e.s)+" → "+esc(e.t)+"</div>";
  h+="<div class='kv'><span class='k'>Confidence</span><span class='v'><span class='conf "+esc(e.conf||"MEDIUM")+"'>"+esc(e.conf||"MEDIUM")+"</span></span></div>";
  if(a) h+="<div class='kv'><span class='k'>From</span><span class='v'><span class='n' data-id='"+esc(a.id)+"' style='cursor:pointer;color:var(--accent)'>"+esc(a.label)+"</span> <span style='color:var(--faint);font-size:12px'>"+esc(a.kind)+"</span></span></div>";
  if(b) h+="<div class='kv'><span class='k'>To</span><span class='v'><span class='n' data-id='"+esc(b.id)+"' style='cursor:pointer;color:var(--accent)'>"+esc(b.label)+"</span> <span style='color:var(--faint);font-size:12px'>"+esc(b.kind)+"</span></span></div>";
  if(e.file){
    const href=editorHref(e.file,e.line);
    h+="<div class='kv'><span class='k'>Evidence</span><span class='v mono'>"+
      (href?"<a class='src' href='"+esc(href)+"'>"+esc(e.file)+(e.line?":"+e.line:"")+"</a>":esc(e.file)+(e.line?":"+e.line:""))+"</span></div>";
  } else h+="<div class='kv'><span class='k'>Evidence</span><span class='v' style='color:var(--faint)'>none recorded</span></div>";
  if(e.ev) h+="<div class='kv'><span class='k'>Source</span><span class='v'>"+esc(e.ev)+"</span></div>";
  h+="<div style='margin-top:12px'><button class='btn' id='eclear'>Clear</button></div>";
  el.innerHTML=h; el.classList.remove("selhead"); bindLinks(el);
  const c=$("eclear"); if(c) c.onclick=()=>{S.edgeSel=null;hideEdgePop();rebuild();syncURL();};
}
cv.addEventListener("dblclick",e=>{
  e.preventDefault();
  const r=cv.getBoundingClientRect();
  const h=hit(e.clientX-r.left,e.clientY-r.top);
  // Single click already drills groups (pick) / focuses members (select):
  // double-click only deepens focus depth on real members.
  if(h&&h.kind!=="__group__"){
    S.depth=Math.min(4,S.depth+1);
    const ds=$("depthsel"); if(ds) ds.value=String(S.depth);
    select(h.id,false);
  }
});
cv.addEventListener("contextmenu",e=>{
  e.preventDefault();
  const r=cv.getBoundingClientRect();
  const h=hit(e.clientX-r.left,e.clientY-r.top);
  if(h&&h.kind!=="__group__") select(h.id,true);
});
function fitCam(redraw=true){
  if(!S.nodes.length) return;
  let x0=1e18,y0=1e18,x1=-1e18,y1=-1e18;
  S.nodes.forEach(x=>{x0=Math.min(x0,x._x);y0=Math.min(y0,x._y);x1=Math.max(x1,x._x);y1=Math.max(y1,x._y);});
  // astronomic-coordinate guard: recenter to a sane grid instead of
  // following garbage off-screen
  if(![x0,y0,x1,y1].every(isFinite)||Math.max(Math.abs(x0),Math.abs(y0),Math.abs(x1),Math.abs(y1))>1e6){
    S.nodes.forEach((x,i)=>{x._x=(i%37)*17-300;x._y=(i%41)*13-260;});
    x0=-300;y0=-260;x1=300;y1=260;
  }
  const r=$("graphpane").getBoundingClientRect();
  if(r.width<10) return;
  const k=Math.min(2.2,(r.width-160)/Math.max(80,x1-x0+200),(r.height-140)/Math.max(80,y1-y0));
  S.cam={x:(x0+x1)/2,y:(y0+y1)/2,k:Math.max(0.08,k)};
  if(redraw) draw();
}
/* keyboard: / arrows Enter Esc [ ] F + - ? */
let resCur = -1;
document.addEventListener("keydown",e=>{
  if($("help").classList.contains("show")){
    if(e.key==="Escape"||e.key==="?"){$("help").classList.remove("show");}
    return;
  }
  if(e.target.tagName==="INPUT"||e.target.tagName==="SELECT") {
    if(e.key==="Escape"){ e.target.blur(); hideResults(); }
    else if(e.key==="ArrowDown"||e.key==="ArrowUp"){
      if($("results").style.display==="block"){ e.preventDefault(); moveRes(e.key==="ArrowDown"?1:-1); }
    }
    else if(e.key==="Enter"){
      const items=$("results").querySelectorAll(".r");
      if(items.length){ e.preventDefault(); items[Math.max(0,resCur)].click(); }
    }
    return;
  }
  if(e.key==="/"){e.preventDefault();$("q").focus();}
  else if(e.key==="?"){$("help").classList.add("show");}
  else if(e.key==="Escape"){S.sel=null;S.edgeSel=null;hideEdgePop();rebuild();syncURL();}
  else if(e.key==="f"||e.key==="F"){fitCam();}
  else if(e.key==="+"||e.key==="="){zoom(1.2);}
  else if(e.key==="-"||e.key==="_"){zoom(1/1.2);}
  else if(e.key==="["){histGo(-1);}
  else if(e.key==="]"){histGo(1);}
  else if(e.key==="Enter"&&S.view==="graph"&&S.sel){e.preventDefault();select(S.sel,false);}
  else if(e.key==="ArrowRight"&&S.view==="graph"){e.preventDefault();stepSel(1);}
  else if(e.key==="ArrowLeft"&&S.view==="graph"){e.preventDefault();stepSel(-1);}
  else if(e.key==="ArrowUp"&&S.view==="graph"){e.preventDefault();stepSel(-1);}
  else if(e.key==="ArrowDown"&&S.view==="graph"){e.preventDefault();stepSel(1);}
});
function stepSel(d){
  // arrows move between NEIGHBOURS of the selection (brief 4.5); with no
  // selection they walk the laid-out nodes in deterministic id order.
  if(!S.nodes.length) return;
  let ids;
  if(S.sel && S.byId[S.sel]){
    const nb = new Set();
    (S.adj[S.sel]||[]).forEach(t=>{if(S.byId[t])nb.add(t);});
    (S.inadj[S.sel]||[]).forEach(t=>{if(S.byId[t])nb.add(t);});
    ids = [...nb].sort();
    if(!ids.length) ids = S.nodes.map(n=>n.id).sort();
  } else ids = S.nodes.map(n=>n.id).sort();
  let i = S.sel ? ids.indexOf(S.sel) : (d>0?-1:0);
  i = (i+d+ids.length)%ids.length;
  select(ids[i], true);
}
function hitContainer(px,py){
  // container header strip (top ~18px of each drawn boundary box)
  const cs = S._containers||[];
  for(let i=cs.length-1;i>=0;i--){
    const c=cs[i];
    if(px>=c.sx&&px<=c.sx+c.w&&py>=c.sy&&py<=c.sy+20) return c;
  }
  return null;
}

/* ---------- search ---------- */
const q=$("q"), res=$("results");
q.addEventListener("input",()=>{
  const t=q.value.trim().toLowerCase();
  if(t.length<2){hideResults();return;}
  const out=[];
  for(const s of DATA.search){
    if(out.length>=25) break;
    const hay=(s.label+" "+s.kind+" "+(s.file||"")+" "+(s.lang||"")+" "+s.id).toLowerCase();
    if(hay.includes(t)||t.split(/\s+/).every(w=>hay.includes(w))) out.push(s);
  }
  if(!out.length){hideResults();return;}
  res.innerHTML=""; resCur=-1;
  out.forEach(s=>{
    const d=document.createElement("div");d.className="r";
    d.innerHTML="<span class='rk'></span><span></span><span class='rf'></span>";
    d.children[0].textContent=s.kind;
    d.children[1].textContent=s.label;
    d.children[2].textContent=s.file||s.id;
    d.onclick=()=>{hideResults();q.value="";resCur=-1;focusSearch(s.id);};
    res.appendChild(d);
  });
  res.style.display="block";
});
function hideResults(){ res.style.display="none"; resCur=-1; }
function moveRes(d){
  const items=res.querySelectorAll(".r");
  if(!items.length) return;
  resCur=(resCur+d+items.length)%items.length;
  items.forEach((el,i)=>el.classList.toggle("cur",i===resCur));
  items[resCur].scrollIntoView({block:"nearest"});
}
document.addEventListener("click",e=>{if(!$("search").contains(e.target))hideResults();});
function focusSearch(id){
  const n=NODES[id];
  if(!n) return;
  setView("graph");
  // open the containing group context if in architecture mode
  if(S.mode==="architecture"&&n.group&&GROUPS["g:"+n.group]&&!S.sel&&!S.group){
    S.group="g:"+n.group;
  }
  select(id,true);
}

/* ---------- inspector ---------- */
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}
function editorHref(file,line){
  // configurable editor: ?editor=vscode|jetbrains|none (default vscode URL, plain text fallback)
  const ed=new URLSearchParams(location.search).get("editor")||"vscode";
  const l=line||1;
  // file paths in the graph are repo-RELATIVE; editor URL handlers need
  // an absolute path (vscode://file/<rel> resolves to filesystem root and
  // "path is wrong"). The generator embeds the absolute repo root in BOOT.
  const root=(BOOT.root||"").replace(/\/+$/,"");
  const abs=root?root+"/"+String(file||"").replace(/^\/+/,""):String(file||"");
  if(ed==="jetbrains") return "jetbrains://open?file="+encodeURIComponent(abs)+"&line="+l;
  if(ed==="none") return null;
  return "vscode://file/"+encodeURIComponent(abs)+":"+l;
}
function srcLink(file,line){
  if(!file) return "<span style='color:var(--faint)'>—</span>";
  const href=editorHref(file,line);
  const t=esc(file)+(line?":"+line:"");
  return href?"<a class='src' href='"+esc(href)+"'>"+t+"</a>":t;
}
function rowLink(id,label){
  if(id&&NODES[id]) return "<span class='n' data-id='"+esc(id)+"' style='cursor:pointer;color:var(--accent)'>"+esc(label||NODES[id].label)+"</span>";
  return esc(label||id||"—");
}
function renderInsp(){
  if(S.edgeSel) { renderEdgeInsp(S.edgeSel); return; }
  const el=$("insp");
  const n=S.sel?NODES[S.sel]:null;
  el.classList.toggle("selhead", !!n); // unmistakable selection header
  if(!n){
    const f=BOOT.focus||{};
    let h='<div class="empty">Select a node to inspect it.<br><br>Single-click inspects · double-click deepens focus.<br>Click an edge for its details.</div>';
    if(f.impact&&f.impact.target) h=impactSummary()+h;
    else if(f.flow) h=flowSummary()+h;
    el.innerHTML=h; bindLinks(el); return;
  }
  const inc=EDGES.filter(e=>e.t===n.id), out=EDGES.filter(e=>e.s===n.id);
  const grp=(arr,dir)=>{const m={};arr.forEach(e=>{const o=NODES[dir==="in"?e.s:e.t];if(!o)return;
    (m[e.type]=m[e.type]||[]).push({e,o});});return m;};
  const relRow=(type,o,e,dir)=>"<div class='rel'><span class='t'>"+esc(dir==="in"?"← ":"")+esc(type)+(dir==="out"?" →":"")+"</span>"+
    "<span class='n' data-id='"+esc(o.id)+"'>"+esc(o.label)+"</span><span class='c'>"+esc(o.kind)+" · "+esc(e.conf)+"</span></div>";
  let h="<h2>"+esc(n.label)+"</h2><div class='sub'>"+esc(n.kind)+(n.lang?" · "+esc(n.lang):"")+(n.fw?" · "+esc(n.fw):"")+(n.layer?" · "+esc(n.layer):"")+"</div>";
  h+="<div class='kv'><span class='k'>Confidence</span><span class='v'><span class='conf "+esc(n.conf)+"'>"+esc(n.conf)+"</span></span></div>";
  if(n.group) h+="<div class='kv'><span class='k'>Group</span><span class='v'>"+esc(n.group)+"</span></div>";
  if(n.layer) h+="<div class='kv'><span class='k'>Layer</span><span class='v'>"+esc(n.layer)+"</span></div>";
  if(n.file){
    const href=editorHref(n.file,n.line);
    h+="<div class='kv'><span class='k'>Source</span><span class='v mono'>"+
      (href?"<a class='src' href='"+esc(href)+"'>"+esc(n.file)+(n.line?":"+n.line:"")+"</a>"
            :esc(n.file)+(n.line?":"+n.line:""))+"</span></div>";
  }
  h+="<div class='kv'><span class='k'>Relations</span><span class='v'>"+inc.length+" in · "+out.length+" out · degree "+(n.deg||0)+"</span></div>";
  const gi=grp(inc,"in"), go=grp(out,"out");
  const order=["handled-by","calls","injects","reads","writes","queries","consumes","exposes","publishes","invokes","transforms","depends-on","imports","references","configures","tests"];
  const keys=[...new Set([...Object.keys(gi),...Object.keys(go)])].sort((a,b)=>order.indexOf(a)-order.indexOf(b));
  keys.forEach(t=>{
    h+="<h3>"+esc(t)+"</h3>";
    (gi[t]||[]).slice(0,12).forEach(({o,e})=>{h+=relRow(t,o,e,"in");});
    (go[t]||[]).slice(0,12).forEach(({o,e})=>{h+=relRow(t,o,e,"out");});
    if((gi[t]||[]).length+(go[t]||[]).length>24) h+="<div style='font-size:12px;color:var(--faint)'>…truncated</div>";
  });
  h+="<div style='margin-top:12px;display:flex;gap:8px'>"+
     "<button class='btn' id='focusbtn'>Focus neighborhood</button>"+
     "<button class='btn' id='clearbtn'>Clear</button></div>";
  el.innerHTML=h; bindLinks(el);
  const fb=$("focusbtn"), cb=$("clearbtn");
  if(fb) fb.onclick=()=>{rebuild();};
  if(cb) cb.onclick=()=>{S.sel=null;rebuild();syncURL();};
}
function bindLinks(el){
  el.querySelectorAll(".n[data-id]").forEach(s=>{
    s.onclick=()=>{const id=s.getAttribute("data-id");if(NODES[id])select(id,true);};
  });
  el.querySelectorAll("[data-view]").forEach(b=>{
    b.onclick=()=>setView(b.getAttribute("data-view"));
  });
}
function impactSummary(){
  const f=BOOT.focus.impact;
  return "<div style='padding:12px;border-bottom:1px solid var(--border)'><h2 style='font-size:13px'>Impact: "+esc(f.target)+"</h2>"+
    "<div class='sub'>"+f.callers+" direct callers · "+f.callees+" direct deps · "+f.transitive+" transitive · "+f.entrypoints.length+" entry points</div></div>";
}
function flowSummary(){
  const f=BOOT.focus.flow, c=BOOT.focus.flow_complete;
  return "<div style='padding:12px;border-bottom:1px solid var(--border)'><h2 style='font-size:13px'>Flow: "+esc(f.from||"")+" → "+esc(f.to||"")+"</h2>"+
    "<div class='sub'>"+(c?"evidenced path":"<span style='color:var(--warn)'>Flow inference incomplete — source inspection required</span>")+"</div></div>";
}

/* ---------- cap banner (explicit N of M) ---------- */
function renderBanner(){
  const b=$("banner");
  if(S.view!=="graph"){ b.classList.remove("show"); return; }
  const folds=Object.keys(S.folded);
  if(folds.length && !S.truncated){
    b.innerHTML="Folded: <b>"+esc(folds.slice(0,5).join(", "))+"</b>"+(folds.length>5?" +"+(folds.length-5)+" more":"")+" (members out of layout). "+
      "<button class='btn' id='capclear'>Unfold all</button>";
    b.classList.add("show");
    const c=$("capclear");
    if(c) c.onclick=()=>{S.folded={};rebuild();};
    return;
  }
  if(S.truncated){
    b.innerHTML="Showing <b>"+S.truncated.shown+" of "+S.truncated.total+"</b> nodes — filter or drill into a module to see more. "+
      "<button class='btn' id='capclear'>Clear filters</button>";
    b.classList.add("show");
    const c=$("capclear");
    if(c) c.onclick=()=>{Object.keys(S.kinds).forEach(k=>S.kinds[k]=true);Object.keys(S.langs).forEach(l=>S.langs[l]=true);S.conf={HIGH:true,MEDIUM:true,LOW:true,UNKNOWN:true};renderFilters();rebuild();};
  } else if(!S.nodes.length){
    b.innerHTML="No nodes for the current selection + filters. <button class='btn' id='capclear'>Reset</button>";
    b.classList.add("show");
    const c=$("capclear");
    if(c) c.onclick=()=>{S.sel=null;S.group=null;rebuild();syncURL();};
  } else b.classList.remove("show");
}

/* ---------- crumbs with back/forward ---------- */
function snap(){ return {view:S.view, mode:S.mode, sel:S.sel, group:S.group, depth:S.depth}; }
function pushHist(){
  S.hist = S.hist.slice(0, S.hi+1);
  S.hist.push(snap());
  if(S.hist.length>50) S.hist.shift();
  S.hi = S.hist.length-1;
}
function histGo(d){
  const i = S.hi + d;
  if(i<0||i>=S.hist.length) return;
  S.hi = i;
  const s = S.hist[i];
  S.view=s.view; S.mode=s.mode; S.sel=s.sel; S.group=s.group; S.depth=s.depth;
  renderViews(); renderModes();
  if(S.view==="graph") rebuild(); else renderTable();
  syncURL();
}
function renderCrumbs(){
  const c=$("crumbs");
  let h="<span class='c navbtn' data-h='-1' title='Back ( [ )'>‹</span>"+
        "<span class='c navbtn' data-h='1' title='Forward ( ] )'>›</span>";
  h+="<span class='c' data-v='overview'><b>"+esc(S.view)+"</b></span>";
  if(S.view==="graph"){
    h+="<span>›</span><span class='c' data-g=''>"+esc(S.mode)+"</span>";
    if(S.group) h+="<span>›</span><span class='c' data-g='"+esc(S.group)+"'>"+esc(S.group.replace(/^g:/,""))+"</span>";
    if(S.sel&&NODES[S.sel]) h+="<span>›</span><span class='c'><b>"+esc(NODES[S.sel].label)+"</b></span>";
    h+="<span style='margin-left:8px'>"+S.nodes.length+" nodes · "+S.edges.length+" edges</span>";
  }
  c.innerHTML=h;
  c.querySelectorAll("[data-v]").forEach(el=>el.onclick=()=>setView("overview"));
  c.querySelectorAll("[data-g]").forEach(el=>el.onclick=()=>{
    const g=el.getAttribute("data-g");
    S.group=g||null; if(!g) S.sel=null;
    pushHist(); rebuild(); syncURL();
  });
  c.querySelectorAll("[data-h]").forEach(el=>el.onclick=()=>histGo(+el.getAttribute("data-h")));
}
function mkChk(parent, checked, html, onchg){
  const l=document.createElement("label");l.className="chk";
  l.innerHTML=html;
  const inp=document.createElement("input");
  inp.type="checkbox";inp.checked=!!checked;
  inp.onchange=onchg;
  l.insertBefore(inp, l.firstChild);
  parent.appendChild(l);
  return l;
}
function renderModes(){
  const m=$("gmodes"); m.innerHTML="";
  if(S.view!=="graph"){ m.style.display="none"; return; }
  m.style.display="flex";
  Object.keys(EMODE).forEach(k=>{
    const b=document.createElement("button");
    b.textContent=k; b.setAttribute("role","tab");
    if(k===S.mode) b.className="on";
    b.onclick=()=>{S.mode=k;S.group=null;S.sel=null;pushHist();rebuild();syncURL();};
    m.appendChild(b);
  });
}
function renderViews(){
  const T = DATA.tables||{};
  const defs = [
    ["overview","Overview"],["endpoints","Endpoints"],["data","Data"],
    ["dependencies","Dependencies"],["symbols","Symbols"],["flows","Flows"],
    ["capabilities","Capabilities"],["graph","Graph"],["issues","Issues"],
  ];
  const m=$("views"); m.innerHTML="";
  defs.forEach(([k,label])=>{
    const b=document.createElement("button");
    let n=null;
    if(k==="endpoints") n=(T.endpoints||[]).length;
    else if(k==="dependencies") n=(T.dependencies||[]).length;
    else if(k==="symbols") n=(T.symbols||{}).total;
    else if(k==="flows") n=(T.flows||[]).length;
    else if(k==="capabilities") n=(T.capabilities||[]).length;
    else if(k==="issues") n=(T.issues||[]).length;
    b.textContent = label+(n!=null?" ("+n+")":"");
    if(k===S.view) b.className="on";
    b.onclick=()=>setView(k);
    m.appendChild(b);
  });
  $("tablepane").classList.toggle("show", S.view!=="graph");
  $("graphpane").style.display = S.view==="graph" ? "flex" : "none";
  renderModes();
}
function setView(v){
  if(S.view!==v){ S.view=v; pushHist(); }
  renderViews(); renderCrumbs();
  if(v==="graph"){ renderModes(); resize(); rebuild(); }
  else renderTable();
  syncURL();
}

/* ---------- structured tables (sortable/filterable/linked/copy-out) ---------- */
function filteredRows(rows, hay){
  const t=S.tfilter.trim().toLowerCase();
  if(!t) return rows;
  return rows.filter(r=>hay(r).toLowerCase().includes(t)
    || t.split(/\s+/).every(w=>hay(r).toLowerCase().includes(w)));
}
function sortRows(rows, col, dir, get){
  if(!col) return rows;
  return rows.slice().sort((a,b)=>{
    const x=get(a,col), y=get(b,col);
    const c=(x??"")<(y??"")?-1:((x??"")>(y??"")?1:0);
    return dir==="desc"?-c:c;
  });
}
function th(col, label, cur){
  const arrow = cur&&cur.col===col ? (cur.dir==="asc"?" ▲":" ▼") : "";
  return "<th data-c='"+col+"'>"+esc(label)+arrow+"</th>";
}
function bindSort(el, get, rerender){
  el.querySelectorAll("th[data-c]").forEach(h=>{
    h.onclick=()=>{
      const c=h.getAttribute("data-c");
      if(S.tsort&&S.tsort.col===c) S.tsort.dir=S.tsort.dir==="asc"?"desc":"asc";
      else S.tsort={col:c,dir:"asc"};
      rerender();
    };
  });
}
function copyOut(kind, rows, cols){
  const toMD=()=>"| "+cols.join(" | ")+" |\n| "+cols.map(()=>"---").join(" | ")+" |\n"+
    rows.map(r=>"| "+cols.map(c=>String(r[c]??"").replace(/\|/g,"/")).join(" | ")+" |").join("\n");
  const toCSV=()=>[cols.join(",")].concat(rows.map(r=>cols.map(c=>'"'+String(r[c]??"").replace(/"/g,'""')+'"').join(","))).join("\n");
  return {md:toMD(), csv:toCSV()};
}
function copyBar(id, rows, cols){
  return "<div class='toolbar'><button class='btn' data-copy='csv:"+id+"'>Copy CSV</button>"+
    "<button class='btn' data-copy='md:"+id+"'>Copy Markdown</button>"+
    "<span style='font-size:12px;color:var(--faint)'>"+rows.length+" rows</span></div>";
}
function bindCopy(el, datasets){
  el.querySelectorAll("[data-copy]").forEach(b=>{
    b.onclick=()=>{
      const [fmt,id]=b.getAttribute("data-copy").split(":");
      const d=datasets[id];
      if(!d) return;
      const txt=fmt==="csv"?d.csv:d.md;
      if(navigator.clipboard&&navigator.clipboard.writeText)
        navigator.clipboard.writeText(txt).then(()=>{b.textContent="Copied ✓";setTimeout(()=>{b.textContent=fmt==="csv"?"Copy CSV":"Copy Markdown";},1200);});
      else {
        const ta=document.createElement("textarea");ta.value=txt;document.body.appendChild(ta);ta.select();
        try{document.execCommand("copy");b.textContent="Copied ✓";}catch(e){b.textContent="Copy failed";}
        document.body.removeChild(ta);
      }
    };
  });
}
function emptyState(what){
  return "<div class='empty'>No "+what+" match the current filter.<br>Clear the filter text above to see everything.</div>";
}
function errState(msg){
  return "<div class='errbar'>"+esc(msg)+"</div>";
}
function renderTable(){
  const T=DATA.tables||{};
  const el=$("tablepane");
  renderViews();
  const V=S.view;
  if(V==="overview") return renderOverview(el,T);
  if(V==="endpoints") return renderEndpoints(el,T);
  if(V==="data") return renderData(el,T);
  if(V==="dependencies") return renderDeps(el,T);
  if(V==="symbols") return renderSymbols(el,T);
  if(V==="flows") return renderFlows(el,T);
  if(V==="capabilities") return renderCapabilities(el,T);
  if(V==="issues") return renderIssues(el,T);
  el.innerHTML=errState("Unknown view: "+V);
}
function renderOverview(el,T){
  const o=(T.overview||{});
  const f=o.freshness||{}, v=o.validation||{};
  const stack=(DATA.meta||{}).stack||{};
  const stackLine = ["languages","frameworks","databases","infrastructure"]
    .map(s=>"<b>"+s+"</b>: "+(((stack[s]||[]).map(d=>esc(d.name||d))).join(", ")||"—")).join("<br>");
  let h="<h1>Overview</h1><p class='lede'>"+esc((DATA.meta||{}).graph_nodes||0)+" nodes · "+esc((DATA.meta||{}).graph_edges||0)+" edges · generated from graph.json (read-only).</p>";
  h+="<div class='cards'>"+
    "<div class='card'><div class='n'>"+(o.total_nodes??"—")+"</div><div class='l'>nodes</div></div>"+
    "<div class='card'><div class='n'>"+(o.total_edges??"—")+"</div><div class='l'>relationships</div></div>"+
    "<div class='card'><div class='n'>"+(v.unresolved??0)+"</div><div class='l'>unresolved refs</div></div>"+
    "<div class='card'><div class='n'>"+(v.low_items??0)+"</div><div class='l'>LOW-confidence items</div></div>"+
    "<div class='card'><div class='n'>"+(v.no_evidence??0)+"</div><div class='l'>no-evidence nodes</div></div>"+
    "<div class='card'><div class='n'>"+esc(f.last_commit||"?")+"</div><div class='l'>last sync "+esc(f.last_sync||"unknown")+"</div></div></div>";
  h+="<h2>Stack</h2><div style='font-size:13px;line-height:2'>"+stackLine+"</div>";
  h+="<h2>Counts by kind</h2><table class='tv'><tr><th>kind</th><th>count</th></tr>"+
    ((o.counts||[]).map(([k,c])=>"<tr><td class='mono'>"+esc(k)+"</td><td class='mono'>"+c+"</td></tr>").join("")||"<tr><td>No data</td><td></td></tr>")+"</table>";
  h+="<h2>Validation</h2><div style='font-size:13px;line-height:2'>"+
    "Unresolved: <b>"+(v.unresolved??0)+"</b> · LOW items: <b>"+(v.low_items??0)+"</b> · duplicate ids: <b>"+(v.dupes??0)+"</b> · no-evidence: <b>"+(v.no_evidence??0)+"</b>"+
    " · <button class='btn' data-view='issues'>Open Issues</button></div>";
  if(f.stale&&f.stale.length) h+="<h2>Freshness</h2><div class='errbar'>Stale: "+f.stale.length+" changed files since sync — "+f.stale.slice(0,5).map(esc).join(", ")+"</div>";
  else h+="<h2>Freshness</h2><div style='font-size:13px'>Map is fresh as of "+esc(f.last_sync||"unknown")+".</div>";
  h+="<h2>Entry points</h2><table class='tv'><tr><th>name</th><th>kind</th><th>source</th></tr>"+
    (((o.entry_points||[]).map(p=>"<tr><td>"+rowLink(p.id,p.label)+"</td><td class='mono'>"+esc(p.kind||"")+"</td><td class='mono'>"+srcLink(p.file,p.line)+"</td></tr>").join(""))||"<tr><td>No entry points found</td><td></td><td></td></tr>")+"</table>";
  const recent=o.recent||[];
  h+="<h2>Recent changes</h2>"+(recent.length
    ? "<table class='tv'><tr><th>change record</th><th>title</th></tr>"+recent.map(c=>"<tr><td class='mono'>"+esc(c.file)+"</td><td>"+esc(c.title)+"</td></tr>").join("")+"</table>"
    : "<div class='empty'>No change records yet — they appear after sync with architectural deltas.</div>");
  el.innerHTML=h; bindLinks(el);
}
function renderEndpoints(el,T){
  const rows=(T.endpoints||[]).slice().sort((a,b)=>(a.path||"")<(b.path||"")?-1:1);
  let h="<h1>Endpoints</h1><p class='lede'>method / path / handler / source / confidence / consumers / downstream reach. Every row links to the inspector and file:line.</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter endpoints…' value='"+esc(S.tfilter)+"'></div>";
  const hay=r=>(r.method+" "+r.path+" "+(r.handler||"")+" "+(r.file||""));
  const got=filteredRows(rows,hay);
  const get=(r,c)=>c==="consumers"?r.n_consumers:(c==="reach"?(r.reach||0):r[c]);
  const s=sortRows(got,S.tsort&&S.tsort.col,S.tsort&&S.tsort.dir,get);
  const ds={ep:copyOut(0,got,["method","path","handler","file","line","conf","n_consumers","reach"])};
  h+=copyBar("ep",got,["method","path","handler","file","line","conf","n_consumers","reach"]);
  if(!s.length) h+=emptyState("endpoints");
  else{
    h+="<table class='tv'><tr>"+th("method","Method",S.tsort)+th("path","Path",S.tsort)+th("handler","Handler",S.tsort)+th("file","Source",S.tsort)+th("conf","Conf",S.tsort)+th("n_consumers","Consumers",S.tsort)+th("reach","Downstream",S.tsort)+"</tr>";
    s.slice(0,500).forEach(r=>{
      h+="<tr><td class='mono'>"+esc(r.method||"—")+"</td><td>"+rowLink(r.id,(r.method?r.method+" ":"")+r.path)+"</td><td class='mono'>"+(r.handler?rowLink(r.handler,r.handler):"<span style='color:var(--faint)'>—</span>")+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td><td class='mono'>"+r.n_consumers+"</td><td class='mono'>"+(r.reach||0)+"</td></tr>";
    });
    h+="</table>"+(s.length>500?"<div class='empty'>Showing 500 of "+s.length+" — refine the filter.</div>":"");
  }
  el.innerHTML=h; bindLinks(el); bindCopy(el,ds);
  bindSort(el,get,()=>renderEndpoints(el,T));
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderEndpoints(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderData(el,T){
  const d=T.data||{tables:[],access:[],migrations:[]};
  let h="<h1>Data</h1><p class='lede'>Stores plus every evidenced read/write access, plus the migrations that define each store.</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter tables / actors…' value='"+esc(S.tfilter)+"'></div>";
  const t=filteredRows(d.tables,r=>r.label+" "+r.kind+" "+(r.file||"")+((r.columns||[]).join(" ")));
  const ds={dt:copyOut(0,d.tables,["label","kind","file","line","conf"])};
  h+="<h2>Tables &amp; stores ("+t.length+")</h2>"+copyBar("dt",t,["label","kind","file","line","conf"]);
  h+= t.length? "<table class='tv'><tr>"+th("label","Store",S.tsort)+th("kind","Kind",S.tsort)+th("columns","Columns",S.tsort)+th("file","Source",S.tsort)+th("conf","Conf",S.tsort)+th("deg","Degree",S.tsort)+"</tr>"+
    t.slice(0,300).map(r=>"<tr><td>"+rowLink(r.id,r.label)+"</td><td class='mono'>"+esc(r.kind)+"</td><td class='mono' style='color:var(--faint)'>"+((r.columns&&r.columns.length)?esc(r.columns.join(", ")):"not in graph — see migration DDL")+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td><td class='mono'>"+r.deg+"</td></tr>").join("")+"</table>"
    : emptyState("stores");
  const a=filteredRows(d.access,r=>r.table+" "+r.op+" "+r.actor+" "+(r.file||""));
  h+="<h2>Readers &amp; writers ("+a.length+")</h2>";
  h+= a.length? "<table class='tv'><tr><th>store</th><th>op</th><th>actor</th><th>source</th><th>conf</th></tr>"+
    a.slice(0,500).map(r=>"<tr><td>"+rowLink(r.table,r.table)+"</td><td class='mono'>"+esc(r.op)+"</td><td>"+rowLink(r.actor,r.actor)+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td></tr>").join("")+"</table>"
    : emptyState("access rows");
  const migs=filteredRows(d.migrations||[],r=>r.table+" "+r.migration+" "+(r.file||""));
  h+="<h2>Migrations ("+migs.length+")</h2>";
  h+= migs.length? "<table class='tv'><tr><th>store</th><th>migration</th><th>source</th><th>conf</th></tr>"+
    migs.slice(0,300).map(r=>"<tr><td>"+rowLink(r.table,r.table)+"</td><td>"+rowLink(r.migration_id,r.migration)+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td></tr>").join("")+"</table>"
    : "<div class='empty'>No migration→store links evidenced in the graph.</div>";
  el.innerHTML=h; bindLinks(el); bindCopy(el,ds);
  bindSort(el,(r,c)=>r[c],()=>renderData(el,T));
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderData(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderDeps(el,T){
  const rows=(T.dependencies||[]).slice();
  let h="<h1>Dependencies</h1><p class='lede'>External modules grouped by manifest — version, importing files, source. (Versions come from graph evidence only; blank means unrecorded, not latest.)</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter dependencies…' value='"+esc(S.tfilter)+"'></div>";
  const hay=r=>r.label+" "+r.manifest+" "+(r.version||"")+" "+((r.importing||[]).join(" "))+" "+(r.file||"");
  const got=filteredRows(rows,hay);
  const get=(r,c)=>c==="importing"?(r.n_importing||0):r[c];
  const s=sortRows(got,S.depSort&&S.depSort.col,S.depSort&&S.depSort.dir,get);
  const ds={dp:copyOut(0,got,["label","manifest","version","conf"])};
  h+=copyBar("dp",got,["label","manifest","version","conf"]);
  if(!s.length) h+=emptyState("dependencies");
  else{
    h+="<table class='tv'><tr>"+th("label","Module",S.depSort)+th("manifest","Manifest",S.depSort)+th("version","Version",S.depSort)+th("n_importing","Importers",S.depSort)+th("conf","Conf",S.depSort)+th("file","Source",S.depSort)+"</tr>";
    let lastManifest=null;
    s.slice(0,500).forEach(r=>{
      if(r.manifest!==lastManifest){ lastManifest=r.manifest; h+="<tr><td colspan='6' class='manifest'>"+esc(lastManifest||"(no manifest)")+"</td></tr>"; }
      h+="<tr><td>"+rowLink(r.id,r.label)+"</td><td class='mono'>"+esc(r.manifest||"—")+"</td><td class='mono'>"+(r.version?esc(r.version):"<span style='color:var(--faint)'>unrecorded</span>")+"</td><td class='mono' title='"+esc((r.importing||[]).join(", "))+"'>"+(r.n_importing||0)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td><td class='mono'>"+srcLink(r.file,r.line)+"</td></tr>";
    });
    h+="</table>"+(s.length>500?"<div class='empty'>Showing 500 of "+s.length+" — refine the filter.</div>":"");
  }
  el.innerHTML=h; bindLinks(el); bindCopy(el,ds);
  el.querySelectorAll("th[data-c]").forEach(hh=>{
    hh.onclick=()=>{
      const c=hh.getAttribute("data-c");
      if(S.depSort&&S.depSort.col===c) S.depSort.dir=S.depSort.dir==="asc"?"desc":"asc";
      else S.depSort={col:c,dir:"asc"};
      renderDeps(el,T);
    };
  });
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderDeps(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderSymbols(el,T){
  const sym=T.symbols||{total:0,rows:[]};
  const kinds=[...new Set(sym.rows.map(r=>r.kind))].sort();
  const langs=[...new Set(sym.rows.map(r=>r.lang).filter(Boolean))].sort();
  let h="<h1>Symbols</h1><p class='lede'>"+sym.total+" symbols"+(sym.total>sym.rows.length?" — showing first "+sym.rows.length+" (filter to narrow)":"")+".</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter symbols…' value='"+esc(S.tfilter)+"'>"+
    "<select id='sk'><option value=''>all kinds</option>"+kinds.map(k=>"<option value='"+esc(k)+"'"+(S.symKind===k?" selected":"")+">"+esc(k)+"</option>").join("")+"</select>"+
    "<select id='sl'><option value=''>all languages</option>"+langs.map(k=>"<option value='"+esc(k)+"'"+(S.symLang===k?" selected":"")+">"+esc(k)+"</option>").join("")+"</select>"+
    "<input type='text' id='sf' placeholder='file contains…' value='"+esc(S.symFile)+"' style='min-width:140px'></div>";
  let rows=sym.rows;
  if(S.symKind) rows=rows.filter(r=>r.kind===S.symKind);
  if(S.symLang) rows=rows.filter(r=>r.lang===S.symLang);
  if(S.symFile) rows=rows.filter(r=>(r.file||"").includes(S.symFile));
  rows=filteredRows(rows,r=>r.label+" "+r.kind+" "+(r.lang||"")+" "+(r.file||""));
  const get=(r,c)=>r[c];
  const s=sortRows(rows,S.tsort&&S.tsort.col,S.tsort&&S.tsort.dir,get);
  const ds={sy:copyOut(0,rows,["label","kind","lang","file","line","conf","deg"])};
  h+=copyBar("sy",rows,["label","kind","lang","file","line","conf","deg"]);
  if(!s.length) h+=emptyState("symbols");
  else h+="<table class='tv'><tr>"+th("label","Symbol",S.tsort)+th("kind","Kind",S.tsort)+th("lang","Lang",S.tsort)+th("file","Source",S.tsort)+th("conf","Conf",S.tsort)+th("deg","Degree",S.tsort)+"</tr>"+
    s.slice(0,500).map(r=>"<tr><td>"+rowLink(r.id,r.label)+"</td><td class='mono'>"+esc(r.kind||"")+"</td><td class='mono'>"+esc(r.lang||"—")+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td><td><span class='pill "+esc(r.conf)+"'>"+esc(r.conf)+"</span></td><td class='mono'>"+r.deg+"</td></tr>").join("")+"</table>"+
    (s.length>500?"<div class='empty'>Showing 500 of "+s.length+" — refine the filter.</div>":"");
  el.innerHTML=h; bindLinks(el); bindCopy(el,ds);
  bindSort(el,get,()=>renderSymbols(el,T));
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderSymbols(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
  $("sk").onchange=e=>{S.symKind=e.target.value;renderSymbols(el,T);};
  $("sl").onchange=e=>{S.symLang=e.target.value;renderSymbols(el,T);};
  $("sf").oninput=e=>{S.symFile=e.target.value;renderSymbols(el,T);const f=$("sf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderFlows(el,T){
  const flows=T.flows||[];
  const nCur=flows.filter(f=>f.curated).length;
  let h="<h1>Flows</h1><p class='lede'>Ordered evidenced steps — click any step to inspect."+
    (nCur?" "+nCur+" agent-curated flow"+(nCur>1?"s":"")+" first, then tool candidates."
      :" No curated business-flows/ yet — candidates below need agent review.")+"</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter flows…' value='"+esc(S.tfilter)+"'></div>";
  const got=filteredRows(flows,f=>f.seed+" "+(f.steps||[]).map(s=>s.label).join(" "));
  if(!got.length) h+=emptyState("flows");
  got.slice(0,100).forEach((f,i)=>{
    const tag=f.curated?"curated":"candidate";
    h+="<h2>"+(i+1)+". "+esc(f.seed||"flow")+" <span class='pill "+(f.complete?"HIGH":"LOW")+"'>"+(f.complete?"evidenced":"partial")+"</span> <span class='pill'>"+tag+"</span>"+
      (f.source?" <span class='mono' style='font-size:12px;color:var(--faint)'>"+esc(f.source)+"</span>":"")+"</h2>";
    (f.steps||[]).forEach((s,j)=>{
      h+="<div class='step'><span class='i'>"+(j+1)+"</span><span>"+(s.missing?esc(s.id):rowLink(s.id,s.label))+"</span><span class='mono' style='color:var(--faint);font-size:12px'>"+esc(s.kind||"")+"</span><span class='mono' style='margin-left:auto;font-size:12px'>"+srcLink(s.file,s.line)+"</span></div>";
    });
  });
  if(got.length>100) h+="<div class='empty'>Showing 100 of "+got.length+" — refine the filter.</div>";
  el.innerHTML=h; bindLinks(el);
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderFlows(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderCapabilities(el,T){
  const caps=T.capabilities||[];
  let h="<h1>Capabilities</h1><p class='lede'>Capability → requirements → realizing code → why. Intent is <span class='pill ASSERTED'>ASSERTED</span> (human/agent claim with author + source), kept separate from DERIVED scanner evidence below.</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter capabilities…' value='"+esc(S.tfilter)+"'></div>";
  const rows=[];
  caps.forEach(c=>{(c.requirements||[]).forEach(r=>rows.push({cap:c.title,capId:c.id,req:r}));});
  const got=filteredRows(rows,r=>r.cap+" "+r.req.title+" "+r.req.status+" "+(r.req.source||""));
  if(!caps.length) h+="<div class='empty'>No intent imported yet — run <span class='mono'>intent import</span> on docs/requirements.md, plan.md, decisions.md to populate this view. Code structure is unaffected.</div>";
  else if(!got.length) h+="<div class='empty'>No capabilities match this filter.</div>";
  got.forEach(({cap,capId,req})=>{
    const pill=req.status==="realized"?"HIGH":(req.status==="stale"?"MEDIUM":"LOW");
    h+="<h2>"+esc(cap)+" <span class='mono' style='font-weight:normal'>"+esc(capId)+"</span></h2>";
    h+="<table class='tv'><tr><th>Requirement</th><th>Status</th><th>Asserted by</th><th>Realizing code</th></tr>";
    h+="<tr><td>"+esc(req.title)+"<br><span class='mono'>"+esc(req.id)+"</span>"+(req.source?"<br><span class='mono'>"+esc(req.source)+"</span>":"")+"</td>"+
      "<td><span class='pill "+pill+"'>"+esc(req.status)+"</span> <span class='pill ASSERTED'>ASSERTED</span></td>"+
      "<td class='mono'>"+esc(req.asserted_by||"—")+(req.asserted_at?"<br>"+esc(req.asserted_at):"")+"</td>"+
      "<td>"+((req.code||[]).map(c=>rowLink(c.id,(c.label||c.id)+" ("+(c.kind||"?")+")")+ " <span class='mono'>"+srcLink(c.file,c.line)+"</span>"+(c.why?"<br><i>"+esc(c.why)+"</i>":"")).join("<br>")||"—")+"</td></tr></table>";
  });
  el.innerHTML=h; bindLinks(el);
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderCapabilities(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
}
function renderIssues(el,T){
  const issues=T.issues||[];
  const sevs=["HIGH","MEDIUM","LOW"];
  const cats0=[...new Set(issues.map(r=>r.category))].sort();
  let h="<h1>Issues</h1><p class='lede'>Validation findings, LOW-confidence items, unresolved refs, stale files.</p>";
  h+="<div class='toolbar'><input type='text' id='tf' placeholder='Filter issues…' value='"+esc(S.tfilter)+"'>"+
    "<select id='sv'><option value=''>all severities</option>"+sevs.map(s=>"<option value='"+s+"'"+(S.issueSev===s?" selected":"")+">"+s+"</option>").join("")+"</select>"+
    "<select id='sc'><option value=''>all categories</option>"+cats0.map(s=>"<option value='"+esc(s)+"'"+(S.issueCat===s?" selected":"")+">"+esc(s)+"</option>").join("")+"</select></div>";
  let rows=issues;
  if(S.issueSev) rows=rows.filter(r=>r.severity===S.issueSev);
  if(S.issueCat) rows=rows.filter(r=>r.category===S.issueCat);
  rows=filteredRows(rows,r=>r.category+" "+r.message+" "+(r.id||"")+" "+(r.file||""));
  const ds={is:copyOut(0,rows,["severity","category","message","id","file","line"])};
  h+=copyBar("is",rows,["severity","category","message","id","file","line"]);
  const cats={};
  rows.forEach(r=>{cats[r.category]=cats[r.category]||[];cats[r.category].push(r);});
  if(!rows.length) h+="<div class='empty'>No issues match — the map validates cleanly for this filter.</div>";
  Object.keys(cats).sort().forEach(c=>{
    h+="<h2>"+esc(c)+" ("+cats[c].length+")</h2><table class='tv'><tr>"+th("severity","Severity",S.tsort)+th("message","Finding",S.tsort)+th("id","Subject",S.tsort)+th("file","Source",S.tsort)+"</tr>"+
      cats[c].slice(0,300).map(r=>"<tr><td><span class='pill "+esc(r.severity)+"'>"+esc(r.severity)+"</span></td><td>"+esc(r.message)+"</td><td class='mono'>"+(r.id?rowLink(r.id,r.id):"—")+"</td><td class='mono'>"+srcLink(r.file,r.line)+"</td></tr>").join("")+"</table>";
  });
  el.innerHTML=h; bindLinks(el); bindCopy(el,ds);
  bindSort(el,(r,c)=>r[c],()=>renderIssues(el,T));
  $("tf").oninput=e=>{S.tfilter=e.target.value;renderIssues(el,T);const f=$("tf");f.focus();f.setSelectionRange(f.value.length,f.value.length);};
  $("sv").onchange=e=>{S.issueSev=e.target.value;renderIssues(el,T);};
  $("sc").onchange=e=>{S.issueCat=e.target.value;renderIssues(el,T);};
}

/* ---------- filters / legend / stats ---------- */
function renderFilters(){
  const fc=$("fconf"); fc.innerHTML="";
  Object.keys(S.conf).forEach(c=>{
    const n=DATA.nodes.filter(x=>x.conf===c).length;
    mkChk(fc, S.conf[c], " <span class='conf "+c+"'>"+c+"</span><span class='n'>"+n+"</span>",
      e=>{S.conf[c]=e.target.checked;rebuild();});
  });
  const fe=$("fetypes"); fe.innerHTML="";
  EDTYPES.forEach(t=>{
    const n=EDGES.filter(e=>e.type===t).length;
    mkChk(fe, S.etypes[t], " <span class='sw' style='background:"+(S.etypes[t]?"#58a6ff":"transparent")+"'></span><span>"+t+"</span><span class='n'>"+n+"</span>",
      e=>{S.etypes[t]=e.target.checked;rebuild();});
  });
  const fk=$("fkinds"); fk.innerHTML="";
  KINDS.slice(0,24).forEach(k=>{
    const n=DATA.nodes.filter(x=>x.kind===k).length;
    mkChk(fk, S.kinds[k], " <span>"+k+"</span><span class='n'>"+n+"</span>",
      e=>{S.kinds[k]=e.target.checked;rebuild();});
  });
  const fl=$("flangs"); fl.innerHTML="";
  if(!LANGS.length) fl.innerHTML="<div style='font-size:12px;color:var(--faint)'>none detected</div>";
  LANGS.forEach(l=>{
    const n=DATA.nodes.filter(x=>x.lang===l).length;
    mkChk(fl, S.langs[l], " <span>"+esc(l)+"</span><span class='n'>"+n+"</span>",
      e=>{S.langs[l]=e.target.checked;rebuild();});
  });
}
function updateStats(){
  $("statline").textContent = S.nodes.length+" / "+DATA.meta.view_nodes+" view nodes · "+S.edges.length+" edges";
  $("mapline").textContent = "· "+DATA.meta.graph_nodes+" nodes · "+DATA.meta.graph_edges+" edges · sync "+(DATA.meta.last_commit||"?");
}
function renderLegend(){
  $("legend").innerHTML="<div class='row'><b style='color:var(--fg)'>Legend</b></div>"+
    "<div class='row'>shape = kind (◆ endpoint · ■ service · ▬ table · ▲ event · ✕ test)</div>"+
    "<div class='row'><svg width='26' height='12'><line x1='1' y1='6' x2='25' y2='6' stroke='#8b949e' stroke-width='2'/></svg>HIGH — solid</div>"+
    "<div class='row'><svg width='26' height='12'><line x1='1' y1='6' x2='25' y2='6' stroke='#6e7681' stroke-width='1.5'/></svg>MEDIUM — lighter</div>"+
    "<div class='row'><svg width='26' height='12'><line x1='1' y1='6' x2='25' y2='6' stroke='#4a515c' stroke-width='1.2' stroke-dasharray='5,4'/></svg>LOW — dashed lead</div>"+
    "<div class='row'><svg width='26' height='12'><line x1='1' y1='6' x2='25' y2='6' stroke='#3d4450' stroke-dasharray='2,4'/></svg>UNKNOWN — faint</div>"+
    "<div class='row'>colour: layer column (left→right) + selection only</div>";
}

/* ---------- deep links ---------- */
function syncURL(){
  // file:// URLs are unique opaque origins: replaceState is blocked there
  // (Chrome logs "Unsafe attempt to load URL ... 'file:' URLs are treated
  // as unique security origins"). Skip it entirely on file:// — deep links
  // still work on load via bootFromURL, and http(s) serving keeps live sync.
  if(location.protocol==="file:") return;
  try {
    const p=new URLSearchParams(location.search);
    ["node","mode","view","impact","flow","endpoint","edge"].forEach(k=>p.delete(k));
    p.set("view",S.view);
    if(S.view==="graph"){
      p.set("mode",S.mode);
      if(S.sel&&NODES[S.sel]) p.set("node",NODES[S.sel].label);
      if(S.edgeSel) p.set("edge",S.edgeSel.s+"|"+S.edgeSel.t);
    }
    history.replaceState(null,"","?"+p.toString());
  } catch(e){}
}
function bootFromURL(){
  const p=new URLSearchParams(location.search);
  const b=BOOT.focus||{};
  const v=p.get("view");
  const VIEWS8=["overview","endpoints","data","dependencies","symbols","flows","capabilities","graph","issues"];
  if(v&&VIEWS8.includes(v)) S.view=v;
  else if(v&&(DATA.views||[]).includes(v)) S.view=v;
  else if(v&&EMODE[v]){ S.view="graph"; S.mode=v; }
  else if(b.flow&&!p.get("node")) S.view="flows";
  else if(b.impact&&!p.get("node")) S.view="graph";
  if(p.get("mode")&&EMODE[p.get("mode")]) S.mode=p.get("mode");
  else if(b.flow&&S.view==="graph") S.mode="flow";
  else if(b.impact&&S.view==="graph") S.mode="impact";
  else if(BOOT.mode&&EMODE[BOOT.mode]) S.mode=BOOT.mode;
  if(p.get("depth")){S.depth=+p.get("depth")||2;$("depthsel").value=String(S.depth);}
  else if(BOOT.depth){S.depth=BOOT.depth;$("depthsel").value=String(S.depth);}
  renderViews(); renderModes();
  let target=p.get("node")||p.get("endpoint")||b.node||null;
  if(p.get("impact")||b.impactId) target=target||p.get("impact")||b.impactId;
  if(p.get("edge")){
    const [s,t]=p.get("edge").split("|");
    const e=EDGES.find(x=>x.s===s&&x.t===t)||EDGES.find(x=>x.s===s||x.t===t);
    if(e){ S.view="graph"; renderViews(); S.edgeSel={s:e.s,t:e.t,type:e.type,conf:e.conf,file:e.file,line:e.line,ev:e.ev}; }
    return;
  }
  if(!target) return;
  const t=target.toLowerCase();
  const hit=DATA.search.find(s=>s.label.toLowerCase()===t)
    ||DATA.search.find(s=>s.label.toLowerCase().includes(t)||s.id.toLowerCase().includes(t));
  if(hit){
    const n=NODES[hit.id];
    S.view="graph"; renderViews();
    if(n&&S.mode==="architecture"&&n.group&&GROUPS["g:"+n.group]){
      S.group="g:"+n.group;
    }
    S.sel=hit.id;
  }
}

/* ---------- chrome ---------- */
$("fitbtn").onclick=()=>fitCam();
$("zfit").onclick=()=>fitCam();
$("resetbtn").onclick=()=>{S.sel=null;S.edgeSel=null;S.group=null;S.hist=[];S.hi=-1;S.tfilter="";S.tsort=null;S.depSort=null;S.symKind="";S.symLang="";S.symFile="";S.issueSev="";S.issueCat="";S.folded={};Object.keys(GROUPS).forEach(g=>{S.collapsed[g]=true;});pushHist();setView("overview");syncURL();};
$("zin").onclick=()=>zoom(1.25);
$("zout").onclick=()=>zoom(1/1.25);
$("depthsel").onchange=e=>{S.depth=+e.target.value;rebuild();};
$("helpbtn").onclick=()=>$("help").classList.add("show");
$("helpclose").onclick=()=>$("help").classList.remove("show");
$("help").onclick=e=>{if(e.target.id==="help")$("help").classList.remove("show");};
$("exportbtn").onclick=()=>{
  // SVG export of the CURRENT view (nodes+edges+labels)
  const r=$("graphpane").getBoundingClientRect();
  let xs=[],ys=[];
  S.nodes.forEach(x=>{const [a,b]=w2s(x._x,x._y);xs.push(a);ys.push(b);});
  if(!xs.length) return;
  const x0=Math.max(0,Math.min(...xs)-80),y0=Math.max(0,Math.min(...ys)-40);
  const NS="http://www.w3.org/2000/svg";
  let s="<svg xmlns='"+NS+"' width='"+Math.ceil(r.width)+"' height='"+Math.ceil(r.height)+"' style='background:#0d1117;font-family:sans-serif'>";
  S.edges.forEach(e=>{
    const a=S.byId[e.s],b=S.byId[e.t];if(!a||!b)return;
    const [x1,y1]=w2s(a._x,a._y),[x2,y2]=w2s(b._x,b._y);
    const dash=e.conf==="LOW"?" stroke-dasharray='5,4'":(e.conf==="UNKNOWN"?" stroke-dasharray='2,4'":"");
    s+="<line x1='"+(x1-x0).toFixed(1)+"' y1='"+(y1-y0).toFixed(1)+"' x2='"+(x2-x0).toFixed(1)+"' y2='"+(y2-y0).toFixed(1)+"' stroke='#4a5568' stroke-width='1'"+dash+"/>";
  });
  S.nodes.forEach(x=>{
    const [px,py]=w2s(x._x,x._y);
    s+="<rect x='"+(px-x0-6).toFixed(1)+"' y='"+(py-y0-6).toFixed(1)+"' width='12' height='12' fill='#0d1117' stroke='"+layerColor(x)+"' stroke-width='1.5'/>";
    s+="<text x='"+(px-x0+9).toFixed(1)+"' y='"+(py-y0+4).toFixed(1)+"' fill='#c9d4e0' font-size='12'>"+esc(x.label.slice(0,40))+"</text>";
  });
  s+="</svg>";
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([s],{type:"image/svg+xml"}));
  a.download="cartographer-"+S.view+"-"+S.mode+".svg";a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),5000);
};

/* ---------- boot ---------- */
(function boot(){
  document.title=BOOT.title||document.title;
  if(BOOT.stale&&BOOT.stale.length){
    const b=$("stalebar");
    b.textContent="Codebase map is stale ("+BOOT.stale.length+" changed files). Run cartographer sync before trusting this view.";
    b.style.display="block";
  }
  if(BOOT.focus&&BOOT.focus.flow&&!BOOT.focus.flow_complete){
    const w=$("flowwarn");
    w.textContent="Flow inference incomplete — source inspection required. Showing evidenced segments only.";
    w.style.display="block";
  }
  renderFilters(); renderLegend(); bootFromURL(); renderModes(); renderViews();
  pushHist();
  if(S.view==="graph"){ rebuild(); } else { renderTable(); renderCrumbs(); renderInsp(); updateStats(); }
  resize();
})();
