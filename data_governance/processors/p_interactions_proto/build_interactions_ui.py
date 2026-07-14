"""THROWAWAY — build a standalone HTML view of the entity-interaction graph.

Runs the P-interactions extractor over the `travel_agent_II` fixture and
emits a self-contained `interactions_graph.html`: a graph of entities with each
interaction drawn as a numbered directed edge (numbered by row in the
time+order sort, so the execution order is explicit) plus a synchronised
numbered table. Pure inspection aid; not wired into the app.

    python -m data_governance.processors.p_interactions_proto.build_interactions_ui
"""

from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict
from pathlib import Path

from data_governance.retrieval import Span

from . import extractor as E

_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests/processors/p_interactions_proto/fixtures/travel_agent_II.json"
)
_OUT = Path(__file__).resolve().parent / "interactions_graph.html"


def _parse_dt(v):
    return dt.datetime.fromisoformat(v) if v else None


def _row_to_span(r: dict) -> Span:
    return Span(
        seq=r["seq"], trace_id=r["trace_id"], span_id=r["span_id"],
        parent_id=r["parent_id"], name=r["name"],
        started_at=_parse_dt(r["started_at"]), attributes=r["attributes"] or {},
        observed_at=_parse_dt(r["observed_at"]), arrival_seq=r["arrival_seq"],
        service_name=r.get("service_name"), kind=r.get("kind"),
        error=r.get("error"), status_message=r.get("status_message"),
        events=r.get("events"), links=r.get("links"),
        ended_at=_parse_dt(r.get("ended_at")), otlp=r.get("otlp"),
        scope=r.get("scope"), resource_attributes=r.get("resource_attributes"),
    )


def _iso(x):
    return x.isoformat() if x else None


