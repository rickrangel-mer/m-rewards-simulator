(function () {
  function setupUploadDialog() {
    var dialog = document.getElementById("upload-dialog");
    var openBtn = document.getElementById("upload-open");
    if (!dialog || !openBtn) return;

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

  setupUploadDialog();
})();
