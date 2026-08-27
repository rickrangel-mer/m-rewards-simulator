(function () {
  function cellValue(td) {
    if (!td) return "";
    var numberInput = td.querySelector("input[type='number'], input[type='text'], input[type='search']");
    if (numberInput) {
      var raw = (numberInput.value || "").replace(/,/g, "").trim();
      var asNum = parseFloat(raw);
      if (raw !== "" && !isNaN(asNum)) return asNum;
      return raw.toLowerCase();
    }
    var checkbox = td.querySelector("input[type='checkbox']");
    if (checkbox) return checkbox.checked ? 1 : 0;
    var text = (td.innerText || td.textContent || "").replace(/\s+/g, " ").trim();
    var numeric = text.replace(/,/g, "").replace(/%/g, "");
    if (numeric !== "" && /^-?\d+(\.\d+)?$/.test(numeric)) return parseFloat(numeric);
    return text.toLowerCase();
  }

  function compare(a, b) {
    if (typeof a === "number" && typeof b === "number") return a - b;
    if (typeof a === "number") return -1;
    if (typeof b === "number") return 1;
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  }

  function sortTable(table, colIdx, th) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var rows = Array.prototype.slice.call(tbody.rows).filter(function (row) {
      return row.cells.length > 1;
    });
    if (rows.length < 2) return;

    var current = th.getAttribute("aria-sort");
    var dir = current === "ascending" ? "descending" : "ascending";
    Array.prototype.forEach.call(table.querySelectorAll("th[aria-sort]"), function (header) {
      header.removeAttribute("aria-sort");
    });
    th.setAttribute("aria-sort", dir);

    rows.sort(function (rowA, rowB) {
      var result = compare(cellValue(rowA.cells[colIdx]), cellValue(rowB.cells[colIdx]));
      return dir === "ascending" ? result : -result;
    });
    rows.forEach(function (row) { tbody.appendChild(row); });
  }

  function enhance(table) {
    var headers = table.querySelectorAll("thead th");
    if (!headers.length) return;
    table.classList.add("js-sortable");
    Array.prototype.forEach.call(headers, function (th, colIdx) {
      th.classList.add("sortable");
      th.setAttribute("role", "columnheader");
      th.tabIndex = 0;
      th.addEventListener("click", function () { sortTable(table, colIdx, th); });
      th.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sortTable(table, colIdx, th);
        }
      });
    });
  }

  document.querySelectorAll("table").forEach(enhance);
})();