def build_data() -> dict:
    rows = json.loads(_FIXTURE.read_text())
    spans = [_row_to_span(r) for r in rows]
    res = E.extract(spans)

    by_ent = {e.id: e for e in res.entities}
    anchor = {
        isp.interaction_id: isp.span_id
        for isp in res.interaction_spans if isp.is_anchor
    }

    entities = [
        dict(
            id=e.id, key=e.natural_key, name=e.display_name,
            inferred=e.inferred, scope=e.scope_name,
        )
        for e in res.entities
    ]

    ordered = sorted(res.interactions, key=lambda x: x.order)

    # Call vs. response: derived STRUCTURALLY, not from `order` parity.
    # There is no explicit call/response marker on ProtoInteraction (nor on the
    # EntityEdge it comes from) — the `_order_execution_walk` walk knows a chain's
    # "call" and "resp" edges but never stamps that onto either object. Under the
    # new global-ordinal walk each chain assigns its request `order` then (after
    # recursing into its subtree) its response `order`, so within a single
    # (X,Y)/(Y,X) mirrored pair the interactions nest as balanced parentheses on
    # the `order` sequence: a call opens, its return leg closes, and any nested
    # same-pair chain is fully contained. So we recover call/response by a LIFO
    # stack match over the ordered interactions, keyed by the *unordered* entity
    # pair: an interaction whose reverse direction is already open on that pair's
    # stack is that open call's RESPONSE (and pops it); otherwise it is a new
    # call (and is pushed). This matches the walk exactly (every leg pairs; zero
    # unmatched) and is fully deterministic (driven by `order` alone).
    is_response: dict[str, bool] = {}
    pair_stack: dict[frozenset, list] = defaultdict(list)
    for it in ordered:
        st = pair_stack[frozenset((it.caller_entity_id, it.callee_entity_id))]
        matched = None
        for idx in range(len(st) - 1, -1, -1):
            o = st[idx]
            if (o.caller_entity_id == it.callee_entity_id
                    and o.callee_entity_id == it.caller_entity_id):
                matched = idx
                break
        if matched is not None:
            is_response[it.id] = True
            st.pop(matched)
        else:
            is_response[it.id] = False
            st.append(it)

    interactions = []
    for row, it in enumerate(ordered, start=1):
        c, t = by_ent[it.caller_entity_id], by_ent[it.callee_entity_id]
        interactions.append(dict(
            row=row,
            caller_id=it.caller_entity_id, callee_id=it.callee_entity_id,
            caller=c.natural_key, callee=t.natural_key,
            caller_name=c.display_name, callee_name=t.display_name,
            started_at=_iso(it.started_at), ended_at=_iso(it.ended_at),
            order=it.order, error=bool(it.error), anchor=anchor.get(it.id),
            summary=it.summary, is_response=is_response[it.id],
        ))
    return dict(entities=entities, interactions=interactions)


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>P-interactions — entity interaction graph (travel_agent_II)</title>
<style>
  :root {
    --bg:#0f1115; --panel:#171a21; --line:#2a2f3a; --fg:#e6e9ef; --muted:#8b93a7;
    --agent:#4f8cff; --tool:#f0a53e; --llm:#c66bff;
    --edge:#5b6478; --edge-hi:#ffd24a; --row-hi:#232838;
  }
  * { box-sizing:border-box; }
  body { margin:0; font:13px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
    background:var(--bg); color:var(--fg); }
  header { padding:12px 18px; border-bottom:1px solid var(--line); }
  header h1 { font-size:15px; margin:0 0 3px; font-weight:600; }
  header .sub { color:var(--muted); font-size:12px; }
  .wrap { display:flex; flex-direction:column; height:calc(100vh - 58px); }
  .graph { flex:0 0 58%; min-height:0; position:relative; border-bottom:1px solid var(--line); }
  .side { flex:1 1 42%; min-height:0; overflow:auto; background:var(--panel); }
  svg { width:100%; height:100%; display:block; }
  .node rect { stroke-width:1.5; }
  .node text { fill:var(--fg); font-size:12px; font-weight:600; pointer-events:none; }
  .node .sub { fill:var(--muted); font-size:10px; font-weight:400; }
  .node.dim { opacity:0.25; }
  .edge { fill:none; stroke:var(--edge); stroke-width:1.4; opacity:0.55; }
  .edge.call { }
  .edge.resp { stroke-dasharray:4 3; }
  .edge.hi { stroke:var(--edge-hi); stroke-width:2.6; opacity:1; }
  .edge.dim { opacity:0.08; }
  .edgelabel { font-size:10px; fill:var(--fg); }
  .edgelabel circle { fill:#20242e; stroke:var(--edge); stroke-width:1; }
  .edgelabel.hi circle { fill:var(--edge-hi); stroke:var(--edge-hi); }
  .edgelabel.hi text { fill:#000; font-weight:700; }
  .edgelabel.dim { opacity:0.12; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th,td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line);
    white-space:nowrap; }
  th { position:sticky; top:0; background:#1d2129; color:var(--muted);
    font-weight:600; z-index:1; }
  td.num { text-align:right; color:var(--muted); font-variant-numeric:tabular-nums; }
  tr.self td { color:var(--muted); }
  tr.hi { background:var(--row-hi); }
  tr:hover { background:var(--row-hi); cursor:default; }
  .pill { display:inline-block; padding:0 6px; border-radius:9px; font-size:10px;
    line-height:16px; margin-left:5px; }
  .pill.inf { background:#3a2d10; color:#f0a53e; }
  .badge { display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }
  .dir { color:var(--muted); }
  .filters { padding:8px 12px; border-bottom:1px solid var(--line); display:flex;
    gap:14px; align-items:center; flex-wrap:wrap; }
  .filters label { color:var(--muted); font-size:12px; cursor:pointer; user-select:none; }
  .legend { display:flex; gap:14px; align-items:center; color:var(--muted); font-size:11px; }
  code { background:#20242e; padding:1px 4px; border-radius:3px; font-size:11px; }
  .mono { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
</style>
</head>
<body>
<header>
  <h1>P-interactions — entity interaction graph</h1>
  <div class="sub">Fixture <code>travel_agent_II</code> · <span id="entCount"></span> entities ·
    <span id="ixCount"></span> interactions. Edges numbered by row in the
    global ordinal <code>order</code> sort — the number is the execution position.
    Call = solid, response = dashed. Hover a row or an edge number to highlight.</div>
</header>
<div class="filters">
  <label><input type="checkbox" id="showSelf" checked> show self-loops (A→A)</label>
  <div class="legend">
    <span><span class="badge" style="background:var(--agent)"></span>agent</span>
    <span><span class="badge" style="background:var(--tool)"></span>tool</span>
    <span><span class="badge" style="background:var(--llm)"></span>llm</span>
  </div>
</div>
<div class="wrap">
  <div class="graph">
    <svg id="svg" viewBox="0 0 900 640" preserveAspectRatio="xMidYMid meet">
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
          markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#5b6478"/>
        </marker>
        <marker id="arrowHi" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8"
          markerHeight="8" orient="auto-start-reverse">
          <path d="M0 0 L10 5 L0 10 z" fill="#ffd24a"/>
        </marker>
      </defs>
      <g id="edges"></g>
      <g id="labels"></g>
      <g id="nodes"></g>
    </svg>
  </div>
  <div class="side">
    <table id="tbl">
      <thead><tr><th class="num">#</th><th>caller</th><th></th><th>callee</th>
        <th class="num">order</th><th>started</th><th>anchor span</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>
<script>
const DATA = __DATA__;

function kindOf(key){ return (key.split(":")[0]||""); }
function colorOf(ent){
  const k = kindOf(ent.key);
  return k==="agent"?"var(--agent)":k==="tool"?"var(--tool)":k==="llm"?"var(--llm)":"#888";
}

/* ---- layout: place entities on a circle, grouped so callers cluster ---- */
const ents = DATA.entities;
const entById = Object.fromEntries(ents.map(e=>[e.id,e]));
const N = ents.length;
const CX=450, CY=310, R=240;
const pos = {};
ents.forEach((e,i)=>{
  const a = (-Math.PI/2) + i*(2*Math.PI/N);
  pos[e.id] = { x: CX + R*Math.cos(a), y: CY + R*Math.sin(a), a };
});

const NW=150, NH=40;
const nodesG=document.getElementById("nodes");
const edgesG=document.getElementById("edges");
const labelsG=document.getElementById("labels");

/* nodes */
ents.forEach(e=>{
  const p=pos[e.id];
  const g=document.createElementNS("http://www.w3.org/2000/svg","g");
  g.setAttribute("class","node"); g.setAttribute("data-id",e.id);
  g.setAttribute("transform",`translate(${p.x-NW/2},${p.y-NH/2})`);
  const c=colorOf(e);
  g.innerHTML =
    `<rect width="${NW}" height="${NH}" rx="7" fill="#1b1f28" stroke="${c}"></rect>`+
    `<text x="8" y="17">${e.name}</text>`+
    `<text class="sub" x="8" y="31">${e.key}`+
      `${e.inferred?'  •inf':''}</text>`;
  nodesG.appendChild(g);
});

/* edges: bundle multiple interactions between the same ordered pair by index */
function edgePath(a,b,off){
  const pa=pos[a], pb=pos[b];
  if(a===b){ // self loop
    const x=pa.x, y=pa.y-NH/2;
    return {d:`M ${x-16} ${y} C ${x-46} ${y-58}, ${x+46} ${y-58}, ${x+16} ${y}`,
            lx:x, ly:y-46};
  }
  const mx=(pa.x+pb.x)/2, my=(pa.y+pb.y)/2;
  const dx=pb.x-pa.x, dy=pb.y-pa.y, len=Math.hypot(dx,dy)||1;
  const nx=-dy/len, ny=dx/len;
  const curve = off*26;
  const cx=mx+nx*curve, cy=my+ny*curve;
  // trim endpoints to node edge (approx radius)
  const trim=78;
  const sax=pa.x+dx/len*0 , say=pa.y;
  return {d:`M ${pa.x} ${pa.y} Q ${cx} ${cy} ${pb.x} ${pb.y}`,
          lx:cx*0.5+mx*0.5, ly:cy*0.5+my*0.5};
}

// assign an offset per (caller,callee) occurrence so parallel edges fan out
const pairSeen={};
const edgeEls=[];
DATA.interactions.forEach(ix=>{
  const key=ix.caller_id+"|"+ix.callee_id;
  const seen=pairSeen[key]||0; pairSeen[key]=seen+1;
  const isResp = ix.is_response; // structural call/response (see build_data)
  const off = (isResp? 1 : -1) * (1 + Math.floor(seen/2));
  const {d,lx,ly}=edgePath(ix.caller_id, ix.callee_id, off);
  const isSelf = ix.caller_id===ix.callee_id;

  const path=document.createElementNS("http://www.w3.org/2000/svg","path");
  path.setAttribute("class","edge "+(isResp?"resp":"call"));
  path.setAttribute("d",d);
  path.setAttribute("marker-end","url(#arrow)");
  path.setAttribute("data-row",ix.row);
  path.dataset.self=isSelf?"1":"0";
  edgesG.appendChild(path);

  const lab=document.createElementNS("http://www.w3.org/2000/svg","g");
  lab.setAttribute("class","edgelabel"); lab.setAttribute("data-row",ix.row);
  lab.dataset.self=isSelf?"1":"0";
  lab.innerHTML=`<circle cx="${lx}" cy="${ly}" r="9"></circle>`+
    `<text x="${lx}" y="${ly+3}" text-anchor="middle">${ix.row}</text>`;
  lab.style.cursor="pointer";
  labelsG.appendChild(lab);
  edgeEls.push({ix,path,lab});
});

/* table */
const tb=document.querySelector("#tbl tbody");
DATA.interactions.forEach(ix=>{
  const tr=document.createElement("tr");
  const isSelf=ix.caller_id===ix.callee_id;
  if(isSelf) tr.className="self";
  tr.dataset.row=ix.row; tr.dataset.self=isSelf?"1":"0";
  const t=(ix.started_at||"").split("T")[1]||"";
  tr.innerHTML=
    `<td class="num">${ix.row}</td>`+
    `<td>${ix.caller_name}<div class="sub mono" style="color:var(--muted);font-size:10px">${ix.caller}</div></td>`+
    `<td class="dir">${ix.is_response?'⤸ resp':'→ call'}</td>`+
    `<td>${ix.callee_name}<div class="sub mono" style="color:var(--muted);font-size:10px">${ix.callee}</div></td>`+
    `<td class="num">${ix.order}</td>`+
    `<td class="mono">${t}</td>`+
    `<td class="mono">${ix.anchor||''}</td>`;
  tb.appendChild(tr);
});

document.getElementById("entCount").textContent=ents.length;
document.getElementById("ixCount").textContent=DATA.interactions.length;

/* highlight sync */
function setHi(row){
  edgeEls.forEach(({ix,path,lab})=>{
    const on = ix.row===row;
    path.classList.toggle("hi",on);
    path.setAttribute("marker-end", on?"url(#arrowHi)":"url(#arrow)");
    lab.classList.toggle("hi",on);
    if(row!=null){ path.classList.toggle("dim",!on); lab.classList.toggle("dim",!on); }
    else { path.classList.remove("dim"); lab.classList.remove("dim"); }
  });
  const cur=edgeEls.find(e=>e.ix.row===row);
  document.querySelectorAll("#nodes .node").forEach(n=>{
    if(row==null){ n.classList.remove("dim"); return; }
    const id=n.getAttribute("data-id");
    n.classList.toggle("dim", !(cur && (id===cur.ix.caller_id||id===cur.ix.callee_id)));
  });
  document.querySelectorAll("#tbl tbody tr").forEach(tr=>{
    tr.classList.toggle("hi", Number(tr.dataset.row)===row);
    if(row!=null && Number(tr.dataset.row)===row)
      tr.scrollIntoView({block:"nearest"});
  });
}
document.querySelectorAll("#tbl tbody tr").forEach(tr=>{
  tr.addEventListener("mouseenter",()=>setHi(Number(tr.dataset.row)));
  tr.addEventListener("mouseleave",()=>setHi(null));
});
labelsG.querySelectorAll(".edgelabel").forEach(l=>{
  l.addEventListener("mouseenter",()=>setHi(Number(l.getAttribute("data-row"))));
  l.addEventListener("mouseleave",()=>setHi(null));
});

/* self-loop filter */
document.getElementById("showSelf").addEventListener("change",e=>{
  const show=e.target.checked;
  edgeEls.forEach(({path,lab})=>{
    if(path.dataset.self==="1"){ path.style.display=show?"":"none"; lab.style.display=show?"":"none"; }
  });
  document.querySelectorAll("#tbl tbody tr").forEach(tr=>{
    if(tr.dataset.self==="1") tr.style.display=show?"":"none";
  });
});
</script>
</body>
</html>
"""


def main() -> None:
    data = build_data()
    html = _HTML.replace("__DATA__", json.dumps(data))
    _OUT.write_text(html)
    print(f"wrote {_OUT}")
    print(f"  {len(data['entities'])} entities, {len(data['interactions'])} interactions")


if __name__ == "__main__":
    main()
