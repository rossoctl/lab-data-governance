// Positions tied to the chosen stills (1920x1080 image coordinates, measured on stills/v3 grid previews).
// Kept apart from timeline.js so a new still only means new numbers here.
window.layout = () => {
  const $ = (id) => document.getElementById(id);
  const place = (el, x, y, w) => Object.assign(el.style, { left: `${x}px`, top: `${y}px`, ...(w ? { width: `${w}px` } : {}) });

  // 04_rule_scroll: parchment spans x≈770–1150, y≈280–860.
  place($("rule-text"), 770, 370, 380);

  // 08_l2_lantern: lantern at (1100,180); squires' heads at (990,560) (840,600) (760,600) (360,560);
  // the glowing book (Rosa's record) at (500,650).
  const hops = [[990, 560], [840, 600], [760, 600], [360, 560]];
  const book = [500, 650];
  $("thread-path").setAttribute("d",
    "M1100 230 C 1080 380, 1030 470, 990 560 " +
    "C 950 520, 880 540, 840 600 " +
    "C 810 570, 785 575, 760 600 " +
    "C 620 520, 460 520, 360 560 " +
    `C 390 640, 450 670, ${book[0]} ${book[1]}`);
  const ns = "http://www.w3.org/2000/svg";
  const g = $("hops");
  for (const [x, y] of hops) {
    const c = document.createElementNS(ns, "circle");
    Object.entries({ class: "hop", cx: x, cy: y, r: 9 }).forEach(([k, v]) => c.setAttribute(k, v));
    g.appendChild(c);
  }
  const o = document.createElementNS(ns, "circle");
  Object.entries({ id: "thread-origin", cx: book[0], cy: book[1], r: 18 }).forEach(([k, v]) => o.setAttribute(k, v));
  g.appendChild(o);
  place($("l2-origin"), 300, 690);
};
