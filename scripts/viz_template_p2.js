/* Part 2: data prep + layered (Sugiyama-style) layout.
   Deterministic (no randomness): hash-seeded order everywhere, layers by
   architectural role left-to-right, barycentre crossing minimization
   (~60 lines), orthogonal/rounded edges, module containers collapsible.
   Progressive disclosure: start collapsed at container level, cap ~300
   nodes with an explicit banner, focus mode re-layouts the neighbourhood
   (non-neighbours leave the layout, not just fade). Every rendered node
   is labelled. Viewport culling kept in draw(). */
const FONT = (() => {
  // Single source: read --sans from CSS; never self-reference (TDZ crash).
  try {
    const v = getComputedStyle(document.documentElement)
      .getPropertyValue("--sans");
    if (v && v.trim()) return v.trim();
  } catch (e) { /* headless / no-DOM: fall through to literal */ }
  return "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif";
})();
const NODES = {}, EDGES = [];
DATA.nodes.forEach(n => NODES[n.id] = n);
DATA.edges.forEach(e => EDGES.push(e));
const GROUPS = {};
(DATA.groups||[]).forEach(g => GROUPS[g.id] = g);
const EMODE = DATA.modes || {};
const CAP = (DATA.caps && DATA.caps.graph_nodes) || 300;
const EDTYPES = [...new Set(EDGES.map(e=>e.type))].sort();
const KINDS = [...new Set(DATA.nodes.map(n=>n.kind))].sort();
const LANGS = [...new Set(DATA.nodes.map(n=>n.lang).filter(Boolean))].sort();
EDTYPES.forEach(t => S.etypes[t] = defaultEtype(t));
KINDS.forEach(k => S.kinds[k] = true);
LANGS.forEach(l => S.langs[l] = true);
// containers start collapsed (10-30 modules at top level)
Object.keys(GROUPS).forEach(g => { S.collapsed[g] = true; });
function defaultEtype(t){
  // data/call/dependency structure on; containment/impl noise on demand
  if(["defines","contains","imports"].includes(t)) return false;
  return true;
}

/* ---------- filters ---------- */
function passE(e){
  if(!S.etypes[e.type]) return false;
  if(!S.conf[e.conf]) return false;
  const a=NODES[e.s], b=NODES[e.t];
  if(!a||!b) return false;
  if(!S.kinds[a.kind]||!S.kinds[b.kind]) return false;
  if(a.lang&&!S.langs[a.lang]) return false;
  if(b.lang&&!S.langs[b.lang]) return false;
  return true;
}
function passN(n){
  if(!S.kinds[n.kind]) return false;
  if(n.lang&&!S.langs[n.lang]) return false;
  return true;
}

