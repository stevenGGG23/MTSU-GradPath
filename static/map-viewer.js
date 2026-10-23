// Toolbar for prerequisite maps: full screen, zoom in/out, fit, and drag to
// pan. Works on any `.prereq-graph-wrap` holding a Mermaid diagram.
(function () {
  "use strict";

  const ZOOM_STEP = 1.25;
  const MIN_ZOOM = 0.25;
  const MAX_ZOOM = 4;

  function svgOf(wrap) {
    return wrap.querySelector("svg");
  }

  // The diagram's own size (Mermaid's viewBox), so 100% means readable text
  function naturalWidth(svg) {
    const box = svg.viewBox && svg.viewBox.baseVal;
    return box && box.width ? box.width : svg.getBoundingClientRect().width;
  }

  function fitZoom(wrap, svg) {
    const room = wrap.clientWidth - 24;
    return Math.min(1, Math.max(MIN_ZOOM, room / naturalWidth(svg)));
  }

  function setZoom(viewer, zoom) {
    const wrap = viewer.querySelector(".prereq-graph-wrap");
    const svg = svgOf(wrap);
    if (!svg) return;
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
    viewer.dataset.zoom = String(zoom);
    svg.style.maxWidth = "none";
    svg.style.width = Math.round(naturalWidth(svg) * zoom) + "px";
    svg.style.height = "auto";
    const label = viewer.querySelector("[data-map-zoom-label]");
    if (label) label.textContent = Math.round(zoom * 100) + "%";
  }

  function readableZoom() {
    return window.innerWidth < 700 ? 0.6 : 1;
  }

  function isFull(viewer) {
    return document.fullscreenElement === viewer || viewer.classList.contains("map-viewer-full");
  }

  function enterFull(viewer) {
    viewer.classList.add("map-viewer-full");
    document.body.classList.add("map-viewer-open");
    if (viewer.requestFullscreen) {
      viewer.requestFullscreen().catch(function () { /* the CSS overlay is enough */ });
    }
    // Full screen opens at readable size (a bit smaller on a phone); drag
    // or zoom to move around
    setZoom(viewer, readableZoom());
    updateButton(viewer);
  }

  function exitFull(viewer) {
    viewer.classList.remove("map-viewer-full");
    document.body.classList.remove("map-viewer-open");
    if (document.fullscreenElement === viewer && document.exitFullscreen) {
      document.exitFullscreen().catch(function () {});
    }
    const wrap = viewer.querySelector(".prereq-graph-wrap");
    const svg = svgOf(wrap);
    if (svg) setZoom(viewer, fitZoom(wrap, svg));
    updateButton(viewer);
  }

  function updateButton(viewer) {
    const button = viewer.querySelector("[data-map-full]");
    if (!button) return;
    const full = viewer.classList.contains("map-viewer-full");
    button.innerHTML = full
      ? '<i class="bi bi-fullscreen-exit me-1"></i>Exit full screen'
      : '<i class="bi bi-arrows-fullscreen me-1"></i>Full screen';
    button.setAttribute("aria-pressed", full ? "true" : "false");
  }

  function enablePan(wrap) {
    let start = null;
    wrap.addEventListener("pointerdown", function (event) {
      if (event.button !== 0 || event.pointerType === "touch") return; // touch scrolls natively
      start = { x: event.clientX, y: event.clientY, left: wrap.scrollLeft, top: wrap.scrollTop };
      wrap.classList.add("is-panning");
    });
    window.addEventListener("pointermove", function (event) {
      if (!start) return;
      wrap.scrollLeft = start.left - (event.clientX - start.x);
      wrap.scrollTop = start.top - (event.clientY - start.y);
    });
    window.addEventListener("pointerup", function () {
      start = null;
      wrap.classList.remove("is-panning");
    });
  }

  function setUp(viewer) {
    const wrap = viewer.querySelector(".prereq-graph-wrap");
    if (!wrap) return;
    enablePan(wrap);

    viewer.querySelector("[data-map-full]").addEventListener("click", function () {
      if (isFull(viewer)) exitFull(viewer); else enterFull(viewer);
    });
    viewer.querySelector("[data-map-in]").addEventListener("click", function () {
      setZoom(viewer, parseFloat(viewer.dataset.zoom || "1") * ZOOM_STEP);
    });
    viewer.querySelector("[data-map-out]").addEventListener("click", function () {
      setZoom(viewer, parseFloat(viewer.dataset.zoom || "1") / ZOOM_STEP);
    });
    viewer.querySelector("[data-map-fit]").addEventListener("click", function () {
      const svg = svgOf(wrap);
      if (svg) setZoom(viewer, fitZoom(wrap, svg));
    });

    // Mermaid draws after the page loads, and can replace its first drawing
    // with the final one: size each new drawing to fit the box (or to the
    // window in full screen)
    let sized = null;
    function sizeNew() {
      const svg = svgOf(wrap);
      if (svg && svg !== sized) {
        sized = svg;
        setZoom(viewer, isFull(viewer) ? readableZoom() : fitZoom(wrap, svg));
      }
    }
    new MutationObserver(sizeNew).observe(wrap, { childList: true, subtree: true });
    sizeNew();
  }

  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    document.querySelectorAll(".map-viewer.map-viewer-full").forEach(exitFull);
  });
  // Leaving browser full screen (Esc, swipe) closes the overlay too
  document.addEventListener("fullscreenchange", function () {
    if (!document.fullscreenElement) {
      document.querySelectorAll(".map-viewer.map-viewer-full").forEach(exitFull);
    }
  });

  document.querySelectorAll(".map-viewer").forEach(setUp);
})();
