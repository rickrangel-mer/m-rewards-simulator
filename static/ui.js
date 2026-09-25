(function () {
  var inflight = null;

  function setupUploadDialog() {
    var dialog = document.getElementById("upload-dialog");
    var openBtn = document.getElementById("upload-open");
    if (!dialog || !openBtn || openBtn.dataset.bound === "1") return;
    openBtn.dataset.bound = "1";

    var closeBtn = document.getElementById("upload-close");
    var cancelBtn = document.getElementById("upload-cancel");
    var okBtn = document.getElementById("upload-ok");
    var source = document.getElementById("upload-source");
    var hint = document.getElementById("upload-hint");
    var dropzone = document.getElementById("upload-dropzone");
    var picker = document.getElementById("upload-file");
    var filename = document.getElementById("upload-filename");
    var importForm = document.getElementById("import-form");
    var catalogForm = document.getElementById("catalog-upload");

    function hints() {
      if (!source || !hint) return;
      hint.textContent = source.value === "catalog"
        ? "Excel/CSV with sku, product_title, current_points"
        : "CSV/Excel with sku + points";
    }

    function setFile(file) {
      if (!file || !picker) return;
      try {
        var transfer = new DataTransfer();
        transfer.items.add(file);
        picker.files = transfer.files;
      } catch (err) {
        /* Safari older DataTransfer gaps: user can still click the input. */
      }
      if (filename) {
        filename.hidden = false;
        filename.textContent = file.name;
      }
    }

    function copyFileTo(form) {
      if (!form || !picker || !picker.files || !picker.files[0]) return false;
      var dest = form.querySelector('input[type="file"]');
      if (!dest) return false;
      try {
        dest.files = picker.files;
        return dest.files && dest.files.length > 0;
      } catch (err) {
        return false;
      }
    }

    openBtn.addEventListener("click", function () {
      hints();
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    });

    function close() {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }

    closeBtn && closeBtn.addEventListener("click", close);
    cancelBtn && cancelBtn.addEventListener("click", close);
    source && source.addEventListener("change", hints);

    dropzone && dropzone.addEventListener("dragover", function (event) {
      event.preventDefault();
      dropzone.classList.add("is-over");
    });
    dropzone && dropzone.addEventListener("dragleave", function () {
      dropzone.classList.remove("is-over");
    });
    dropzone && dropzone.addEventListener("drop", function (event) {
      event.preventDefault();
      dropzone.classList.remove("is-over");
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file) setFile(file);
    });
    picker && picker.addEventListener("change", function () {
      if (picker.files && picker.files[0] && filename) {
        filename.hidden = false;
        filename.textContent = picker.files[0].name;
      }
    });

    okBtn && okBtn.addEventListener("click", function () {
      var form = source && source.value === "catalog" ? catalogForm : importForm;
      if (!copyFileTo(form)) {
        picker && picker.click();
        return;
      }
      form.submit();
    });
  }

  function brandPath(url) {
    var parts = url.pathname.split("/").filter(Boolean);
    if (parts.length !== 2 || parts[0] !== "brands") return false;
    return true;
  }

  function brandSlug(pathname) {
    var parts = String(pathname || "").split("/").filter(Boolean);
    if (parts[0] === "brands" && parts[1]) return parts[1];
    return "";
  }

  function canonicalUrl(doc, fallback) {
    try {
      var form = doc.querySelector("#month-form");
      if (!form) return fallback;
      var action = form.getAttribute("action") || location.pathname;
      var url = new URL(action, location.origin);
      url.search = new URLSearchParams(new FormData(form)).toString();
      return url.href;
    } catch (err) {
      return fallback;
    }
  }

  function applyPage(doc, url) {
    var hero = doc.querySelector(".page-hero");
    var currentHero = document.querySelector(".page-hero");
    if (hero && currentHero) currentHero.replaceWith(document.importNode(hero, true));
    var main = doc.querySelector("main.container");
    var currentMain = document.querySelector("main.container");
    if (!main || !currentMain) return false;
    currentMain.replaceWith(document.importNode(main, true));
    if (doc.body) {
      document.body.dataset.brand = doc.body.dataset.brand || "";
      document.body.dataset.theme = doc.body.dataset.theme || "default";
    }
    if (doc.title) document.title = doc.title;
    var nextPath = new URL(url, location.origin).pathname;
    Array.prototype.forEach.call(document.querySelectorAll(".brand-nav a"), function (link) {
      var path = "";
      try { path = new URL(link.getAttribute("href"), location.origin).pathname; } catch (err) { path = ""; }
      link.classList.toggle("active", path === nextPath);
    });
    if (window.enhanceTables) window.enhanceTables();
    setupUploadDialog();
    return true;
  }

  var currentSlug = brandSlug(location.pathname);
  var scrollLock = null;

  window.addEventListener("scroll", function () {
    if (scrollLock == null) return;
    if (Math.abs(window.scrollY - scrollLock) > 2) window.scrollTo(0, scrollLock);
  }, { passive: true });

  function pinScroll(y) {
    scrollLock = y;
    window.scrollTo(0, y);
    var frames = 0;
    (function tick() {
      if (scrollLock !== y) return;
      if (Math.abs(window.scrollY - y) > 2) window.scrollTo(0, y);
      frames += 1;
      if (frames < 24) requestAnimationFrame(tick);
      else scrollLock = null;
    })();
  }

  function parkFocus() {
    var node = document.activeElement;
    if (!node || node === document.body) return;
    document.body.tabIndex = -1;
    try { document.body.focus({ preventScroll: true }); }
    catch (err) { node.blur(); }
  }

  function swapPage(url, push, options) {
    options = options || {};
    if (inflight) inflight.abort();
    var controller = new AbortController();
    inflight = controller;
    var anchorY = window.scrollY;
    var target = new URL(url, location.origin);
    var keepScroll = target.origin === location.origin && brandSlug(target.pathname) === currentSlug;
    document.body.classList.add("is-updating");
    var init = {
      signal: controller.signal,
      credentials: "same-origin",
      headers: { "Accept": "text/html" }
    };
    if (options.method) init.method = options.method;
    if (options.body) init.body = options.body;
    fetch(target.href, init).then(function (response) {
      return response.text().then(function (html) {
        return { response: response, html: html };
      });
    }).then(function (result) {
      if (inflight !== controller) return;
      var doc = new DOMParser().parseFromString(result.html, "text/html");
      var y = typeof options.scrollTo === "number" ? options.scrollTo : (keepScroll ? anchorY : 0);
      parkFocus();
      if (!result.response.ok) {
        if (options.method && options.method !== "GET" && applyPage(doc, location.href)) {
          pinScroll(0);
          return;
        }
        window.location.assign(target.href);
        return;
      }
      var finalUrl = canonicalUrl(doc, result.response.url || target.href);
      var parsed = new URL(finalUrl, location.origin);
      if (!brandPath(parsed) || !applyPage(doc, finalUrl)) {
        window.location.assign(result.response.url || target.href);
        return;
      }
      currentSlug = brandSlug(parsed.pathname);
      if (push) {
        if (history.scrollRestoration) history.scrollRestoration = "manual";
        history.replaceState({ mrewards: 1, y: anchorY }, "");
        history.pushState({ mrewards: 1, y: y }, "", finalUrl);
      }
      pinScroll(y);
    }).catch(function (err) {
      if (err && err.name === "AbortError") return;
      if (!options.method || options.method === "GET") window.location.assign(target.href);
    }).finally(function () {
      if (inflight === controller) {
        inflight = null;
        document.body.classList.remove("is-updating");
      }
    });
  }

  function onClick(event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest("a[href]");
    if (!link || link.target === "_blank" || link.hasAttribute("download")) return;
    var url;
    try { url = new URL(link.href, location.origin); } catch (err) { return; }
    if (url.origin !== location.origin || !brandPath(url)) return;
    event.preventDefault();
    swapPage(url.href, true);
  }

  function onSubmit(event) {
    var form = event.target;
    if (!form || !form.id) return;
    if (form.id !== "month-form" && form.id !== "lift-scenario" && form.id !== "sim-form" && form.id !== "brand-refresh-form") return;
    event.preventDefault();
    if (form.id === "month-form" || form.id === "lift-scenario") {
      var params = new URLSearchParams(new FormData(form));
      var action = form.getAttribute("action") || location.pathname;
      swapPage(action + "?" + params.toString(), true);
      return;
    }
    var body = new FormData(form);
    if (event.submitter && event.submitter.name) body.append(event.submitter.name, event.submitter.value);
    // A control named "action" shadows HTMLFormElement.action, so read the attribute.
    var action = form.getAttribute("action") || location.pathname;
    swapPage(action, true, { method: "POST", body: body });
  }

  if (history.scrollRestoration) history.scrollRestoration = "manual";
  document.addEventListener("click", onClick);
  document.addEventListener("submit", onSubmit);
  window.addEventListener("popstate", function (event) {
    var saved = event.state && typeof event.state.y === "number" ? event.state.y : window.scrollY;
    swapPage(location.href, false, { scrollTo: saved });
  });
  setupUploadDialog();
})();