/* ---------- deterministic hash (no random seed anywhere) ---------- */
function hashStr(s){
  let h = 2166136261;
  for(let i=0;i<s.length;i++){ h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}

/* ---------- view builders ---------- */
function groupViewNodes(){
  // architecture default: GROUP nodes + inter-group edges (never hairball)
  const nodes = (DATA.groups||[]).map(g=>({id:g.id,label:g.label+"  ("+g.count+")",
    kind:"__group__",conf:"HIGH",deg:g.count,_g:g}));
  const emap = {};
  (EMODE.architecture?EMODE.architecture.edges:[]).forEach(([s,t])=>{
    if(!S.etypes[edgeTypeOf(s,t)]) return;
    const k=s+"→"+t;
    emap[k]=emap[k]||{s,t,type:edgeTypeOf(s,t),conf:"MEDIUM",n:0}; emap[k].n++;
  });
  return {nodes, edges:Object.values(emap)};
}
function edgeTypeOf(s,t){
  const gs=s.replace(/^g:/,""), gt=t.replace(/^g:/,"");
  const hit = EDGES.find(e=>{
    const a=NODES[e.s], b=NODES[e.t];
    return a&&b&&grp(a)===gs&&grp(b)===gt&&S.etypes[e.type]&&S.conf[e.conf];
  });
  return hit?hit.type:"references";
}
function grp(n){ return "g:"+((n&&n.group)||"misc"); }
function rawModeView(mode){
  const m = EMODE[mode]||{nodes:[],edges:[]};
  const keep = new Set(m.nodes.filter(id=>NODES[id]&&passN(NODES[id])));
  const edges = [];
  const byPair = {};
  EDGES.forEach(e=>{ byPair[e.s+"→"+e.t] = e; });
  (m.edges||[]).forEach(([s,t])=>{
    const e = byPair[s+"→"+t] || {s,t,type:"references",conf:"MEDIUM"};
    if(keep.has(s)&&keep.has(t)&&passE(e)) edges.push(e);
  });
  // include focused-view edges between kept nodes even if mode list missed
  EDGES.forEach(e=>{
    if(mode!=="impact"&&mode!=="flow") return;
    if(keep.has(e.s)&&keep.has(e.t)&&passE(e)&&!edges.includes(e)) edges.push(e);
  });
  return {nodes:[...keep].map(id=>NODES[id]), edges};
}
function focusView(centerId, depth){
  const seen=new Set([centerId]); let fr=new Set([centerId]);
  for(let d=0;d<depth;d++){
    const nx=new Set();
    // deterministic neighbour order (hash of edge endpoints)
    const sorted = EDGES.slice().sort((a,b)=>hashStr(a.s+a.t)-hashStr(b.s+b.t));
    fr.forEach(fid=>{
      sorted.forEach(e=>{
        if(!passE(e)) return;
        let o=null;
        if(e.s===fid) o=e.t; else if(e.t===fid) o=e.s;
        if(o&&NODES[o]&&passN(NODES[o])&&!seen.has(o)){seen.add(o);nx.add(o);}
      });
    });
    fr=nx; if(!fr.size) break;
    if(seen.size>CAP) break;
  }
  const edges=EDGES.filter(e=>seen.has(e.s)&&seen.has(e.t)&&passE(e));
  return {nodes:[...seen].map(id=>NODES[id]).filter(Boolean), edges};
}
/* cap to ~300 with explicit banner state (deterministic rank: conf, degree, id) */
function capView(v){
  const total = v.nodes.length;
  if(total <= CAP){ S.truncated = null; return v; }
  const rank = {HIGH:0, MEDIUM:1, LOW:2, UNKNOWN:3};
  const must = new Set([S.sel, S.hover].filter(Boolean));
  const rest = v.nodes.filter(n=>!must.has(n.id))
    .sort((a,b)=>(rank[a.conf]??3)-(rank[b.conf]??3)
      || (b.deg||0)-(a.deg||0) || (a.id<b.id?-1:1));
  const keep = new Set([...must].filter(id=>NODES[id]));
  rest.slice(0, Math.max(0, CAP-keep.size)).forEach(n=>keep.add(n.id));
  const edges = v.edges.filter(e=>keep.has(e.s)&&keep.has(e.t));
  S.truncated = {shown: keep.size, total};
  return {nodes:[...keep].map(id=>NODES[id]).filter(Boolean), edges};
}
function rebuild(){
  // Loading veil: force layout on large views blocks the main thread, so
  // show the veil first and yield a frame before the heavy work. Without
  // the yield the browser never paints the veil (and the canvas keeps its
  // stale frame, which looked like "mode buttons do nothing").
  const veil=$("veil");
  if(veil){
    const vm=$("veilmsg");
    if(vm) vm.textContent="Loading…";
    veil.classList.add("show");
  }
  const run=()=>{
    try{ rebuildNow(); } finally{ if(veil) veil.classList.remove("show"); }
  };
  if(window.requestAnimationFrame) requestAnimationFrame(()=>setTimeout(run,0));
  else setTimeout(run,0);
}
function rebuildNow(){
  S._containers = [];  // stale header boxes must never intercept clicks
  let v;
  if(S.sel && NODES[S.sel]) v = focusView(S.sel, S.depth);
  else if(S.mode==="architecture" && !S.group) v = groupViewNodes();
  else if(S.mode==="architecture" && S.group){
    const g = GROUPS[S.group];
    const ids = (g?g.members:[]).filter(id=>NODES[id]&&passN(NODES[id]));
    const keep = new Set(ids);
    v = {nodes:ids.map(id=>NODES[id]),
         edges:EDGES.filter(e=>keep.has(e.s)&&keep.has(e.t)&&passE(e))};
  }
  else v = rawModeView(S.mode);
  // Focus mode removes the non-neighbourhood from the layout entirely:
  // focusView already returns only the neighbourhood (not a fade).
  // Header-folded groups leave every layout the same way.
  if(S.view==="graph" && Object.keys(S.folded).length){
    const cut = new Set(Object.keys(S.folded));
    v = {nodes: v.nodes.filter(n=>n.kind==="__group__"||!cut.has(n.group)),
         edges: v.edges.filter(e=>{
           const a=NODES[e.s], b=NODES[e.t];
           return !(a&&cut.has(a.group)) && !(b&&cut.has(b.group));
         })};
  }
  v = capView(v);
  S.nodes=v.nodes; S.edges=v.edges;
  const vm=$("veilmsg");
  if(vm) vm.textContent="Laying out "+S.nodes.length+" nodes…";
  S.byId={}; S.adj={}; S.inadj={};
  S.nodes.forEach(n=>{S.byId[n.id]=n;S.adj[n.id]=[];S.inadj[n.id]=[];});
  S.edges.forEach(e=>{
    if(S.adj[e.s])S.adj[e.s].push(e.t);
    if(S.inadj[e.t])S.inadj[e.t].push(e.s);
  });
  layoutLayered();
  renderBanner(); renderCrumbs(); renderInsp(); updateStats(); draw();
}

/* ---------- Sugiyama-style layered layout (deterministic) ----------
   Assign: layer by architectural role -> order within layer by barycentre
   sweeps -> x = layer column, y = order position. Containers are one
   column-header row; collapsed containers render as a single node. */
const LAYER_X_GAP = 210, LAYER_Y_GAP = 46;
function layerOf(n){
  return (n && n.layer) || "services";
}
function layoutLayered(){
  const n = S.nodes.length;
  if(!n) return;
  if(S.mode==="architecture" && !S.sel && !S.group){
    // ring layout for group containers: stable, readable, no physics
    const R=Math.max(200, n*26);
    S.nodes.forEach((x,i)=>{const a=i/n*Math.PI*2-Math.PI/2;
      x._x=Math.cos(a)*R; x._y=Math.sin(a)*R*0.72; x._r=nodeR(x);});
    S.cam={x:0,y:0,k:Math.min(1.2, 520/Math.max(R,1))};
    return;
  }
  // --- assign layers (architectural role; groups keep member majority)
  const li = {}; LAYERS.forEach((l,i)=>li[l]=i);
  const order = S.nodes.slice().sort((a,b)=>a.id<b.id?-1:1);
  const buckets = LAYERS.map(()=>[]);
  order.forEach(x=>{ buckets[li[layerOf(x)] ?? 3].push(x); });
  // empty layers collapse out (keep left-to-right compactness)
  const cols = buckets.filter(b=>b.length);
  // --- order: barycentre sweeps (deterministic, ~4 passes)
  const pos = {}; // id -> index within column
  cols.forEach((col,c)=>col.forEach((x,i)=>pos[x.id]=i));
  const edgeList = S.edges.filter(e=>S.byId[e.s]&&S.byId[e.t]);
  for(let sweep=0; sweep<4; sweep++){
    const fwd = sweep % 2 === 0;
    const seq = fwd ? cols.map((_,i)=>i) : cols.map((_,i)=>cols.length-1-i);
    seq.forEach(c=>{
      if(fwd && c===0) return;
      if(!fwd && c===cols.length-1) return;
      const ref = fwd ? cols[c-1] : cols[c+1];
      const refPos = {}; ref.forEach((x,i)=>refPos[x.id]=i);
      const scored = cols[c].map(x=>{
        const nbs = fwd
          ? edgeList.filter(e=>e.t===x.id&&refPos[e.s]!=null).map(e=>refPos[e.s])
          : edgeList.filter(e=>e.s===x.id&&refPos[e.t]!=null).map(e=>refPos[e.t]);
        const bar = nbs.length
          ? nbs.reduce((a,b)=>a+b,0)/nbs.length
          : hashStr(x.id) % 997 / 997 * cols[c].length;
        return {x, bar};
      });
      scored.sort((a,b)=>a.bar-b.bar || (a.x.id<b.x.id?-1:1));
      cols[c] = scored.map(s=>s.x);
      cols[c].forEach((x,i)=>pos[x.id]=i);
    });
  }
  // --- position: x = column, y = centred order; deterministic jitter
  // breaks exact overlaps of same-barycentre nodes. Narrow viewports
  // (<720px canvas) transpose: layers run top-to-bottom instead of L-to-R.
  const gp = (typeof document!=="undefined") && $("graphpane");
  const narrow = gp && gp.clientWidth > 0 && gp.clientWidth < 720;
  S.narrow = !!narrow;
  const maxRows = Math.max(...cols.map(c=>c.length), 1);
  cols.forEach((col,c)=>{
    col.forEach((x,i)=>{
      x._r = nodeR(x);
      const px = c * LAYER_X_GAP;
      const py = (i - (col.length-1)/2) * LAYER_Y_GAP
        + ((hashStr(x.id) % 100)-50)/50 * 7;
      x._x = narrow ? py : px;
      x._y = narrow ? px : py;
      x._col = c; x._row = i;
    });
  });
  // --- de-overlap sweep (same column, bounded, deterministic): 3 passes
  for(let sweep=0; sweep<3; sweep++){
    let moved=false;
    cols.forEach(col=>{
      const sorted = col.slice().sort((a,b)=>a._y-b._y);
      for(let i=1;i<sorted.length;i++){
        const need=(sorted[i-1]._r||9)+(sorted[i]._r||9)+8;
        if(sorted[i]._y - sorted[i-1]._y < need){
          sorted[i]._y = sorted[i-1]._y + need; moved=true;
        }
      }
    });
    if(!moved) break;
  }
  // finite guard (snapshot-testable contract: never NaN)
  S.nodes.forEach((x,i)=>{
    if(!isFinite(x._x)||!isFinite(x._y)){x._x=(i%37)*17-300;x._y=(i%41)*13-260;}});
  // center + fit
  let mx=0,my=0; S.nodes.forEach(x=>{mx+=x._x;my+=x._y;});
  if(n){mx/=n;my/=n; S.nodes.forEach(x=>{x._x-=mx;x._y-=my;});}
  S.layerCols = cols.map(col=>col.map(x=>x.id));
  S.layerMaxRows = maxRows;
  fitCam(false);
}
function nodeR(x){
  if(x.kind==="__group__") return 26+Math.min(26,Math.sqrt(x._g?x._g.count:10)*3);
  const base={"endpoint":13,"route":13,"service":15,"controller":15,"table":12,
    "database":15,"external-service":14,"component":12}[x.kind]||9;
  return base+Math.min(8,(x.deg||0)*0.5);
}
/* orthogonal rounded edge path through layer columns */
function edgePath(a,b){
  const x1=a._x, y1=a._y, x2=b._x, y2=b._y;
  if(a._col==null || b._col==null || a._col===b._col) return null;
  const mx = (x1+x2)/2;
  return {mx};
}
/* hover: full in/out paths (transitive neighbourhood, capped) */
function pathSet(id){
  const ns = new Set([id]), es = new Set();
  let fr = [id];
  for(let d=0; d<4 && fr.length; d++){
    const nx = [];
    fr.forEach(fid=>{
      EDGES.forEach(e=>{
        if(!S.byId[e.s]||!S.byId[e.t]) return;
        let o=null;
        if(e.s===fid) o=e.t; else if(e.t===fid) o=e.s;
        if(o && !ns.has(o) && ns.size<200){ ns.add(o); nx.push(o); }
        if(o) es.add(e.s+"→"+e.t+"→"+e.type);
      });
    });
    fr = nx;
    if(ns.size>=200) break;
  }
  return {nodes:ns, edges:es};
}

/* ---------- canvas render (DPR-aware, viewport-culled, SVG-exportable) ---------- */
const cv=$("cv"), ctx=cv.getContext("2d");
let DPR=1;
function resize(){
  DPR=Math.min(2,window.devicePixelRatio||1);
  const r=$("graphpane").getBoundingClientRect();
  if(r.width<2||r.height<2) return; // table view visible: keep last size
  cv.width=r.width*DPR; cv.height=r.height*DPR;
  if(S.view==="graph") draw();
}
window.addEventListener("resize", resize);
function w2s(x,y){return [(x-S.cam.x)*S.cam.k+cv.width/DPR/2,(y-S.cam.y)*S.cam.k+cv.height/DPR/2];}
function s2w(px,py){return [(px-cv.width/DPR/2)/S.cam.k+S.cam.x,(py-cv.height/DPR/2)/S.cam.k+S.cam.y];}
function draw(){
  ctx.setTransform(DPR,0,0,DPR,0,0);
  const W=cv.width/DPR,H=cv.height/DPR;
  ctx.clearRect(0,0,W,H);
  if(!S.nodes.length){
    // empty-state: never a silent blank canvas
    ctx.globalAlpha=1; ctx.fillStyle="#8b949e";
    ctx.font="13px "+FONT; ctx.textAlign="center";
    const cxm=W/2, cym=H/2-8;
    ctx.fillText("This view is empty for the current selection + filters.",cxm,cym);
    ctx.fillStyle="#5b6572"; ctx.font="12px "+FONT;
    ctx.fillText("Click the Graph crumb (top-left) to reset, or enable LOW / UNKNOWN on the left.",cxm,cym+20);
    return;
  }
  const inPath = S.hoverPath ? S.hoverPath.nodes : null;
  const inEdge = S.hoverPath ? S.hoverPath.edges : null;
  // layer column headers + container outlines
  drawLayers(W,H);
  // edges — LOW/UNKNOWN first, HIGH last (on top); hover path highlights,
  // selection neighbourhood stays, rest fades. Orthogonal/rounded for
  // cross-layer edges, straight arrows within a layer.
  const erank=e=>({"UNKNOWN":0,"LOW":1,"MEDIUM":2,"HIGH":3}[e.conf]??2);
  const drawEdges=[...S.edges].sort((a,b)=>erank(a)-erank(b));
  const sel=S.sel;
  const edgeKey = e => e.s+"→"+e.t+"→"+e.type;
  drawEdges.forEach(e=>{
    const a=S.byId[e.s],b=S.byId[e.t];
    if(!a||!b) return;
    const st=CONF_STYLE[e.conf]||CONF_STYLE.MEDIUM;
    const [x1,y1]=w2s(a._x,a._y),[x2,y2]=w2s(b._x,b._y);
    if(Math.max(x1,x2)<-80||Math.min(x1,x2)>W+80||Math.max(y1,y2)<-80||Math.min(y1,y2)>H+80) return; // viewport culling
    const hot = (S.edgeSel && S.edgeSel.s===e.s && S.edgeSel.t===e.t && S.edgeSel.type===e.type)
      || (S.sel&&(e.s===S.sel||e.t===S.sel));
    const onPath = inEdge && inEdge.has(edgeKey(e));
    const dimmed = (sel&&!hot&&!onPath) || (inPath&&!onPath&&!hot);
    ctx.strokeStyle = hot?"#58a6ff":(onPath?"#9ecbff":colorFor(e.conf));
    ctx.globalAlpha = hot?0.95:(onPath?0.85:(dimmed?0.06:st.a));
    ctx.lineWidth = hot?2.2:(onPath?2:st.w);
    ctx.setLineDash(st.dash);
    drawEdgeLine(x1,y1,x2,y2,a,b,onPath);
    ctx.setLineDash([]);
    // arrow for directed types
    if(["calls","reads","writes","injects","handled-by","consumes","depends-on","queries","invokes","publishes","transforms","navigates","seeds","creates","modifies"].includes(e.type)){
      const ang = (a._col!=null&&b._col!=null&&a._col!==b._col)
        ? Math.atan2(y2-y1,x2-x1) : Math.atan2(y2-y1,x2-x1);
      const L=7;
      ctx.globalAlpha=hot?0.95:(onPath?0.85:(dimmed?0.06:st.a));
      ctx.fillStyle=hot?"#58a6ff":(onPath?"#9ecbff":colorFor(e.conf));
      ctx.beginPath();
      ctx.moveTo(x2-Math.cos(ang)*(b._r*S.cam.k+2),y2-Math.sin(ang)*(b._r*S.cam.k+2));
      ctx.lineTo(x2-Math.cos(ang-0.42)*(L+ b._r*S.cam.k*0.3),y2-Math.sin(ang-0.42)*(L+b._r*S.cam.k*0.3));
      ctx.lineTo(x2-Math.cos(ang+0.42)*(L+ b._r*S.cam.k*0.3),y2-Math.sin(ang+0.42)*(L+b._r*S.cam.k*0.3));
      ctx.closePath();ctx.fill();
    }
  });
  ctx.globalAlpha=1;
  // nodes — EVERY rendered node labelled (collapse instead of hiding)
  S.nodes.forEach(x=>{
    const [px,py]=w2s(x._x,x._y), r=Math.max(3,x._r*S.cam.k);
    if(px<-80||py<-40||px>W+80||py>H+40) return; // viewport culling
    const isSel=x.id===S.sel, isHov=x.id===S.hover;
    const onPath = inPath && inPath.has(x.id);
    const col=layerColor(x);
    const confA={HIGH:1,MEDIUM:0.85,LOW:0.6,UNKNOWN:0.45}[x.conf]||1;
    ctx.globalAlpha=(inPath&&!onPath&&!isSel)?0.25:confA;
    drawGlyph(x,px,py,r,col,isSel,isHov||onPath);
    // LOW/UNKNOWN badge: dashed halo (not color alone)
    if(x.conf==="LOW"||x.conf==="UNKNOWN"){
      ctx.globalAlpha=0.7;ctx.strokeStyle=x.conf==="LOW"?"#8b949e":"#3d4450";
      ctx.setLineDash([3,3]);ctx.lineWidth=1.2;
      ctx.beginPath();ctx.arc(px,py,r+4,0,7);ctx.stroke();ctx.setLineDash([]);
      ctx.globalAlpha=confA;
    }
    // label: always drawn; collapse text (not node) when tight
    ctx.globalAlpha=1;
    ctx.font=(isSel?"650 ":"")+"12px "+FONT;
    ctx.fillStyle=isSel?"#fff":(x.conf==="HIGH"?"#dbe4ee":"#8b949e");
    ctx.textAlign = (x._col!=null) ? "left" : "center";
    const lx = (x._col!=null) ? px+r+5 : px;
    const ly = (x._col!=null) ? py+4 : py+r+15;
    ctx.fillText(fitLabel(x.label, r, x._col!=null), lx, ly);
  });
  ctx.globalAlpha=1; ctx.textAlign="left";
}
function drawLayers(W,H){
  if(S.mode==="architecture"&&!S.sel&&!S.group) return;
  if(!S.layerCols || !S.layerCols.length) return;
  // column label strip (layer names left-to-right, top-to-bottom narrow)
  ctx.font="12px "+FONT; ctx.textAlign="left";
  const cols = S.layerCols;
  cols.forEach((col,c)=>{
    if(!col.length) return;
    const a=S.byId[col[0]];
    if(!a) return;
    ctx.fillStyle="#5b6572";
    if(S.narrow){
      const [,sy]=w2s(0,a._y);
      ctx.fillText(layerName(c), 8, Math.max(14,sy-8));
    } else {
      const [sx]=w2s(a._x,0);
      ctx.fillText(layerName(c), Math.max(8,sx-70), 18);
    }
  });
  // real container boundaries: rounded outline + label per group with
  // 2+ members on screen. Clicking a header folds that group (its
  // members leave the layout; the architecture ring shows the summary).
  S._containers = [];
  const seen = {};
  S.nodes.forEach(x=>{
    if(x.kind==="__group__") return;
    const g = x.group;
    if(!g || S.folded[g]) return;  // header-folded: leave layout entirely
    (seen[g]=seen[g]||[]).push(x);
  });
  Object.keys(seen).forEach(g=>{
    const ms = seen[g].filter(x=>isFinite(x._x)&&isFinite(x._y));
    if(ms.length<2) return;
    let x0=1e18,y0=1e18,x1=-1e18,y1=-1e18;
    ms.forEach(x=>{x0=Math.min(x0,x._x-x._r);y0=Math.min(y0,x._y-x._r);
      x1=Math.max(x1,x._x+x._r);y1=Math.max(y1,x._y+x._r);});
    const [sx0,sy0]=w2s(x0,y0), [sx1,sy1]=w2s(x1,y1);
    const pad=14;
    ctx.strokeStyle="rgba(88,166,255,0.35)"; ctx.lineWidth=1.2;
    ctx.setLineDash([6,4]);
    ctx.beginPath();
    if(ctx.roundRect) ctx.roundRect(sx0-pad,sy0-pad-16,sx1-sx0+pad*2,sy1-sy0+pad*2+16,8);
    else ctx.rect(sx0-pad,sy0-pad-16,sx1-sx0+pad*2,sy1-sy0+pad*2+16);
    ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle="#8b9eb5"; ctx.font="12px "+FONT; ctx.textAlign="left";
    const label = g+" ("+ms.length+") — click header to fold";
    ctx.fillText(label.slice(0,48), sx0-pad+4, sy0-pad-4);
    S._containers.push({g, sx:sx0-pad, sy:sy0-pad-16,
      w:sx1-sx0+pad*2, h:sy1-sy0+pad*2+16});
  });
  ctx.textAlign="left";
}
function layerName(c){
  // visible columns map back to non-empty layers in order
  return LAYERS.filter(l=>{
    const li={}; LAYERS.forEach((x,i)=>li[x]=i);
    return S.nodes.some(n=>((n.layer)||"services")===l);
  })[c] || ("layer "+(c+1));
}
function drawEdgeLine(x1,y1,x2,y2,a,b,onPath){
  const crossLayer = a._col!=null && b._col!=null && a._col!==b._col;
  if(!crossLayer){ ctx.beginPath();ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);ctx.stroke(); return; }
  // orthogonal with rounded corner via quadratic midpoint
  const mx=(x1+x2)/2;
  ctx.beginPath();
  ctx.moveTo(x1,y1);
  ctx.lineTo(mx-8,y1);
  ctx.quadraticCurveTo(mx,y1,mx,(y1+y2)/2);
  ctx.quadraticCurveTo(mx,y2,mx+8,y2);
  ctx.lineTo(x2,y2);
  ctx.stroke();
}
function colorFor(conf){return {HIGH:"#8b949e",MEDIUM:"#6e7681",LOW:"#4a515c",UNKNOWN:"#3d4450"}[conf]||"#6e7681";}
function fitLabel(t,r,side){
  const m=side?Math.max(14,26):Math.max(8,Math.floor(r*0.9));
  t=String(t||"");
  return t.length>m?t.slice(0,m-1)+"…":t;
}
function drawGlyph(x,px,py,r,col,sel,hov){
  const g=kindGlyph(x.kind);
  // unmistakable selection: outline + color + thicker ring (header in p3)
  ctx.fillStyle="#0d1117";ctx.strokeStyle=sel?"#fff":col;ctx.lineWidth=sel?3:(hov?2.4:1.6);
  ctx.beginPath();
  if(g==="sq"||g==="rsq"){
    const rr=g==="rsq"?4:1.5;
    ctx.roundRect?ctx.roundRect(px-r,py-r,r*2,r*2,rr):ctx.rect(px-r,py-r,r*2,r*2);
  }
  else if(g==="dia"){ctx.moveTo(px,py-r);ctx.lineTo(px+r,py);ctx.lineTo(px,py+r);ctx.lineTo(px-r,py);ctx.closePath();}
  else if(g==="tri"){ctx.moveTo(px,py-r);ctx.lineTo(px+r*0.95,py+r*0.8);ctx.lineTo(px-r*0.95,py+r*0.8);ctx.closePath();}
  else if(g==="oct"){const k=0.42;ctx.moveTo(px-r*k,py-r);ctx.lineTo(px+r*k,py-r);ctx.lineTo(px+r,py-r*k);ctx.lineTo(px+r,py+r*k);ctx.lineTo(px+r*k,py+r);ctx.lineTo(px-r*k,py+r);ctx.lineTo(px-r,py+r*k);ctx.lineTo(px-r,py-r*k);ctx.closePath();}
  else if(g==="hex"){for(let i=0;i<6;i++){const a=i/6*Math.PI*2-Math.PI/2;const X=px+Math.cos(a)*r,Y=py+Math.sin(a)*r;i?ctx.lineTo(X,Y):ctx.moveTo(X,Y);}ctx.closePath();}
  else if(g==="cyl"){ctx.ellipse(px,py,r,r*0.72,0,0,7);}
  else if(g==="x"){ctx.moveTo(px-r,py-r);ctx.lineTo(px+r,py+r);ctx.moveTo(px+r,py-r);ctx.lineTo(px-r,py+r);ctx.stroke();if(sel||hov){ctx.beginPath();ctx.arc(px,py,r+2,0,7);ctx.stroke();}return;}
  else if(g==="dot"){ctx.arc(px,py,Math.max(2.5,r*0.5),0,7);}
  else if(g==="flag"){ctx.moveTo(px-r,py+r);ctx.lineTo(px-r,py-r);ctx.lineTo(px+r*0.6,py-r*0.4);ctx.lineTo(px-r,py);ctx.closePath();}
  else {ctx.arc(px,py,r,0,7);}
  ctx.fill();ctx.stroke();
  // icon dot: kind initial for quick scan (shape+letter, not color alone)
  if(r>9&&["sq","rsq","cir","dia","oct","hex","cyl"].includes(g)){
    ctx.fillStyle=col;ctx.globalAlpha*=0.9;
    ctx.font="650 "+Math.max(8,r*0.85)+"px "+FONT;
    ctx.textAlign="center";ctx.textBaseline="middle";
    ctx.fillText((x.label||"?").trim()[0].toUpperCase(),px,py+0.5);
    ctx.textBaseline="alphabetic";
  }
  if(sel||hov){ctx.strokeStyle=sel?"#fff":col;ctx.globalAlpha=0.9;ctx.lineWidth=sel?2.4:1.2;
    ctx.beginPath();ctx.arc(px,py,r+5,0,7);ctx.stroke();}
}
