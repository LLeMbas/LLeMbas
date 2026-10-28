/*
  The command palette: Ctrl/⌘ + K, from any page.

  One box; what it finds comes from `/api/palette` (api/palette.py) -- chats,
  files, models, settings -- grouped, the first one chosen. Arrow keys move,
  Enter opens, Escape closes. The composer's slash commands are still one
  entry away ("Commands…"), and `/` in an empty composer opens them as before.

  Every title is somebody's text (a chat's title, a file's name): put in with
  textContent, never as markup.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var box = null;
  var input = null;
  var list = null;
  var items = [];
  var chosen = 0;
  var timer = null;
  var asked = 0;

  function build() {
    box = document.createElement("dialog");
    box.className = "dialog palette";
    box.setAttribute("aria-label", t("Go to"));
    box.setAttribute("data-testid", "palette");
    input = document.createElement("input");
    input.className = "input palette__input";
    input.type = "search";
    input.setAttribute("placeholder", t("Go to a chat, a file, a model or a setting…"));
    input.setAttribute("aria-label", t("Go to"));
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "palette-list");
    list = document.createElement("ul");
    list.className = "palette__list";
    list.id = "palette-list";
    list.setAttribute("role", "listbox");
    box.appendChild(input);
    box.appendChild(list);
    document.body.appendChild(box);

    input.addEventListener("input", function () {
      clearTimeout(timer);
      timer = setTimeout(fetchItems, 120);
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "ArrowDown") { event.preventDefault(); move(1); }
      else if (event.key === "ArrowUp") { event.preventDefault(); move(-1); }
      else if (event.key === "Enter") { event.preventDefault(); go(items[chosen]); }
    });
    box.addEventListener("click", function (event) {
      if (event.target === box) box.close();
    });
  }

  function commandsEntry() {
    var composer = document.querySelector("[data-composer-input]");
    if (!composer) return [];
    return [{ kind: "command", group: t("Commands"), title: t("Commands…"), run: function () {
      composer.focus();
      composer.value = "/";
      composer.dispatchEvent(new Event("input", { bubbles: true }));
    } }];
  }

  function fetchItems() {
    var mine = ++asked;
    fetch("/api/palette?q=" + encodeURIComponent(input.value), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (response) {
      return response.ok ? response.json() : { items: [] };
    }).then(function (answer) {
      if (mine !== asked) return;
      draw(commandsEntry().filter(function (entry) {
        return !input.value.trim() || entry.title.toLowerCase().indexOf(input.value.trim().toLowerCase()) !== -1;
      }).concat(answer.items || []));
    }).catch(function () { draw(commandsEntry()); });
  }

  function draw(found) {
    items = found;
    chosen = 0;
    list.textContent = "";
    var group = null;
    found.forEach(function (item, index) {
      if (item.group !== group) {
        group = item.group;
        var head = document.createElement("li");
        head.className = "palette__group";
        head.setAttribute("role", "presentation");
        head.textContent = group;
        list.appendChild(head);
      }
      var row = document.createElement("li");
      row.className = "palette__item";
      row.id = "palette-item-" + index;
      row.setAttribute("role", "option");
      row.setAttribute("data-testid", "palette-item");
      var title = document.createElement("span");
      title.className = "palette__title";
      title.textContent = item.title;
      row.appendChild(title);
      if (item.detail) {
        var detail = document.createElement("span");
        detail.className = "palette__detail";
        detail.textContent = item.detail;
        row.appendChild(detail);
      }
      row.addEventListener("mousemove", function () { select(index); });
      row.addEventListener("click", function () { go(item); });
      list.appendChild(row);
    });
    if (!found.length) {
      var none = document.createElement("li");
      none.className = "palette__empty";
      none.textContent = t("Nothing found");
      list.appendChild(none);
    }
    select(0);
  }

  function select(index) {
    if (!items.length) return;
    chosen = Math.max(0, Math.min(items.length - 1, index));
    list.querySelectorAll(".palette__item").forEach(function (row, n) {
      row.classList.toggle("is-chosen", n === chosen);
      row.setAttribute("aria-selected", n === chosen ? "true" : "false");
    });
    var current = document.getElementById("palette-item-" + chosen);
    if (current) {
      input.setAttribute("aria-activedescendant", current.id);
      current.scrollIntoView({ block: "nearest" });
    }
  }

  function move(by) { select(chosen + by); }

  function go(item) {
    if (!item) return;
    box.close();
    if (item.run) { item.run(); return; }
    if (item.url && item.url.charAt(0) === "/") location.href = item.url;
  }

  function open() {
    if (!box) build();
    input.value = "";
    if (!box.open) box.showModal();
    input.focus();
    fetchItems();
  }

  document.addEventListener("keydown", function (event) {
    if (event.isComposing) return;
    if (event.target.closest && event.target.closest("#terminal")) return;
    if ((event.ctrlKey || event.metaKey) && event.code === "KeyK") {
      event.preventDefault();
      event.stopImmediatePropagation();
      open();
    }
  }, true);

  window.lembas = window.lembas || {};
  window.lembas.palette = open;
})();
