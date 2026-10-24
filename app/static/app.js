(function () {
  "use strict";

  function scrollLogs() {
    document.querySelectorAll("[data-log]").forEach(function (element) {
      element.scrollTop = element.scrollHeight;
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    var message = form.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    var opener = event.target.closest("[data-open-picker]");
    if (opener) {
      var picker = document.querySelector(opener.dataset.openPicker);
      if (picker) picker.hidden = false;
      return;
    }

    var closer = event.target.closest("[data-close-picker]");
    if (closer) {
      var box = closer.closest(".picker") || document.querySelector("#dir-picker");
      if (box) box.hidden = true;
      return;
    }

    var pick = event.target.closest("[data-pick-dir]");
    if (pick) {
      var value = pick.dataset.pickDir || "";
      if (pick.dataset.pickFrom) {
        var source = document.querySelector(pick.dataset.pickFrom);
        if (source) value = source.value;
      }
      var input = document.querySelector("#data-dir-input");
      if (input && value) {
        input.value = value;
        input.focus();
      }
      var pickerBox = document.querySelector("#dir-picker");
      if (pickerBox) pickerBox.hidden = true;
    }
  });

  document.addEventListener("htmx:afterSwap", scrollLogs);
  document.addEventListener("DOMContentLoaded", scrollLogs);
  window.addEventListener("load", scrollLogs);
})();
