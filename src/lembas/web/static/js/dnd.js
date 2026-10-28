/*
  Drag and drop: chats into folders, and lists put in order.

  Native HTML5 drag and drop with a mouse, and for each a way that needs
  neither a mouse nor a steady thumb:

  * A chat row (`[data-chat-id][draggable]`) dropped on a folder
    (`[data-folder-drop]`, empty for "no folder") is moved there with the same
    `PATCH /api/chats/{id}` `folder_id` the chat's own settings use, and the
    sidebar tree comes back re-rendered. The row's "Move to folder" button
    (`[data-move-chat]`) asks the same question in a dialog, listing the drop
    targets on the page -- so there is one list of folders, the one drawn.

  * A list in order (`[data-sortable]`, items `[data-sortable-item]` with
    `data-id`) is reordered by dragging an item's handle
    (`[data-sortable-handle]`), by touch on the same handle (pointer events,
    because a phone's browser does not start a native drag from a finger), or
    from the keyboard: focus the handle, Arrow Up / Arrow Down. The new order is
    posted to `data-sortable-url` as `ids` (comma-separated, the order of this
    list only; the server keeps the slots of everything not on the page) and
    the page is saved in place (inplace.js), so the numbers beside the rows are
    the server's again.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  /* --- Chats into folders --------------------------------------------- */

  var CHAT_TYPE = "application/x-lembas-chat";

  function moveChat(chatId, folderId) {
    if (!window.htmx || !chatId) return;
    window.htmx.ajax("PATCH", "/api/chats/" + encodeURIComponent(chatId), {
      values: { folder_id: folderId || "" },
      target: "#sidebar-tree",
      swap: "outerHTML",
    });
  }

  function dropTarget(node) {
    return node && node.closest ? node.closest("[data-folder-drop]") : null;
  }

  document.addEventListener("dragstart", function (event) {
    var row = event.target.closest && event.target.closest("[data-chat-id][draggable='true']");
    if (!row || !event.dataTransfer) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(CHAT_TYPE, row.dataset.chatId);
    event.dataTransfer.setData("text/plain", row.textContent.trim());
    row.classList.add("is-dragging");
    document.body.classList.add("is-dragging-chat");
  });

  document.addEventListener("dragend", function (event) {
    var row = event.target.closest && event.target.closest("[data-chat-id]");
    if (row) row.classList.remove("is-dragging");
    document.body.classList.remove("is-dragging-chat");
    document.querySelectorAll(".is-drop-target").forEach(function (el) {
      el.classList.remove("is-drop-target");
    });
  });

  function carriesChat(event) {
    var types = event.dataTransfer && event.dataTransfer.types;
    return !!types && Array.prototype.indexOf.call(types, CHAT_TYPE) !== -1;
  }

  document.addEventListener("dragover", function (event) {
    var target = dropTarget(event.target);
    if (!target || !carriesChat(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    target.classList.add("is-drop-target");
  });

  document.addEventListener("dragleave", function (event) {
    var target = dropTarget(event.target);
    if (target && !target.contains(event.relatedTarget)) target.classList.remove("is-drop-target");
  });

  document.addEventListener("drop", function (event) {
    var target = dropTarget(event.target);
    if (!target || !carriesChat(event)) return;
    event.preventDefault();
    target.classList.remove("is-drop-target");
    moveChat(event.dataTransfer.getData(CHAT_TYPE), target.dataset.folderDrop);
  });

  /* The keyboard's and the phone's way: the same targets, in a dialog. */
  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-move-chat]");
    if (!button) return;
    event.preventDefault();
    var seen = {};
    var options = [];
    document.querySelectorAll("[data-folder-drop]").forEach(function (el) {
      var id = el.dataset.folderDrop || "";
      if (seen[id]) return;
      seen[id] = true;
      options.push({ value: id, label: el.dataset.folderName || id });
    });
    if (!options.length || !window.lembas || !window.lembas.choose) return;
    window.lembas.choose({
      title: t("Move to folder"),
      message: button.dataset.chatTitle || "",
      options: options,
      value: button.dataset.folderId || "",
      confirmLabel: t("Move"),
    }).then(function (value) {
      if (value === null || value === (button.dataset.folderId || "")) return;
      moveChat(button.dataset.moveChat, value);
    });
  });

  /* --- Lists in order -------------------------------------------------- */

  var dragged = null;

  function items(list) {
    return Array.prototype.filter.call(list.children, function (el) {
      return el.hasAttribute("data-sortable-item");
    });
  }

  function save(list) {
    var url = list.dataset.sortableUrl;
    if (!url || !window.htmx) return;
    var ids = items(list).map(function (el) { return el.dataset.id; });
    if (ids.join(",") === list.dataset.sortableWas) return;
    var root = list.closest("[data-in-place-root]");
    window.htmx.ajax("POST", url, {
      source: list,
      values: { ids: ids.join(","), back: location.pathname + location.search },
      target: root || list,
      select: root ? "[data-in-place-root]" : undefined,
      swap: "outerHTML show:none focus-scroll:false",
      headers: { "LMB-In-Place": "1" },
    });
  }

  function remember(list) {
    list.dataset.sortableWas = items(list).map(function (el) { return el.dataset.id; }).join(",");
  }

  /* Where `y` falls among the other items: before the first one whose middle
     is below it. */
  function placeAt(list, item, y) {
    var others = items(list).filter(function (el) { return el !== item; });
    var before = null;
    for (var i = 0; i < others.length; i++) {
      var box = others[i].getBoundingClientRect();
      if (y < box.top + box.height / 2) { before = others[i]; break; }
    }
    if (before) list.insertBefore(item, before);
    else if (others.length) others[others.length - 1].after(item);
  }

  document.addEventListener("dragstart", function (event) {
    var handle = event.target.closest && event.target.closest("[data-sortable-item]");
    var list = handle && handle.parentElement;
    if (!list || !list.hasAttribute("data-sortable") || !event.dataTransfer) return;
    /* Only from the handle, so selecting a name's text still works. */
    if (!handle.dataset.armed) { event.preventDefault(); return; }
    dragged = handle;
    remember(list);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", handle.dataset.id || "");
    handle.classList.add("is-dragging");
  });

  document.addEventListener("mousedown", function (event) {
    var grip = event.target.closest && event.target.closest("[data-sortable-handle]");
    var item = grip && grip.closest("[data-sortable-item]");
    if (item) item.dataset.armed = "1";
  });
  document.addEventListener("mouseup", function () {
    document.querySelectorAll("[data-sortable-item][data-armed]").forEach(function (el) {
      delete el.dataset.armed;
    });
  });

  document.addEventListener("dragover", function (event) {
    if (!dragged) return;
    var list = dragged.parentElement;
    if (!list || !list.contains(event.target)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
    placeAt(list, dragged, event.clientY);
  });

  document.addEventListener("drop", function (event) {
    if (!dragged) return;
    event.preventDefault();
  });

  document.addEventListener("dragend", function () {
    if (!dragged) return;
    var item = dragged;
    dragged = null;
    item.classList.remove("is-dragging");
    delete item.dataset.armed;
    save(item.parentElement);
  });

  /* A finger: pointer events on the handle, which has `touch-action: none`
     so the page does not scroll instead. */
  document.addEventListener("pointerdown", function (event) {
    if (event.pointerType === "mouse") return;
    var grip = event.target.closest && event.target.closest("[data-sortable-handle]");
    var item = grip && grip.closest("[data-sortable-item]");
    var list = item && item.parentElement;
    if (!list || !list.hasAttribute("data-sortable")) return;
    event.preventDefault();
    remember(list);
    item.classList.add("is-dragging");
    grip.setPointerCapture(event.pointerId);
    function move(e) { placeAt(list, item, e.clientY); }
    function end() {
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", end);
      grip.removeEventListener("pointercancel", end);
      item.classList.remove("is-dragging");
      save(list);
    }
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", end);
    grip.addEventListener("pointercancel", end);
  });

  /* The keyboard: Arrow Up / Arrow Down on a focused handle, saved once the
     keys stop. */
  var pending = null;
  document.addEventListener("keydown", function (event) {
    var grip = event.target.closest && event.target.closest("[data-sortable-handle]");
    if (!grip || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    var item = grip.closest("[data-sortable-item]");
    var list = item && item.parentElement;
    if (!list || !list.hasAttribute("data-sortable")) return;
    event.preventDefault();
    if (!pending) remember(list);
    var all = items(list);
    var at = all.indexOf(item);
    if (event.key === "ArrowUp" && at > 0) list.insertBefore(item, all[at - 1]);
    if (event.key === "ArrowDown" && at < all.length - 1) all[at + 1].after(item);
    grip.focus();
    clearTimeout(pending);
    pending = setTimeout(function () { pending = null; save(list); }, 700);
  });
})();
