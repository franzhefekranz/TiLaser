// TiLaser – Projektseite
// 1) Laserkopf fährt die Schnittkontur im Kopfbild einmal ab (nicht bei „Bewegung reduzieren“)
// 2) Download-Tabelle mit den Dateien des neuesten Releases füllen

(function laser() {
  const svg = document.querySelector(".platte svg");
  if (!svg) return;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const paths = Array.from(svg.querySelectorAll("#schnitt path"));
  const head = svg.querySelector("#laserkopf");
  if (reduce || !paths.length || !head || !paths[0].getTotalLength) return;

  const lens = paths.map((p) => p.getTotalLength());
  const total = lens.reduce((a, b) => a + b, 0);
  paths.forEach((p, i) => {
    p.style.strokeDasharray = `${lens[i]} ${lens[i]}`;
    p.style.strokeDashoffset = `${lens[i]}`;
  });
  svg.classList.add("laeuft");
  head.setAttribute("opacity", "1");

  const duration = 5200;          // ms für die gesamte Kontur
  let start = null;
  function frame(ts) {
    if (start === null) start = ts;
    let done = Math.min(1, (ts - start) / duration) * total;
    for (let i = 0; i < paths.length; i++) {
      const L = lens[i];
      const part = Math.max(0, Math.min(L, done));
      paths[i].style.strokeDashoffset = `${L - part}`;
      if (part >= L) paths[i].classList.add("fertig");
      if (done > 0 && done <= L) {
        const pt = paths[i].getPointAtLength(part);
        head.setAttribute("transform", `translate(${pt.x} ${pt.y})`);
      }
      done -= L;
    }
    if (ts - start < duration) {
      requestAnimationFrame(frame);
    } else {
      head.setAttribute("opacity", "0");
      paths.forEach((p) => { p.style.strokeDasharray = ""; p.style.strokeDashoffset = ""; });
    }
  }
  requestAnimationFrame(frame);
})();

(function downloads() {
  const info = document.getElementById("release-info");
  const rows = document.querySelectorAll(".pakete tr[data-muster]");
  if (!info || !rows.length || !window.fetch) return;
  fetch("https://api.github.com/repos/franzhefekranz/TiLaser/releases/latest")
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((rel) => {
      const datum = new Date(rel.published_at).toLocaleDateString("de-DE", { day: "numeric", month: "long", year: "numeric" });
      info.innerHTML = "";
      const a = document.createElement("a");
      a.href = rel.html_url;
      a.textContent = `Version ${rel.tag_name.replace(/^v/, "")}`;
      info.append(a, ` vom ${datum}.`);
      rows.forEach((row) => {
        const re = new RegExp(row.dataset.muster);
        const asset = (rel.assets || []).find((x) => re.test(x.name));
        const link = row.querySelector("td:nth-child(2) a");
        if (asset && link) {
          link.href = asset.browser_download_url;
          const mb = document.createElement("span");
          mb.className = "groesse";
          mb.textContent = `${Math.round(asset.size / 1048576)} MB`;
          link.after(mb);
        }
      });
    })
    .catch(() => {
      info.innerHTML = 'Noch keine Version veröffentlicht – die Pakete erscheinen auf der ' +
        '<a href="https://github.com/franzhefekranz/TiLaser/releases">Release-Seite</a>, sobald es eine gibt.';
    });
})();
