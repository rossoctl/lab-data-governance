// The architecture diagram, as SVG, drawn into a container.
//
// The full layout (the film's architecture scenes and the slides) shows where things run: the owner,
// the Mac, its Lima VM with DAM's k3s cluster (control plane, each agent's gateway, and Sentry beside
// DAM in its own namespace), the host runner with the agent's micro-VM, and the outside world (GitHub's
// MCP server, the agent's model provider, the LLM aiac's onboarding uses). What this work adds inside
// DAM is gold: the policy check in the gateway (NEW IN DAM) and "+ …" lines on the pieces that changed.
// The mini layout (the UI scenes' "Where the call goes") keeps only the call's path.
//
// Arch.mount(el, {mini}) returns {set(state)}: state.show {node: opacity}, state.wire {wire: opacity},
// state.lit {wire: 0..1 drawn}, state.adds (opacity of the gold "+ …" lines).
(function () {
  const NS = "http://www.w3.org/2000/svg";

  const FULL = {
    viewBox: "0 0 1600 770",
    zones: [
      { x: 215, y: 14, w: 1040, h: 746, label: "YOUR MAC" },
      { x: 235, y: 50, w: 1000, h: 535, label: "LIMA VM · k3s cluster" },
      { x: 235, y: 600, w: 1000, h: 146, label: "HOST RUNNER · the Mac's own hypervisor", lx: 320, ly: 82 },
      { x: 255, y: 214, w: 760, h: 172, label: "AGENT GATEWAY · PER AGENT", cls: "gw" },
    ],
    heads: [
      { x: 20, y: 40, text: "PEOPLE" },
      { x: 1300, y: 40, text: "OUTSIDE WORLD" },
    ],
    nodes: {
      owner:   { x: 20,   y: 86,  w: 175, h: 104, cls: "",       label: "Owner",            sub: "web app · CLI" },
      api:     { x: 255,  y: 86,  w: 230, h: 104, cls: "dam",    label: "api-server",       sub: "the only way in",       add: "+ aiac · dg switches" },
      ctrl:    { x: 505,  y: 86,  w: 230, h: 104, cls: "dam",    label: "controller",       sub: "builds each gateway",   add: "+ the enforcement point" },
      kc:      { x: 755,  y: 86,  w: 200, h: 104, cls: "dam",    label: "Keycloak",         sub: "signs people in" },
      records: { x: 975,  y: 86,  w: 240, h: 104, cls: "dam",    label: "Agent records",    sub: "description · settings", add: "+ policy settings" },
      rules:   { x: 275,  y: 256, w: 185, h: 96,  cls: "dam",    label: "Network rules",    sub: "owner's approvals", lsize: 23 },
      pep:     { x: 482,  y: 256, w: 290, h: 96,  cls: "new",    label: "Policy Enforcement Point", sub: "every MCP tool call", tag: "right", lsize: 21.5 },
      creds:   { x: 794,  y: 256, w: 196, h: 96,  cls: "dam",    label: "Credentials",      sub: "token attached" },
      sentry:  { x: 515,  y: 420, w: 700, h: 155, cls: "sentry", label: "Sentry",           sub: "its own namespace, beside DAM", inline: true, lx: 650 },
      dg:      { x: 535,  y: 470, w: 320, h: 92,  cls: "sentry", label: "dg",               sub: "data · company policy" },
      aiac:    { x: 875,  y: 470, w: 320, h: 92,  cls: "sentry", label: "aiac",             sub: "tools · from the description" },
      agent:   { x: 237 + 18,  y: 636, w: 260, h: 96,  cls: "dam",    label: "Agent",            sub: "its own micro-VM" },
      model:   { x: 1300, y: 140, w: 280, h: 96,  cls: "",       label: "Model provider",   sub: "the agent's model" },
      github:  { x: 1300, y: 256, w: 280, h: 96,  cls: "",       label: "GitHub MCP server", sub: "api.githubcopilot.com" },
      llm:     { x: 1300, y: 470, w: 280, h: 96,  cls: "",       label: "LLM",              sub: "aiac's onboarding" },
    },
    // The call's path, lit stage by stage: w1–w4 through the gateway and out, w5 down to Sentry, w6 to
    // aiac, w7 to dg. wc: the controller configures the policy check.
    wires: {
      w1: "M367 636 L367 352",
      w2: "M460 304 L482 304",
      w3: "M772 304 L794 304",
      w4: "M990 304 L1300 304",
      w5: "M627 352 L627 402",
      w6: "M627 402 L1035 402 L1035 470",
      w7: "M627 402 L627 470",
      wc: "M560 190 L560 256",
    },
    // Never lit: DAM's own (plain) and Sentry's configuration (dashed gold).
    statics: {
      wo: { d: "M195 138 L255 138", cls: "wire" },
      wm: { d: "M990 282 L1300 200", cls: "wire" },
      wd: { d: "M1180 190 L1180 470", cls: "wire cfg", label: "reads the description", lx: 1172, ly: 214, anchor: "end" },
      wl: { d: "M1195 516 L1300 516", cls: "wire cfg" },
    },
    cfg: ["wc"],
  };

  const MINI = {
    viewBox: "0 0 1600 770",
    zones: [{ x: 330, y: 190, w: 770, h: 370, label: "DAM gateway", cls: "gwmini" }],
    heads: [],
    nodes: {
      agent:  { x: 30,   y: 300, w: 240, h: 140, cls: "dam",    label: "Agent",              sub: "its own micro-VM" },
      rules:  { x: 360,  y: 300, w: 210, h: 140, cls: "dam",    label: "Network rules",      sub: "owner's approvals" },
      pep:    { x: 610,  y: 300, w: 220, h: 140, cls: "new",    label: "Policy Enforcement Point", lsize: 17,       sub: "every MCP tool call", tag: "left" },
      creds:  { x: 870,  y: 300, w: 200, h: 140, cls: "dam",    label: "Credentials",        sub: "token attached" },
      github: { x: 1150, y: 300, w: 420, h: 140, cls: "",       label: "GitHub MCP server",  sub: "api.githubcopilot.com" },
      ctrl:   { x: 330,  y: 40,  w: 770, h: 104, cls: "new",    label: "DAM agent settings", sub: "per agent: MCP policy enforcement · aiac · dg", tag: "right" },
      sentry: { x: 420,  y: 580, w: 820, h: 170, cls: "sentry", label: "Sentry",             sub: "" },
      aiac:   { x: 440,  y: 630, w: 380, h: 104, cls: "sentry", label: "aiac",               sub: "tools, from the agent's description" },
      dg:     { x: 840,  y: 630, w: 380, h: 104, cls: "sentry", label: "dg",                 sub: "data, by company policy" },
    },
    wires: {
      w1: "M270 370 L360 370",
      w2: "M570 370 L610 370",
      w3: "M830 370 L870 370",
      w4: "M1070 370 L1150 370",
      w5: "M720 440 L720 580",
      w6: "M720 612 L630 630",
      w7: "M720 612 L1030 630",
      wc: "M790 144 L790 300",
    },
    statics: {},
    cfg: ["wc"],
  };

  const STYLE = `
    .zone { fill: rgba(246, 231, 200, .025); stroke: rgba(246, 231, 200, .28); stroke-width: 1.5; stroke-dasharray: 3 5; }
    .zone.gw { fill: rgba(18, 48, 58, .6); stroke: #8fb3c9; stroke-dasharray: none; stroke-width: 2; }
    .zone.gwmini { fill: rgba(18, 48, 58, .55); stroke: #8fb3c9; stroke-dasharray: none; stroke-width: 2; }
    .ztext { font-family: "Fira Mono", monospace; font-size: 15px; letter-spacing: .12em; fill: #a9b8b9 !important; }
    .zone-gw-text { fill: #8fb3c9 !important; }
    .add { font-family: "Fira Mono", monospace; font-size: 16px; fill: #e9c46a !important; }
    .wlabel { font-family: "Fira Mono", monospace; font-size: 14px; fill: #e9c46a !important; }
  `;

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(parent, x, y, cls, s, extra = {}) {
    const t = el("text", { x, y, class: cls, ...extra }, parent);
    t.textContent = s;
    return t;
  }

  function mount(container, { mini = false } = {}) {
    const L = mini ? MINI : FULL;
    const svg = el("svg", { viewBox: L.viewBox, class: "arch", width: "100%", height: "100%" });
    el("style", {}, svg).textContent = STYLE;
    const defs = el("defs", {}, svg);
    // In user space: a glow sized to each element's box would vanish on a straight horizontal or
    // vertical wire, whose box has no height or width.
    const f = el("filter", { id: "glow", filterUnits: "userSpaceOnUse", x: 0, y: 0, width: 1600, height: 770 }, defs);
    el("feGaussianBlur", { stdDeviation: "5", result: "b" }, f);
    const m = el("feMerge", {}, f);
    el("feMergeNode", { in: "b" }, m);
    el("feMergeNode", { in: "SourceGraphic" }, m);
    const mk = el("marker", { id: "head", viewBox: "0 0 10 10", refX: 7, refY: 5, markerWidth: 3.2, markerHeight: 3.2, orient: "auto-start-reverse" }, defs);
    el("path", { d: "M0 0 L10 5 L0 10 z", fill: "#ffd36a" }, mk);

    for (const z of L.zones) {
      el("rect", { x: z.x, y: z.y, width: z.w, height: z.h, rx: z.cls ? 24 : 14, class: `zone ${z.cls || ""}` }, svg);
      if (z.cls === "gwmini") {
        text(svg, z.x + 26, z.y + 44, "label", z.label);
        text(svg, z.x + 26, z.y + 74, "sub", "every request the agent makes");
      } else {
        text(svg, z.x + (z.lx || 18), z.y + (z.ly || 26), `ztext ${z.cls === "gw" ? "zone-gw-text" : ""}`, z.label);
      }
    }
    for (const h of L.heads) text(svg, h.x, h.y, "ztext", h.text);

    const wires = {}, lits = {};
    for (const [id, d] of Object.entries(L.wires)) wires[id] = el("path", { d, class: L.cfg.includes(id) ? "wire cfg" : "wire" }, svg);
    for (const [id, s] of Object.entries(L.statics)) {
      const g = el("g", {}, svg);
      el("path", { d: s.d, class: s.cls }, g);
      if (s.label) text(g, s.lx, s.ly, "wlabel", s.label, s.anchor ? { "text-anchor": s.anchor } : {});
      wires[id] = g;
    }
    const nodes = {}, adds = [];
    for (const [id, n] of Object.entries(L.nodes)) {
      const g = el("g", { class: `node ${n.cls}` }, svg);
      el("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 18 }, g);
      const lx = n.lx ?? n.x + 22;
      const short = n.h < 120;
      if (n.inline) {
        text(g, lx, n.y + 34, "label", n.label);
        if (n.sub) text(g, lx + 105, n.y + 34, "sub", n.sub);
      } else {
        const lt = text(g, lx, n.y + (short ? 38 : id === "sentry" ? 36 : 52), "label", n.label);
        if (n.lsize) lt.style.fontSize = `${n.lsize}px`;
        if (n.sub) text(g, lx, n.y + (short ? 66 : 86), "sub", n.sub);
      }
      if (n.add) adds.push(text(g, lx, n.y + 92, "add", n.add));
      if (n.tag) {
        const tg = el("g", { class: "newtag" }, g);
        const tx = n.tag === "left" ? n.x + 10 : n.x + n.w - 150;
        el("rect", { x: tx, y: n.y - 16, width: 140, height: 30, rx: 8 }, tg);
        text(tg, tx + 12, n.y + 5, "", "NEW IN DAM");
      }
      nodes[id] = g;
    }
    for (const [id, d] of Object.entries(L.wires)) if (!L.cfg.includes(id)) lits[id] = el("path", { d, class: "lit", "marker-end": "url(#head)" }, svg);
    container.appendChild(svg);
    const lengths = Object.fromEntries(Object.entries(lits).map(([id, p]) => [id, p.getTotalLength()]));

    return {
      set(state) {
        for (const [id, g] of Object.entries(nodes)) g.style.opacity = state.show?.[id] ?? 1;
        for (const [id, p] of Object.entries(wires)) p.style.opacity = state.wire?.[id] ?? 1;
        for (const a of adds) a.style.opacity = state.adds ?? 1;
        for (const [id, p] of Object.entries(lits)) {
          const k = Math.max(0, Math.min(1, state.lit?.[id] ?? 0));
          p.style.strokeDasharray = lengths[id];
          p.style.strokeDashoffset = lengths[id] * (1 - k);
          p.style.opacity = k > 0 ? 1 : 0;
          // The head shows once the wire has drawn in, so it never floats ahead of the line.
          p.setAttribute("marker-end", k > 0.97 ? "url(#head)" : "none");
        }
      },
    };
  }

  window.Arch = { mount };
})();
