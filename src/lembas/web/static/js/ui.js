/*
  Toasts and dialogs.

  Replaces window.confirm/prompt, which cannot be styled, ignore the theme, and
  block the whole tab. Dialogs are built on <dialog>, so focus trapping, Escape
  and inertness of the page behind come from the browser rather than from
  hand-written key handling.

  Everything returns a Promise, so callers read as if they were still using the
  built-ins:

      if (await lembas.confirm({ message: "Delete this?" })) { ... }
      const name = await lembas.prompt({ message: "New name", value: old });
      lembas.notify("Saved.", { kind: "success" });
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var TOAST_MS = 4000;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    // textContent, never innerHTML: these messages carry filenames, chat
    // titles and upstream error text, none of which is ours to trust.
    if (text != null) node.textContent = text;
    return node;
  }

  /* --- Toasts ------------------------------------------------------------ */
  function toastHost() {
    var host = document.getElementById("toasts");
    if (!host) {
      host = el("div", "toasts");
      host.id = "toasts";
      // Announced politely so a screen reader hears it without being yanked
      // away from whatever it was reading.
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    return host;
  }

  function notify(message, options) {
    options = options || {};
    var toast = el("div", "toast toast--" + (options.kind || "info"));
    toast.appendChild(el("span", "toast__text", message));

    /* Some news is worth acting on where it is read: "a new version is ready"
       with no way to take it is a sentence that sends somebody looking for a
       menu. One action, never two -- a toast is not a dialog, and anything
       needing a choice should be one. */
    if (options.action && options.action.label) {
      var act = el("button", "btn btn--sm toast__action", options.action.label);
      act.type = "button";
      act.addEventListener("click", function () {
        dismiss(toast);
        if (options.action.run) options.action.run();
      });
      toast.appendChild(act);
    }

    var close = el("button", "toast__close");
    close.type = "button";
    close.setAttribute("aria-label", t("Dismiss"));
    close.textContent = "×";
    close.addEventListener("click", function () { dismiss(toast); });
    toast.appendChild(close);

    toastHost().appendChild(toast);
    // Next frame, so the entry transition has a state to move from.
    requestAnimationFrame(function () { toast.classList.add("is-in"); });

    /* A toast offering an action must not take it away while it is being read.
       Anything with a button stays until it is answered or dismissed. */
    var timeout = options.timeout == null
      ? (options.action ? 0 : TOAST_MS)
      : options.timeout;
    if (timeout > 0) setTimeout(function () { dismiss(toast); }, timeout);
    return toast;
  }

  function dismiss(toast) {
    if (!toast || toast.dataset.going) return;
    toast.dataset.going = "1";
    toast.classList.remove("is-in");
    setTimeout(function () { toast.remove(); }, 180);
  }

  /* --- Dialogs ----------------------------------------------------------- */
  function buildDialog(options) {
    var dialog = el("dialog", "dialog");
    var form = el("form", "dialog__form");
    form.method = "dialog";

    if (options.title) form.appendChild(el("h2", "dialog__title", options.title));
    if (options.message) form.appendChild(el("p", "dialog__message", options.message));

    var input = null;
    if (options.kind === "prompt") {
      input = el("input", "input");
      input.type = "text";
      input.value = options.value || "";
      if (options.placeholder) input.placeholder = options.placeholder;
      input.setAttribute("aria-label", options.title || options.message || t("Value"));
      form.appendChild(input);
    }

    var actions = el("div", "dialog__actions");

    var cancel = el("button", "btn", options.cancelLabel || t("Cancel"));
    cancel.type = "button";
    cancel.value = "cancel";
    actions.appendChild(cancel);

    var accept = el(
      "button",
      "btn " + (options.danger ? "btn--danger-solid" : "btn--primary"),
      options.confirmLabel || t("OK")
    );
    accept.type = "submit";
    accept.value = "accept";
    actions.appendChild(accept);

    form.appendChild(actions);
    dialog.appendChild(form);
    document.body.appendChild(dialog);

    return { dialog: dialog, form: form, input: input, cancel: cancel, accept: accept };
  }

  function open(options) {
    return new Promise(function (resolve) {
      var parts = buildDialog(options);
      var settled = false;

      function finish(value) {
        if (settled) return;
        settled = true;
        resolve(value);
        parts.dialog.close();
        // Let the closing transition finish before the node disappears.
        setTimeout(function () { parts.dialog.remove(); }, 200);
      }

      parts.cancel.addEventListener("click", function () { finish(options.kind === "prompt" ? null : false); });
      parts.form.addEventListener("submit", function (event) {
        event.preventDefault();
        finish(options.kind === "prompt" ? (parts.input.value || "") : true);
      });
      // Escape and the backdrop both mean "no".
      parts.dialog.addEventListener("cancel", function (event) {
        event.preventDefault();
        finish(options.kind === "prompt" ? null : false);
      });
      parts.dialog.addEventListener("click", function (event) {
        if (event.target === parts.dialog) finish(options.kind === "prompt" ? null : false);
      });

      parts.dialog.showModal();
      if (parts.input) {
        parts.input.focus();
        parts.input.select();
      } else {
        (options.danger ? parts.cancel : parts.accept).focus();
      }
    });
  }

  function confirm(options) {
    if (typeof options === "string") options = { message: options };
    return open(Object.assign({ kind: "confirm", confirmLabel: t("OK") }, options));
  }

  function prompt(options) {
    if (typeof options === "string") options = { message: options };
    return open(Object.assign({ kind: "prompt", confirmLabel: t("Save") }, options));
  }

  window.lembas = window.lembas || {};
  window.lembas.notify = notify;
  window.lembas.confirm = confirm;
  window.lembas.prompt = prompt;

  /* --- htmx integration --------------------------------------------------
     hx-confirm normally calls window.confirm. Intercepting the event lets every
     existing hx-confirm attribute keep working while getting the themed dialog,
     with no change at the call sites. */
  document.addEventListener("htmx:confirm", function (event) {
    if (!event.detail.question) return;  // no confirmation asked for
    event.preventDefault();

    var trigger = event.detail.elt;
    confirm({
      title: trigger && trigger.dataset.confirmTitle,
      message: event.detail.question,
      confirmLabel: (trigger && trigger.dataset.confirmLabel) || t("Delete"),
      danger: !trigger || trigger.dataset.confirmDanger !== "false",
    }).then(function (ok) {
      if (ok) event.detail.issueRequest(true);
    });
  });

  /* A submit button that acts on its own (formaction) rather than the form it
     sits in. Confirming the whole form would be wrong: the same form also has
     a plain Save. */
  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-confirm-button]");
    if (!button || button.dataset.confirmed) return;
    event.preventDefault();
    event.stopPropagation();
    confirm({
      title: button.dataset.confirmTitle,
      message: button.dataset.confirmButton,
      confirmLabel: button.dataset.confirmLabel || t("Delete"),
      danger: button.dataset.confirmDanger !== "false",
    }).then(function (ok) {
      if (!ok) return;
      button.dataset.confirmed = "1";
      button.click();
      delete button.dataset.confirmed;
    });
  }, true);

  /* Asking for one line of text before a request goes out: renaming a folder,
     renaming a chat.

     Deliberately NOT htmx's own hx-prompt. htmx calls the browser's prompt()
     synchronously and only then fires htmx:prompt with the answer already in
     hand -- so intercepting the event cannot supply a different one, and the
     native box appears regardless. Cancelling the event only aborts the
     request. This is the data-confirm-button shape instead: swallow the click,
     ask in our own dialog, write the answer where htmx will collect it, and
     click again behind a guard flag.

     The answer goes into hx-vals as a normal field rather than into a header,
     because every route that wants it already reads a form. htmx reads
     attributes when the request is built, so setting it just before the second
     click is enough. It is JSON.stringify'd, never concatenated: a folder
     called `"` would otherwise produce hx-vals that does not parse, and the
     request would go out with the field missing rather than with the name. */
  document.addEventListener("click", function (event) {
    var el = event.target.closest("[data-prompt]");
    if (!el || el.dataset.prompted) return;
    event.preventDefault();
    event.stopPropagation();

    var field = el.dataset.promptField || "name";
    prompt({
      title: el.dataset.promptTitle,
      message: el.dataset.prompt,
      value: el.dataset.promptValue || "",
      confirmLabel: el.dataset.promptLabel || t("Save"),
    }).then(function (value) {
      /* null is Cancel. An empty string is somebody clearing the box and
         pressing Save, which is not a rename either -- the routes ignore a
         blank name, so sending it would be a request that does nothing. */
      if (value === null || !String(value).trim()) return;
      var values = {};
      values[field] = String(value).trim();
      el.setAttribute("hx-vals", JSON.stringify(values));
      el.dataset.prompted = "1";
      el.click();
      delete el.dataset.prompted;
    });
  }, true);

  /* Plain forms opt in with data-confirm, so they need no inline onsubmit. */
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.dataset || !form.dataset.confirm || form.dataset.confirmed) return;
    event.preventDefault();
    confirm({
      title: form.dataset.confirmTitle,
      message: form.dataset.confirm,
      confirmLabel: form.dataset.confirmLabel || t("Delete"),
      danger: form.dataset.confirmDanger !== "false",
    }).then(function (ok) {
      if (!ok) return;
      form.dataset.confirmed = "1";
      form.submit();
    });
  }, true);
})();

/*
  The model picker.

  A <select> cannot render an avatar, a description or capability badges, so
  the control is built out of buttons and a hidden input. Keyboard behaviour is
  written out by hand for the same reason -- there is no native widget doing it
  for us.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  function close(picker) {
    var menu = picker.querySelector("[data-picker-menu]");
    var toggle = picker.querySelector("[data-picker-toggle]");
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  }

  function closeAll(except) {
    document.querySelectorAll("[data-picker]").forEach(function (picker) {
      if (picker !== except) close(picker);
    });
  }

  function open(picker) {
    var menu = picker.querySelector("[data-picker-menu]");
    var toggle = picker.querySelector("[data-picker-toggle]");
    closeAll(picker);
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");

    var filter = menu.querySelector("[data-picker-filter]");
    if (filter) {
      filter.value = "";
      applyFilter(menu, "");
    }
    // Not on a touchscreen: focusing a text field there raises the keyboard,
    // which covers half the list the finger came to choose from. The filter
    // is one tap away for whoever wants it.
    /* A menu whose first control is not an option says which one with
       `data-picker-focus` (the effort slider in the composer's
       settings). */
    var first = menu.querySelector("[data-picker-focus]");
    if (first) {
      first.focus();
    } else if (filter && !window.matchMedia("(hover: none)").matches) {
      filter.focus();
    } else {
      var selected = menu.querySelector(".picker__option.is-selected") ||
                     menu.querySelector(".picker__option");
      if (selected) selected.focus();
    }
    // Keep the chosen model in view when the list is long.
    var current = menu.querySelector(".picker__option.is-selected");
    if (current) current.scrollIntoView({ block: "nearest" });
    refreshStates(menu);
  }

  /* Which models are loaded, asked for each time the model menu opens --
     llama-swap holds one at a time and it changes by the minute, so a value
     rendered with the page would be stale by the time anybody looked. Only
     models whose endpoint reports a state come back; everything else keeps an
     empty `data-model-state`, which draws nothing. While one is loading the
     menu asks again every two seconds, and stops when it closes. */
  function refreshStates(menu) {
    var list = menu.querySelector(".picker__list--models");
    if (!list || !window.fetch) return;
    clearTimeout(menu._stateTimer);
    fetch("/api/models/state", {
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    }).then(function (response) {
      return response.ok ? response.json() : null;
    }).then(function (data) {
      var states = (data && data.states) || {};
      var loading = false;
      list.querySelectorAll(".picker__option[data-model-id]").forEach(function (option) {
        var slot = option.querySelector("[data-model-state]");
        if (!slot) return;
        var state = states[option.dataset.modelId] || "";
        slot.dataset.modelState = state;
        if (state === "loading") loading = true;
        var label = slot.querySelector("[data-model-state-label]");
        if (label) {
          label.textContent = state === "loaded" ? list.dataset.labelLoaded
            : state === "loading" ? list.dataset.labelLoading : "";
        }
      });
      if (loading && !menu.hidden) {
        menu._stateTimer = setTimeout(function () {
          if (!menu.hidden) refreshStates(menu);
        }, 2000);
      }
    }).catch(function () { /* No state is a menu without dots, not an error. */ });
  }

  function applyFilter(menu, needle) {
    var shown = 0;
    menu.querySelectorAll(".picker__option").forEach(function (option) {
      var match = !needle || option.dataset.pickerSearch.indexOf(needle) !== -1;
      option.hidden = !match;
      if (match) shown += 1;
    });
    var empty = menu.querySelector("[data-picker-empty]");
    if (empty) empty.hidden = shown > 0;
  }

  function choose(picker, value) {
    var navigate = picker.querySelector("[data-picker-navigate]");
    if (navigate) {
      window.location = navigate.dataset.pickerNavigate + encodeURIComponent(value);
      return;
    }
    var input = picker.querySelector("[data-picker-input]");
    if (input) {
      input.value = value;
      // htmx listens for change on the input; assigning .value does not fire it.
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    // Reflect the choice immediately rather than waiting for a reload.
    picker.querySelectorAll(".picker__option").forEach(function (option) {
      var selected = option.dataset.pickerValue === value;
      option.classList.toggle("is-selected", selected);
      option.setAttribute("aria-selected", selected ? "true" : "false");
    });
    var chosen = picker.querySelector('[data-picker-value="' + CSS.escape(value) + '"]');
    var label = picker.querySelector(".picker__label");
    var avatar = picker.querySelector(".picker__button .picker__avatar");
    if (chosen && label) {
      label.textContent = chosen.querySelector(".picker__option-name").textContent.trim();
    }
    if (chosen && avatar) {
      var source = chosen.querySelector(".picker__avatar");
      if (source) avatar.replaceWith(source.cloneNode(true));
    }
    close(picker);
    if (window.lembas && window.lembas.notify) {
      window.lembas.notify(t("Model switched to %(model)s", { model: label ? label.textContent : value }), {
        kind: "info", timeout: 2000,
      });
    }
  }

  document.addEventListener("click", function (event) {
    var toggle = event.target.closest("[data-picker-toggle]");
    if (toggle) {
      var picker = toggle.closest("[data-picker]");
      var menu = picker.querySelector("[data-picker-menu]");
      if (menu.hidden) open(picker); else close(picker);
      return;
    }

    var option = event.target.closest("[data-picker-value]");
    if (option) {
      choose(option.closest("[data-picker]"), option.dataset.pickerValue);
      return;
    }

    /* A menu item is an action, so the menu has served its purpose the moment
       one is pressed. Only `choose` used to close anything, which left the
       attach menu standing open over the composer after picking from it. The
       item's own handler -- htmx, or the [data-attach] and [data-toggle]
       delegates in app.js -- still runs; this only puts the menu away.
       `menuitemradio` too -- the Agents side's per-device filter is a
       menu of three mutually exclusive choices, which is that role. */
    var item = event.target.closest(
      '[data-picker-menu] [role="menuitem"], [data-picker-menu] [role="menuitemradio"]'
    );
    if (item) {
      close(item.closest("[data-picker]"));
      return;
    }

    if (!event.target.closest("[data-picker-menu]")) closeAll(null);
  });

  document.addEventListener("input", function (event) {
    if (!event.target.matches("[data-picker-filter]")) return;
    applyFilter(
      event.target.closest("[data-picker-menu]"),
      event.target.value.trim().toLowerCase()
    );
  });

  document.addEventListener("keydown", function (event) {
    var picker = event.target.closest("[data-picker]");
    if (!picker) return;
    var menu = picker.querySelector("[data-picker-menu]");

    if (event.key === "Escape" && !menu.hidden) {
      event.preventDefault();
      close(picker);
      picker.querySelector("[data-picker-toggle]").focus();
      return;
    }

    if (menu.hidden) {
      if (event.key === "ArrowDown" || event.key === "Enter") {
        if (event.target.matches("[data-picker-toggle]")) {
          event.preventDefault();
          open(picker);
        }
      }
      return;
    }

    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    /* A slider in a menu takes its own arrows: all four move it, natively. */
    if (event.target.matches('input[type="range"]')) return;
    event.preventDefault();
    var options = Array.prototype.filter.call(
      menu.querySelectorAll(".picker__option"), function (o) { return !o.hidden; }
    );
    if (!options.length) return;
    var at = options.indexOf(document.activeElement);
    var step = event.key === "ArrowDown" ? 1 : -1;
    var next = at === -1 ? 0 : (at + step + options.length) % options.length;
    options[next].focus();
  });
})();

/*
  Something arrived.

  The sidebar polls /api/chats/unread; the response carries out-of-band spans
  for the dots and, when something has just landed, an HX-Trigger listing it.
  Announcing it here rather than server-side keeps the wording and the timing in
  one place.

  Three things happen, and they are deliberately not the same thing three times:

  - A **toast**, always. It is the answer for somebody who is looking at the
    page, and it is the only one of the three that needs no permission and
    cannot be switched off by an operating system.
  - A **count in the tab title**, while the tab is not the one being looked at.
    This is the part that was missing and that nothing else replaces: a schedule
    that fires while you are in another tab lit a green dot in a corner you
    could not see. Cleared the moment the page is looked at again, because a
    badge you have to dismiss is worse than none.
  - A **browser notification**, if the reader has asked for one. Only while the
    page is hidden -- notifying somebody about something they are watching
    happen is the behaviour that gets notifications turned off for good.
*/
(function () {
  /* The words, in the reader's language: see `lembasT` in base.html. Defined
     in this scope too: without it the notifications switch and the agent
     picker's directory label throw a ReferenceError. */
  var t = window.lembasT || function (text) { return text; };
  var NOTIFY_KEY = "lembas-desktop-notifications";
  /* Kept per browser rather than on the account, because the *permission* is
     per browser and per origin. A preference that followed somebody to a
     machine where they had never granted it would be a switch that reads "on"
     and does nothing. */
  var baseTitle = document.title;
  var pending = 0;

  function wanted() {
    try {
      return window.localStorage.getItem(NOTIFY_KEY) === "1";
    } catch (error) {
      return false;
    }
  }

  function setWanted(on) {
    try {
      window.localStorage.setItem(NOTIFY_KEY, on ? "1" : "0");
    } catch (error) { /* private mode; the toast still works */ }
  }

  function retitle() {
    document.title = pending > 0 ? "(" + pending + ") " + baseTitle : baseTitle;
  }

  /* The title is rewritten by navigation and by a rename arriving out of band,
     so the base is re-read rather than captured once. Without this, renaming a
     chat while something is unread would pin the old name until a reload. */
  function rebase() {
    var shown = document.title;
    var stripped = shown.replace(/^\(\d+\)\s*/, "");
    if (stripped !== baseTitle) { baseTitle = stripped; retitle(); }
  }

  function clear() {
    if (!pending) return;
    pending = 0;
    retitle();
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) clear();
  });
  window.addEventListener("focus", clear);

  function describe(items) {
    if (items.length > 1) return t("%(n)s new arrivals", { n: items.length });
    var item = items[0];
    if (item.kind === "message") return t("New message");
    return t("Reply ready in “%(title)s”", { title: item.title });
  }

  /* `registration.showNotification` where there is a service worker, because
     `new Notification()` throws outright on Android Chrome -- so the plain
     constructor alone would work on every desktop it was tested on and on no
     phone at all. */
  function show(message, url) {
    if (!wanted() || !("Notification" in window)) return;
    if (Notification.permission !== "granted") return;

    /* `tag` collapses several into one: a browser left in the background for an
       hour must not come back to a stack of them. */
    var options = {
      body: message,
      tag: "lembas-unread",
      icon: "/static/img/icon-192.png",
      data: { url: url },
    };
    if (navigator.serviceWorker && navigator.serviceWorker.ready) {
      navigator.serviceWorker.ready
        .then(function (registration) { registration.showNotification("LLeMbas", options); })
        .catch(function () { plain(options, url); });
      return;
    }
    plain(options, url);
  }

  function plain(options, url) {
    try {
      var notification = new Notification("LLeMbas", options);
      notification.onclick = function () { window.focus(); if (url) location.href = url; };
    } catch (error) { /* unsupported; the toast and the title still carry it */ }
  }

  document.addEventListener("lembas:unread", function (event) {
    var items = (event.detail && event.detail.items) || [];
    if (!items.length) return;

    var message = describe(items);
    if (window.lembas && window.lembas.notify) {
      window.lembas.notify(message, { kind: "success", timeout: 6000 });
    }
    if (document.hidden) {
      rebase();
      pending += items.length;
      retitle();
      show(message, items.length === 1 ? items[0].url : "");
    }
  });

  /* Asking. `requestPermission` must be called from a gesture, so every path to
     it is a button somebody pressed -- a preference restored on load and acted
     on is refused by the browser with nothing said anywhere.

     Shared by the Settings toggle and by the one-time offer below, because two
     copies of "ask, then interpret the three answers" is two places for the
     denied case to be got wrong. */
  function ask(onSettled) {
    if (!("Notification" in window)) {
      window.lembas.notify(t("This browser has no notifications to offer."), { kind: "error" });
      return;
    }
    if (Notification.permission === "denied") {
      window.lembas.notify(
        t("Notifications are blocked for this site in your browser's own settings, which is the only place that can be undone."),
        { kind: "error", timeout: 8000 }
      );
      return;
    }
    Notification.requestPermission().then(function (result) {
      if (result !== "granted") {
        window.lembas.notify(t("Left off — nothing was changed."));
        if (onSettled) onSettled();
        return;
      }
      setWanted(true);
      if (onSettled) onSettled();
      subscribe().then(function (pushed) {
        window.lembas.notify(
          pushed
            ? t("Notifications on, including while LLeMbas is closed.")
            : t("Notifications on while LLeMbas is open."),
          { kind: "success", timeout: 6000 }
        );
      });
    });
  }

  /*
    Registering with the browser's push service.

    This is what makes a notification arrive with nothing of ours running --
    the poll above needs an open page, and the case worth notifying about is a
    schedule firing at seven in the morning.

    Best effort, always. It needs a service worker (so HTTPS or localhost), a
    push service the browser can reach, and a `PushManager` that some browsers
    do not have; every one of those fails to "notifications while LLeMbas is
    open", which still works. A permission granted and a subscription refused
    must not read as a failure, because most of the feature is still there.
  */
  function subscribe() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      return Promise.resolve(false);
    }
    return fetch("/api/push/key")
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (data) {
        if (!data || !data.key) return false;
        return navigator.serviceWorker.ready.then(function (registration) {
          return registration.pushManager.subscribe({
            /* Required, and not merely conventional: a browser refuses a
               subscription that does not promise every push will be shown to
               somebody. It is also why the worker's `push` handler always ends
               in a notification unless a window is focused. */
            userVisibleOnly: true,
            applicationServerKey: bytes(data.key),
          });
        }).then(function (subscription) {
          return fetch("/api/push/subscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(subscription.toJSON()),
          }).then(function (response) { return response.ok; });
        });
      })
      .catch(function () { return false; });
  }

  function unsubscribe() {
    if (!("serviceWorker" in navigator)) return Promise.resolve();
    return navigator.serviceWorker.ready
      .then(function (registration) { return registration.pushManager.getSubscription(); })
      .then(function (subscription) {
        if (!subscription) return null;
        var endpoint = subscription.endpoint;
        return subscription.unsubscribe().then(function () {
          return fetch("/api/push/unsubscribe", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: endpoint }),
          });
        });
      })
      .catch(function () { return null; });
  }

  /* base64url to bytes. `applicationServerKey` wants the raw 65-byte point and
     will not take the string, and `atob` will not take base64url -- the two
     substitutions and the padding are the whole of this. */
  function bytes(text) {
    var padded = (text + "===".slice((text.length + 3) % 4))
      .replace(/-/g, "+")
      .replace(/_/g, "/");
    var raw = window.atob(padded);
    var out = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-notify-toggle]");
    if (!button) return;
    event.preventDefault();

    if (wanted()) {
      setWanted(false);
      // The registration goes too. Leaving it would mean this server kept
      // sending to a browser that has been told to stop showing them, which is
      // traffic to a third party for something switched off.
      unsubscribe();
      paint(button);
      return;
    }
    ask(function () { paint(button); });
  });

  /*
    Offering, once.

    The browser's own permission box cannot be called on page load and should
    not be: it appears with no context, and a box somebody dismisses without
    reading is a permission that can only be undone in browser settings they
    will never find. So the offer is ours first -- a themed dialog that says
    what the notifications are for -- and pressing its button is the gesture the
    browser needs.

    Once, ever, per browser. "Not now" is recorded exactly as firmly as "yes":
    an offer that comes back is the thing that makes people block a site to
    silence it, and Settings has the switch for anybody who changes their mind.
  */
  var ASKED_KEY = "lembas-notifications-asked";

  function offer() {
    if (!document.body || !document.body.dataset.authenticated) return;
    if (!("Notification" in window) || Notification.permission !== "default") return;
    try {
      if (window.localStorage.getItem(ASKED_KEY)) return;
      window.localStorage.setItem(ASKED_KEY, "1");
    } catch (error) {
      return;  // no way to remember having asked, so do not ask
    }
    if (!window.lembas || !window.lembas.confirm) return;

    window.lembas.confirm({
      title: t("Notifications"),
      message: t("Let LLeMbas tell you when a reply, a report or a scheduled run arrives while you are looking at something else?"),
      confirmLabel: t("Turn on"),
      cancelLabel: t("Not now"),
    }).then(function (yes) { if (yes) ask(scan); });
  }

  function paint(button) {
    var on = wanted() && "Notification" in window && Notification.permission === "granted";
    button.textContent = on ? t("Turn off notifications") : t("Turn on notifications");
    button.setAttribute("aria-pressed", on ? "true" : "false");
    var hint = document.querySelector("[data-notify-state]");
    if (!hint) return;
    if (!("Notification" in window)) {
      hint.textContent = t("This browser has no notifications to offer.");
    } else if (Notification.permission === "denied") {
      hint.textContent = t("Blocked for this site in your browser's settings, which is the only place that can be undone.");
    } else if (on) {
      hint.textContent = t("On in this browser. Nothing is shown while you are looking at the page.");
    } else {
      hint.textContent = t("Off in this browser.");
    }
  }

  function scan() {
    document.querySelectorAll("[data-notify-toggle]").forEach(paint);
  }

  function start() {
    scan();
    /* After the page has settled rather than during it: the offer is a dialog,
       and one that appears while the shell is still being painted reads as an
       error rather than as a question. */
    setTimeout(offer, 1500);
  }

  document.addEventListener("DOMContentLoaded", start);
  document.body && start();
  /* Not on a stream frame (`isStreamFrame` in app.js): a reply being written
     brings no notification toggle with it. */
  document.addEventListener("htmx:afterSettle", function (event) {
    if (!(window.lembas && window.lembas.isStreamFrame && window.lembas.isStreamFrame(event))) scan();
  });
})();

/*
  Two number fields set together.

  Image sizes come in pairs and nobody types 1024 and then 1536; they pick
  "portrait". A button rather than a select of pairs, because a select would
  have to enumerate every combination somebody might want and these are only
  the common ones — the two boxes are still there and still authoritative.

  Delegated and keyed on the attributes rather than on the image page's ids, so
  the next screen with a paired number wants no new code.
*/
document.addEventListener("click", function (event) {
  var preset = event.target.closest("[data-width][data-height]");
  if (!preset) return;
  var row = preset.closest("[data-size-presets]");
  if (!row) return;
  event.preventDefault();

  var form = preset.closest("form");
  if (!form) return;
  var width = form.querySelector('[name="default_width"]');
  var height = form.querySelector('[name="default_height"]');
  if (width) width.value = preset.dataset.width;
  if (height) height.value = preset.dataset.height;
});

/*
  A toast asked for by the server.

  Some routes answer 204 because there is nothing to swap, and still have
  something to say -- answering a question that has already timed out, for
  instance. `HX-Trigger: {"lembas:notify": {"message": …}}` is how they say it.
*/
document.addEventListener("lembas:notify", function (event) {
  var detail = event.detail || {};
  if (!detail.message || !window.lembas || !window.lembas.notify) return;
  window.lembas.notify(detail.message, { kind: detail.kind || "" });
});

/*
  Send becomes Stop while a reply is being written.

  One button in the markup (see chat/_composer.html), retargeted here. The
  composer and the streaming bubble are far apart in the document, so the link
  between them is made at runtime: whenever the thread changes, look for a
  message that is still streaming and point the button at it. A
  MutationObserver rather than htmx events, because the bubble is replaced by
  an SSE swap that does not always surface as one.

  This used to build a second button and hide it with the `hidden` attribute,
  which did nothing at all: `.btn` sets `display: inline-flex`, and that beats
  the browser's `[hidden] { display: none }`. app.css now forces the attribute
  to win, and there is only one button to get wrong.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  function streamingMessage() {
    var live = document.querySelector(".msg[sse-connect]");
    if (!live) return null;
    var id = live.id.replace(/^msg-/, "");
    var chat = (live.getAttribute("sse-connect") || "").match(/\/api\/chats\/([^/]+)\//);
    return chat ? { messageId: id, chatId: chat[1] } : null;
  }

  /* Something typed turns Stop back into Send while a reply runs: the
     message is queued -- and taken into the reply at its next step where the
     reply can take one (a device's, or between tool rounds) -- exactly as Enter
     already did. With the box empty it is Stop, because that is the only thing
     the button could mean. Before this it stayed Stop, and nothing said a
     second message could be sent at all. */
  function typed() {
    var box = document.querySelector(".composer__input");
    return !!(box && box.value.trim());
  }

  /* A stop that has been asked for and not yet answered. The `done` frame
     answers it: it replaces the article with the finished one, which has
     neither `sse-connect` nor this mark, so nothing has to clear it. */
  function stopping() {
    return !!document.querySelector(".msg[data-stopping][sse-connect]");
  }

  /* Written only when it differs. This runs on every mutation of the thread
     the observer below lets through, and setting an attribute to the value it
     already holds still invalidates style on some engines. */
  function put(button, name, value) {
    if (button.getAttribute(name) !== value) button.setAttribute(name, value);
  }

  function sync() {
    var button = document.querySelector("[data-composer-action]");
    if (!button) return;
    var active = streamingMessage() && !typed();
    var halting = active && stopping();

    put(button, "data-composer-action", active ? "stop" : "send");
    // As a submit button the form sends; as a plain button the click handler
    // below stops. Nothing else distinguishes the two states.
    put(button, "type", active ? "button" : "submit");
    var queues = !active && streamingMessage();
    var label = halting ? t("Stopping…") : active ? t("Stop generating") : queues ? t("Send — it waits for its turn") : t("Send");
    put(button, "aria-label", label);
    put(button, "title", halting ? label : active ? t("Stop generating") : queues ? t("Sent now, taken in at the reply's next step or after it") : "");
    /* Stop stays pressed while a stop is under way. This line used to say
       `false` unconditionally, and since every stream frame runs it, the button
       came back a twelfth of a second after it was pressed -- with nothing to
       say the press had registered, Stop felt dead and got pressed again.
       Something typed makes it Send, which is a different request and is not
       held back by a stop. */
    if (button.disabled !== halting) button.disabled = halting;
  }

  /* What the bubble says while it stops: "Stopping…" in its status row, in a
     span of its own beside `.msg__status` rather than in it. That span is an
     `sse-swap` target, so the next status frame would overwrite the words; the
     row around it is not, and chat.css hides the live status while the mark
     is on. */
  function markStopping(article, on) {
    var row = article.querySelector(".msg__waiting");
    var note = article.querySelector("[data-stopping-note]");
    if (on) {
      article.setAttribute("data-stopping", "");
      if (row && !note) {
        note = document.createElement("span");
        note.className = "msg__status msg__status--stopping";
        note.setAttribute("data-stopping-note", "");
        note.setAttribute("role", "status");
        note.textContent = t("Stopping…");
        row.appendChild(note);
      }
    } else {
      article.removeAttribute("data-stopping");
      if (note) note.remove();
    }
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest('[data-composer-action="stop"]');
    if (!button) return;
    event.preventDefault();

    var target = streamingMessage();
    if (!target) return;
    var article = document.getElementById("msg-" + target.messageId);
    if (!article || article.hasAttribute("data-stopping")) return;
    // Marked before the request, so the reader sees the press at once and a
    // second click has nothing to fire: sync() keeps the button disabled for
    // as long as the mark is on a streaming bubble.
    markStopping(article, true);
    sync();

    /* A stop that did not land puts everything back -- the mark, the button --
       and says so, rather than leaving a bubble that claims to be stopping and
       a button nobody can press again. A 404 is a failure as much as a dropped
       connection: `fetch` resolves on any status. */
    function failed() {
      markStopping(article, false);
      sync();
      if (window.lembas && window.lembas.notify) {
        window.lembas.notify(t("The reply could not be stopped. Try again."), { kind: "error" });
      }
    }
    fetch(
      "/api/chats/" + target.chatId + "/messages/" + target.messageId + "/stop",
      { method: "POST", credentials: "same-origin" }
    ).then(function (response) {
      if (!response.ok) failed();
    }).catch(failed);
  });

  /* Every mutation of the thread, except the ones a stream frame makes inside
     the bubble it is writing: those cannot change whether a reply is
     streaming, and they arrive a dozen times a second. A bubble arriving,
     leaving or being replaced (the `done` frame) is a mutation of its parent,
     which is not inside a streaming bubble, so it still counts. */
  function relevant(records) {
    for (var i = 0; i < records.length; i++) {
      var node = records[i].target;
      if (!(node.closest && node.closest(".msg[sse-connect]"))) return true;
    }
    return false;
  }

  function watch() {
    var thread = document.getElementById("thread");
    if (thread) {
      new MutationObserver(function (records) {
        if (relevant(records)) sync();
      }).observe(thread, { childList: true, subtree: true });
    }
    sync();
  }

  document.addEventListener("DOMContentLoaded", watch);
  document.body && document.body.addEventListener("htmx:afterSettle", function (event) {
    if (!(window.lembas && window.lembas.isStreamFrame && window.lembas.isStreamFrame(event))) sync();
  });
  document.addEventListener("input", function (event) {
    if (event.target && event.target.classList && event.target.classList.contains("composer__input")) sync();
  });
})();

/*
  The agent's connection, directory and mode, on the new-chat composer.

  Shown only when the screen was opened as an agent chat (the sidebar's Agent
  side, `?kind=agent`); there is no Chat/Agent switch in the row.
  The radios are still read if a page carries them, so an older cached page
  submits what it shows.
*/
(function () {
  /* The words, in the reader's language: see `lembasT` in base.html. Defined
     in this scope too: without it the notifications switch and the agent
     picker's directory label throw a ReferenceError. */
  var t = window.lembasT || function (text) { return text; };
  /* The last segment of a path, with trailing slashes ignored so `/srv/app/`
     reads as `app` rather than as nothing. `/` is itself, since it has no name
     of its own and "the root" is what somebody means by it. */
  function baseName(path) {
    var trimmed = String(path || "").replace(/\/+$/, "");
    if (!trimmed) return "/";
    return trimmed.slice(trimmed.lastIndexOf("/") + 1) || "/";
  }

  function wire(root) {
    var kind = root.querySelector("#chat-kind");
    var extra = root.querySelector("[data-agent-extra]");
    var picker = root.querySelector('select[name="ssh_profile_id"]');
    var dir = root.querySelector("[data-dir-value]");
    var dirLabel = root.querySelector("[data-dir-label]");
    if (!kind || !extra) return;

    /* The directory is a hidden field plus a button, so the two have to be set
       together or the button shows one path and the form submits another.

       The button shows the directory's own name; the full path goes in the
       tooltip and, of course, in the field that is submitted. A real project
       path is long enough that showing it whole made the button eat the row and
       squeeze the mode select beside it, and the leading directories are the
       part nobody is reading -- what you check before sending is that you are
       in `myproject` rather than `myproject-old`. */
    function setDir(value) {
      if (!dir) return;
      dir.value = value || "";
      if (dirLabel) dirLabel.textContent = value ? baseName(value) : t("Choose a directory");
      var button = dirLabel && dirLabel.closest("[data-dir-browse]");
      if (button) {
        button.title = value || t("Choose the directory this chat works in");
        if (value) button.removeAttribute("aria-invalid");
      }
      announce();
    }

    function sync() {
      var chosen = root.querySelector('input[name="kind_choice"]:checked');
      var agent = chosen ? chosen.value === "agent" : kind.value === "agent";
      kind.value = agent ? "agent" : "chat";
      extra.hidden = !agent;
    }

    /* A device says the most it allows (its own `remote.max_mode`), and that is
       where its chats start: a device's owner who chose Auto meant it.
       Like the directory, it follows the connection only until somebody picks a
       mode themselves. An SSH connection carries no mode of its own. */
    var mode = root.querySelector('select[name="agent_mode"]');
    if (mode) mode.addEventListener("change", function () { mode.dataset.touched = "1"; });
    function followMode() {
      if (!mode || mode.dataset.touched) return;
      var option = picker && picker.options[picker.selectedIndex];
      var wanted = option && option.dataset.mode;
      if (wanted && mode.querySelector('option[value="' + wanted + '"]')) mode.value = wanted;
    }

    /* The directories already used on the selected device, latest first
       (`data-recent`, from `chat.recent_dirs`). The first is the default; with
       none, nothing is chosen and sending waits until something is -- the
       device's own first root used to be filled in silently, which made `/`
       the directory of every first chat on a new machine. */
    function recentDirs() {
      var option = picker && picker.options[picker.selectedIndex];
      try {
        var list = JSON.parse((option && option.dataset.recent) || "[]");
        return Array.isArray(list) ? list.filter(function (p) { return typeof p === "string" && p; }) : [];
      } catch (error) {
        return [];
      }
    }

    function deviceDefault() { return recentDirs()[0] || ""; }

    /* Which machine and which directory the panels should be looking at.
       Dispatched rather than read, because nothing here owns the terminal or
       the canvas -- and because assigning to a hidden field's `.value` fires no
       event of its own, so `setDir` has to say so out loud. */
    /* A linked device has no SFTP behind it and no terminal or canvas
       here: the panels are told about SSH connections only. Its directory is
       chosen from what the device itself offers. */
    function onDevice() {
      return !!(picker && picker.value && picker.value.indexOf("device:") === 0);
    }

    function announce() {
      var chosen = kind && kind.value === "agent" && !onDevice() ? (picker ? picker.value : "") : "";
      document.dispatchEvent(new CustomEvent("lembas:agent-target", {
        detail: { profileId: chosen, projectDir: dir ? dir.value : "" }
      }));
    }

    root.addEventListener("change", function (event) {
      if (event.target.name === "kind_choice") { sync(); announce(); }
      // Following the device's latest directory is a convenience, not a rule:
      // once someone has chosen their own it is left alone.
      if (event.target === picker && dir && !dir.dataset.touched) {
        setDir(deviceDefault());
      }
      if (event.target === picker) followMode();
      if (event.target === picker) announce();
    });

    /* --- The directory menu --------------------------------------------------
       The button opens a short menu: the directories already used on this
       device, the current one marked, and "Browse…" last, which is the
       device's own directory browser (`chooseDirectory`). A device with no
       history goes straight to the browser -- a menu whose only item is
       "Browse…" is one more press to reach the same dialog.

       Shaped and driven like the connection chooser beside it (the upward
       select below): the same menu classes, Escape and the arrow keys, closed
       by a click elsewhere, and placed against the window with
       `position: fixed`, because `.composer__context` scrolls sideways on a
       phone and a scroller clips an absolute menu in both axes. */
    var dirButton = root.querySelector("[data-dir-browse]");
    var dirMenu = null;

    function browse() {
      var profileId = picker ? picker.value : "";
      if (!profileId || !dir) return;
      window.lembas.chooseDirectory(profileId, dir.value, function (chosen) {
        if (!chosen) return;
        dir.dataset.touched = "1";
        setDir(chosen);
      });
    }

    function closeDirMenu(refocus) {
      if (!dirMenu || dirMenu.hidden) return;
      dirMenu.hidden = true;
      if (dirButton) dirButton.setAttribute("aria-expanded", "false");
      if (refocus && dirButton) dirButton.focus();
    }

    function placeDirMenu() {
      var at = dirButton.getBoundingClientRect();
      var gap = 4;
      dirMenu.style.left = "0px";
      dirMenu.style.bottom = (window.innerHeight - at.top + gap) + "px";
      var width = dirMenu.getBoundingClientRect().width;
      var left = Math.min(at.left, window.innerWidth - width - 8);
      dirMenu.style.left = Math.max(8, left) + "px";
      dirMenu.style.maxHeight = Math.max(120, at.top - gap - 8) + "px";
    }

    function dirItem(path) {
      var current = path === (dir ? dir.value : "");
      var item = document.createElement("button");
      item.type = "button";
      item.className = "picker__option dir-menu__option" + (current ? " is-selected" : "");
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", current ? "true" : "false");
      item.title = path;
      item.dataset.dirRecent = path;
      item.innerHTML = '<svg class="icon icon--sm dir-menu__tick" aria-hidden="true"><use href="#i-check"/></svg>' +
        '<span class="dir-menu__path"><span class="dir-menu__text"></span></span>';
      /* textContent, never markup: a path is whatever a directory was called. */
      item.querySelector(".dir-menu__text").textContent = path;
      return item;
    }

    function openDirMenu(recent) {
      if (!dirMenu) {
        dirMenu = document.createElement("div");
        dirMenu.className = "picker__menu picker__menu--compact dir-menu";
        dirMenu.setAttribute("role", "menu");
        dirMenu.setAttribute("aria-label", t("Recent directories"));
        dirMenu.hidden = true;
        dirButton.parentNode.insertBefore(dirMenu, dirButton.nextSibling);
        dirMenu.addEventListener("click", function (event) {
          var item = event.target.closest("[data-dir-recent]");
          if (item) {
            dir.dataset.touched = "1";
            setDir(item.dataset.dirRecent);
            closeDirMenu(true);
            return;
          }
          if (event.target.closest("[data-dir-more]")) {
            closeDirMenu(false);
            browse();
          }
        });
        dirMenu.addEventListener("keydown", function (event) {
          var items = Array.prototype.slice.call(dirMenu.querySelectorAll(".picker__option"));
          var at = items.indexOf(document.activeElement);
          if (event.key === "Escape") { event.preventDefault(); closeDirMenu(true); }
          else if (event.key === "ArrowDown") { event.preventDefault(); (items[at + 1] || items[0]).focus(); }
          else if (event.key === "ArrowUp") { event.preventDefault(); (items[at - 1] || items[items.length - 1]).focus(); }
          else if (event.key === "Tab") { closeDirMenu(false); }
        });
        document.addEventListener("click", function (event) {
          if (dirMenu.hidden || dirMenu.contains(event.target)) return;
          if (dirButton.contains(event.target)) return;
          closeDirMenu(false);
        });
        window.addEventListener("resize", function () { if (!dirMenu.hidden) placeDirMenu(); });
        document.addEventListener("scroll", function (event) {
          if (!dirMenu.hidden && !dirMenu.contains(event.target)) placeDirMenu();
        }, true);
      }
      dirMenu.textContent = "";
      recent.forEach(function (path) { dirMenu.appendChild(dirItem(path)); });
      var more = document.createElement("button");
      more.type = "button";
      more.className = "picker__option dir-menu__option dir-menu__browse";
      more.setAttribute("role", "menuitem");
      more.dataset.dirMore = "1";
      more.innerHTML = '<svg class="icon icon--sm" aria-hidden="true"><use href="#i-folder"/></svg>' +
        '<span class="dir-menu__path"></span>';
      more.querySelector(".dir-menu__path").textContent = t("Browse…");
      dirMenu.appendChild(more);

      dirMenu.hidden = false;
      placeDirMenu();
      dirButton.setAttribute("aria-expanded", "true");
      var current = dirMenu.querySelector(".is-selected") || dirMenu.querySelector(".picker__option");
      // `preventScroll`: the menu is placed against the window, and focusing
      // inside the sideways scroller would scroll the row for nothing.
      if (current) current.focus({ preventScroll: true });
    }

    /* The button's popup is a menu when there is history and the browser's
       dialog when there is not; say which, for whoever is listening. */
    function paintDirButton() {
      if (dirButton) dirButton.setAttribute("aria-haspopup", recentDirs().length ? "menu" : "dialog");
    }
    if (picker) picker.addEventListener("change", paintDirButton);

    function chooseDir() {
      var recent = recentDirs();
      if (!recent.length) { browse(); return; }
      if (dirMenu && !dirMenu.hidden) { closeDirMenu(true); return; }
      openDirMenu(recent);
    }

    root.addEventListener("click", function (event) {
      if (!event.target.closest("[data-dir-browse]")) return;
      event.preventDefault();
      if (!picker || !picker.value) return;
      chooseDir();
    });

    /* No directory, no send. The server would still fall back to the device's
       first root, and stays tolerant for a client that sends nothing -- but
       this screen asks rather than guessing. Capture phase on the document, so
       it runs before htmx's own submit handler on the form. The message stays
       in the box. */
    var form = root.closest("form");
    if (form) {
      document.addEventListener("submit", function (event) {
        if (event.target !== form || !document.contains(root)) return;
        if (!kind || kind.value !== "agent" || !dir || dir.value) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (dirButton) dirButton.setAttribute("aria-invalid", "true");
        if (window.lembas && window.lembas.notify) {
          window.lembas.notify(t("Choose the directory this agent works in before sending."), { kind: "info" });
        }
        if (picker && picker.value) chooseDir();
      }, true);
    }

    sync();
    /* Seeded from whichever device the select is actually showing, not from
       the first in the list. A folder that names a directory wins, as it does
       on the server: the chat is being started in it on purpose. */
    var seed = dir ? dir.dataset.dirSeed || "" : "";
    if (seed) dir.dataset.touched = "1";
    setDir(seed || deviceDefault());
    paintDirButton();
    followMode();
    announce();
  }

  /* The same directory picker, on a form that is not the composer.

     Scoped to `[data-dir-field]`, which the composer's own markup does not
     carry -- otherwise this and `wire()` above would both answer one click and
     open two dialogs. The composer keeps its own handler because it has more to
     do: it follows the selected profile's default directory until somebody
     chooses their own, which only makes sense while a chat is being created.

     A path is something you would rather find than spell, and the text box this
     replaced was the one control on the folder page that asked somebody to
     remember an absolute path on another machine. */
  document.addEventListener("click", function (event) {
    var field = event.target.closest("[data-dir-field]");
    if (!field) return;

    var value = field.querySelector("[data-dir-value]");
    var label = field.querySelector("[data-dir-label]");
    if (!value) return;

    function show(path) {
      value.value = path || "";
      if (label) label.textContent = path || t("the connection's own default");
    }

    if (event.target.closest("[data-dir-clear]")) {
      event.preventDefault();
      show("");
      return;
    }
    if (!event.target.closest("[data-dir-browse]")) return;
    event.preventDefault();

    /* The connection this folder points at. Browsing needs one, and saying so
       beats a dialog that opens onto nothing. */
    var form = field.closest("form");
    var picker = form && form.querySelector('select[name="ssh_profile_id"]');
    var profileId = picker ? picker.value : "";
    if (!profileId) {
      window.lembas.notify(t("Choose a connection first — there is nothing to browse without one."));
      return;
    }
    window.lembas.chooseDirectory(profileId, value.value, show);
  });

  function scan() {
    document.querySelectorAll("[data-agent-picker]").forEach(function (el) {
      if (!el.dataset.wired) { el.dataset.wired = "1"; wire(el); }
    });
  }

  /* The two panel buttons, on the screen where the server cannot answer.

     Both the canvas and the terminal need an agent chat on a chosen connection,
     and before a chat exists both of those are radio buttons and a select in the
     composer -- nothing the server has seen. It used to answer with
     `profiles[0]`, so the buttons were offered on the new-chat screen whatever
     the toggle said and whatever was selected, and pressing either opened a
     panel that could not work.

     So the markup renders them `hidden` carrying `data-agent-only`, and this
     follows the event `wire()` already dispatches. Anything without that
     attribute is left alone: on a chat that exists the server's answer is
     complete and this must not second-guess it.

     Closing a panel whose target has just gone is not tidiness. The panel is
     still pointed at the old connection, and leaving it open would show one
     machine's files under a heading naming another. */
  document.addEventListener("lembas:agent-target", function (event) {
    var ready = !!(event.detail && event.detail.profileId);
    document.querySelectorAll("[data-agent-only]").forEach(function (button) {
      button.hidden = !ready;
      if (ready) return;
      var selector = button.dataset.toggle || "";
      var panel = selector && document.querySelector(selector);
      if (panel && !panel.hidden && window.lembas && window.lembas.setPanel) {
        window.lembas.setPanel(selector, false);
      }
    });
  });

  document.addEventListener("DOMContentLoaded", scan);
  document.body && scan();
  document.addEventListener("htmx:afterSettle", scan);
})();

/*
  Tabs remember their scroll position, and that made short panels look empty.

  A tab is a radio and a panel is shown by CSS, so switching one changes nothing
  about `.tabs__body` -- which is the element that scrolls. Read half way down
  the long Tools panel on /admin/prompts, click Context, and the container keeps
  a scrollTop the new panel is not tall enough to fill: the browser clamps it to
  that panel's bottom, and what lands on screen is the end of it above a screen
  of nothing. It reads as a page that failed to load, and the way out is to
  scroll up before scrolling down.

  Nothing in CSS can reset a scroll position, so this is the smallest amount of
  JavaScript that fixes it: on a tab change, put the container that actually
  scrolls back to the top. Delegated and keyed on the class rather than on any
  one page, because every tabbed screen here has the same problem.

  Which container that is depends on where the tabs are, and assuming it was
  always `.tabs__body` is why this did nothing at all on /admin/prompts for the
  whole life of the fix. `.tabs__body` scrolls only when `.tabs` is a flex child
  of something bounded -- true on the settings page, false under the admin
  layout, where the scroller is the `.admin-scroll` above it and `.tabs__body`
  has `height: auto`. Setting `scrollTop = 0` on an element that does not scroll
  is a silent no-op, which is exactly the kind of failure that survives review.

  So: walk up from the bar and reset the first ancestor that can scroll. That is
  correct on both shapes without knowing which one it is looking at.
*/
(function () {
  function scroller(node) {
    for (var el = node; el && el !== document.body; el = el.parentElement) {
      var overflow = getComputedStyle(el).overflowY;
      if ((overflow === "auto" || overflow === "scroll") && el.scrollHeight > el.clientHeight) {
        return el;
      }
    }
    return null;
  }

  document.addEventListener("change", function (event) {
    var radio = event.target;
    if (!radio || radio.type !== "radio") return;
    var bar = radio.closest && radio.closest(".tabs__bar");
    if (!bar) return;
    /* The body first, because on the settings page it is the scroller and is
       also the thing whose *content* changed; then whatever encloses the tabs.
       Both, not either: on the admin layout the body may still hold a scrolled
       inner panel while the page itself is what the reader is lost in. */
    var body = bar.parentElement && bar.parentElement.querySelector(".tabs__body");
    if (body) body.scrollTop = 0;

    /* And where the page itself is the scroller, put the bar back at the top of
       it -- not the page at zero. There is content above the tabs on
       /admin/prompts and the reader has just asked to look at a tab, so the tab
       bar is where they want to be.

       This has to happen *after* the panel has swapped, which it has: :checked
       applies before `change` fires. That order is the whole failure -- the
       browser scrolls the focused radio into view first, then the shorter panel
       shrinks the document and scrollTop is clamped to the new maximum, which
       for a short panel is somewhere below everything. */
    var outer = scroller(bar);
    if (!outer || outer === body) return;
    /* Moved by hand, and only `outer`. `scrollIntoView` scrolls *every*
       ancestor that can scroll, the document included -- and the document
       could, by the height of whatever leaked out of the scroller, so a tab
       switch lifted the whole shell and left a strip of background under it.
       The containing block in app.css stops the leak; this stops a leak
       anyone adds later from being turned into a visible one.

       Measured from `.tabs`, not the bar: the bar is sticky, so once the page
       is scrolled past the lede it reports the scroller's own top and the
       sum below would come out as nothing to do. */
    var tabs = bar.parentElement;
    outer.scrollTop += tabs.getBoundingClientRect().top - outer.getBoundingClientRect().top;
  });
})();

/*
  The effort: a slider in the composer's settings, over the select that
  holds the state. `/effort`, htmx and a model switch all read and write the
  select, as they did when it was the visible control -- so the slider only
  repaints from it, and moving the slider writes the select and fires its
  `change`, which is what saves it. The settings button is a plain icon; the
  level is shown only beside the slider.

  `input` moves the words as the thumb moves; `change` (released, or a key)
  is the one that saves -- a drag across four levels is one PATCH, not four.
*/
(function () {
  function levels(range) {
    try { return JSON.parse(range.dataset.levels || "[]"); } catch (e) { return []; }
  }
  function said(range, value) {
    return value === "off" ? (range.dataset.offLabel || "off") : value;
  }
  function boxOf(el) { return el.closest(".composer__settings"); }

  function paint() {
    document.querySelectorAll("[data-effort]").forEach(function (select) {
      var box = boxOf(select);
      if (!box) return;
      var value = String(select.value || "off");
      var range = box.querySelector("[data-effort-range]");
      if (!range) return;
      var at = levels(range).indexOf(value);
      if (at !== -1) range.value = String(at);
      show(range, value);
    });
  }
  function show(range, value) {
    range.setAttribute("aria-valuetext", said(range, value));
    var out = boxOf(range) && boxOf(range).querySelector("[data-effort-output]");
    if (out) out.textContent = said(range, value);
  }

  document.addEventListener("input", function (event) {
    var range = event.target;
    if (!range.matches || !range.matches("[data-effort-range]")) return;
    show(range, levels(range)[Number(range.value)] || "off");
  });
  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || !target.matches) return;
    if (target.matches("[data-effort-range]")) {
      var select = boxOf(target) && boxOf(target).querySelector("[data-effort]");
      var value = levels(target)[Number(target.value)] || "off";
      if (select && select.value !== value) {
        select.value = value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return;
    }
    if (target.matches("[data-effort]")) paint();
  });
  document.addEventListener("DOMContentLoaded", paint);
  document.body && document.body.addEventListener("htmx:afterSettle", paint);
})();

/*
  Continue is offered on the newest reply only. Marked here rather than
  by the server, because the newest one changes under a page that is not
  re-rendered: a reply finishing, a queued turn arriving.
*/
(function () {
  function mark() {
    var thread = document.getElementById("thread");
    if (!thread) return;
    var replies = thread.querySelectorAll(".msg--assistant");
    replies.forEach(function (reply, index) {
      reply.classList.toggle("is-newest", index === replies.length - 1);
    });
  }
  function watch() {
    var thread = document.getElementById("thread");
    if (thread) new MutationObserver(mark).observe(thread, { childList: true });
    mark();
  }
  document.addEventListener("DOMContentLoaded", watch);
  document.body && document.body.addEventListener("htmx:afterSettle", mark);
})();

/*
  A <select> whose list opens upward: `select[data-upward]`.

  The composer's connection chooser sits at the bottom of the window, and the
  browser's own list for a <select> drops below it -- off the screen -- where
  the composer's other menus all open upward. The select stays: hidden, in the
  form, and the one truth the other scripts read and set (its options' data-recent
  and data-mode, its change event). This only draws its options as a menu above
  it and keeps the button's label in step with whatever sets the value.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  var CHEVRON = '<svg class="icon icon--sm" aria-hidden="true"><use href="#i-chevron-down"/></svg>';

  function label(select) {
    var option = select.options[select.selectedIndex];
    return option ? option.textContent.trim() : "";
  }

  function build(select) {
    if (select.dataset.upwardReady) return;
    select.dataset.upwardReady = "1";

    var box = document.createElement("span");
    box.className = "upward picker--up";
    var button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn--sm upward__button";
    button.setAttribute("aria-haspopup", "listbox");
    button.setAttribute("aria-expanded", "false");
    if (select.getAttribute("aria-label")) button.setAttribute("aria-label", select.getAttribute("aria-label"));
    var text = document.createElement("span");
    text.className = "upward__label";
    button.appendChild(text);
    button.insertAdjacentHTML("beforeend", CHEVRON);
    var menu = document.createElement("div");
    menu.className = "picker__menu picker__menu--compact upward__menu";
    menu.setAttribute("role", "listbox");
    menu.hidden = true;

    select.parentNode.insertBefore(box, select);
    box.appendChild(select);
    box.appendChild(button);
    box.appendChild(menu);
    select.classList.add("upward__select");
    select.tabIndex = -1;
    select.setAttribute("aria-hidden", "true");

    function repaint() { text.textContent = label(select); }

    function fill() {
      menu.textContent = "";
      Array.prototype.forEach.call(select.children, function (child) {
        if (child.tagName === "OPTGROUP") {
          var heading = document.createElement("p");
          heading.className = "picker__group";
          heading.textContent = child.label;
          menu.appendChild(heading);
          Array.prototype.forEach.call(child.children, add);
        } else {
          add(child);
        }
      });
    }

    function add(option) {
      var item = document.createElement("button");
      item.type = "button";
      item.className = "picker__option" + (option.selected ? " is-selected" : "");
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", option.selected ? "true" : "false");
      item.disabled = option.disabled;
      item.textContent = option.textContent.trim();
      item.addEventListener("click", function () {
        select.value = option.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
        close();
        button.focus();
      });
      menu.appendChild(item);
    }

    /* The menu is placed against the window, not against its box. The
       connection chooser sits inside `.composer__context`, which scrolls
       sideways on a narrow screen (`overflow-x: auto`, chat.css) -- and a box
       that scrolls in one axis clips in both, because `overflow-y: visible`
       beside it computes to `auto`. So the menu opened above the row, inside a
       scroller one control high, and was cut away entirely: it was there,
       `hidden` off, 288px wide, and every pixel of it was the thread behind.
       Nothing on screen changed when the button was pressed, which is what
       "the connection button is broken" was, for one device or for five.

       `position: fixed` is what escapes a scroller's clip (nothing between
       here and the root is transformed, which would make it the containing
       block again). Measured at each open, so it follows the button wherever
       the row has scrolled to, and again on a resize or any scroll outside
       the menu, so it never floats where the button used to be. (Re-placed,
       not closed: on a phone the row itself scrolls sideways to bring the
       button into view, and closing on that scroll shut the menu the moment
       it opened.) */
    function place() {
      var at = button.getBoundingClientRect();
      var gap = 4;
      menu.classList.add("upward__menu--floating");
      menu.style.left = "0px";
      menu.style.bottom = (window.innerHeight - at.top + gap) + "px";
      var width = menu.getBoundingClientRect().width;
      var left = Math.min(at.left, window.innerWidth - width - 8);
      menu.style.left = Math.max(8, left) + "px";
      menu.style.maxHeight = Math.max(120, at.top - gap - 8) + "px";
    }

    function open() {
      fill();
      menu.hidden = false;
      place();
      button.setAttribute("aria-expanded", "true");
      var current = menu.querySelector(".picker__option.is-selected") || menu.querySelector(".picker__option");
      // `preventScroll`: focusing inside the sideways scroller would scroll
      // the row to "reveal" a menu that is placed against the window anyway.
      if (current) current.focus({ preventScroll: true });
    }

    function close() {
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }

    button.addEventListener("click", function () { if (menu.hidden) open(); else close(); });
    menu.addEventListener("keydown", function (event) {
      var items = Array.prototype.filter.call(menu.querySelectorAll(".picker__option"), function (b) { return !b.disabled; });
      var at = items.indexOf(document.activeElement);
      if (event.key === "Escape") { event.preventDefault(); close(); button.focus(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); (items[at + 1] || items[0]).focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); (items[at - 1] || items[items.length - 1]).focus(); }
    });
    document.addEventListener("click", function (event) {
      if (!menu.hidden && !box.contains(event.target)) close();
    });
    window.addEventListener("resize", function () { if (!menu.hidden) place(); });
    document.addEventListener("scroll", function (event) {
      if (!menu.hidden && !menu.contains(event.target)) place();
    }, true);
    // Whatever sets the value -- this menu, a draft restored, another script --
    // the button says it.
    select.addEventListener("change", repaint);
    repaint();
  }

  function scan(root) {
    (root || document).querySelectorAll("select[data-upward]").forEach(build);
  }

  document.addEventListener("DOMContentLoaded", function () { scan(document); });
  document.addEventListener("htmx:afterSwap", function (event) { scan(event.target); });
  if (document.readyState !== "loading") scan(document);
})();

/*
  The chosen tab, in view. A tab bar wider than a phone scrolls in its
  own box, and a page opened on a tab near its end -- Settings -> Scheduled from
  `/scheduled`, Library -> Reports from a notification -- showed a bar whose
  chosen tab was off its right edge, so nothing said where you were. Only the
  bar scrolls, never the page.
*/
(function () {
  function reveal() {
    document.querySelectorAll(".tabs__bar").forEach(function (bar) {
      var tab = bar.querySelector(".tabs__tab.is-active");
      if (!tab) {
        var checked = bar.querySelector("input:checked");
        tab = checked && bar.querySelector('label[for="' + checked.id + '"]');
      }
      if (!tab || bar.scrollWidth <= bar.clientWidth) return;
      var left = tab.getBoundingClientRect().left - bar.getBoundingClientRect().left + bar.scrollLeft;
      if (left < bar.scrollLeft || left + tab.offsetWidth > bar.scrollLeft + bar.clientWidth) {
        bar.scrollLeft = Math.max(0, left - (bar.clientWidth - tab.offsetWidth) / 2);
      }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", reveal);
  else reveal();
})();

/*
  The settings window.

  On a desktop, any link to /settings -- the reader's entry at the foot of the
  sidebar, the composer's "Your defaults per model", the agents tree's
  "Settings → Devices", the admin area's own entry -- opens
  Settings in a dialog over the page instead of navigating away from it, and
  closing it puts the reader back exactly where they were. It exists
  because Settings is visited mid-task: change a default, come back to the
  chat. A navigation threw away the chat's scroll position, the open panel and
  anything half-typed in the composer to do it.

  Delegated at the document, so every settings link there is or ever will be
  gets it without being marked. What it skips, and why:

    - a phone (<= 48rem, the breakpoint where the sidebar becomes a drawer):
      there is no room for a window over a page, and the full page is what a
      phone has always had;
    - a modified or non-primary click: a new tab is a new tab;
    - a link with a `target`, a `download`, or `data-settings-page` -- the
      last on the few links that mean "go to the page", like Cancel on a
      scheduled task's form or Done after a token was shown;
    - /settings itself: a window over the page it is a window onto is nothing.

  What is inside is a frame of `/settings?embed=1`: the very page, rendered
  without its shell (pages.py). That keeps every form, tab and `?saved=`
  redirect of Settings exactly as it is, rather than a second copy of Settings
  that would drift from the first. The frame is same-origin -- CSP says
  `frame-ancestors 'self'` and nothing sets X-Frame-Options -- so this file
  can read where it has got to:

    - a page that came back with a sidebar in it (a browser that sends no
      `Sec-Fetch-Dest`, after a redirect) is asked for again with `embed=1`;
    - a page that is not Settings at all belongs in the whole window, so the
      window goes there;
    - a `saved=` anywhere along the way means something changed -- a
      language, a default model, the reader's own instructions -- and the page
      underneath was rendered with the old value, so closing reloads it.

  The theme is the one thing applied live: app.js stores it in localStorage,
  and a `storage` event reaches this window when the frame writes it.

  Inside the frame (the body says `data-settings-embed`) the other half runs:
  a link that leaves Settings is sent to the whole window, and Escape closes
  the window, because a key pressed in a frame never reaches the dialog
  around it.
*/
(function () {
  "use strict";
  var t = window.lembasT || function (text) { return text; };
  var PHONE = "(max-width: 48rem)";
  var body = document.body;

  function settingsUrl(href) {
    var url;
    try { url = new URL(href, window.location.href); } catch (e) { return null; }
    if (url.origin !== window.location.origin || url.pathname !== "/settings") return null;
    return url;
  }

  /* --- In the frame ------------------------------------------------------ */
  if (body && body.hasAttribute("data-settings-embed")) {
    document.addEventListener("click", function (event) {
      var link = event.target.closest && event.target.closest("a[href]");
      if (!link || link.target || link.hasAttribute("download")) return;
      var href = link.getAttribute("href") || "";
      if (href.charAt(0) === "#") return;
      var url = settingsUrl(link.href);
      if (url) {
        /* Still Settings: stays in the frame, and asks for the slim page by
           name rather than leaning on the header. */
        url.searchParams.set("embed", "1");
        link.href = url.href;
        return;
      }
      /* Anywhere else -- a note, the admin area, a scheduled task -- is a
         place, and a place belongs in the whole window. */
      link.target = "_top";
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      /* Something of the frame's own is open and Escape is its: a confirm,
         a picker. */
      if (document.querySelector("dialog[open], [data-picker-menu]:not([hidden])")) return;
      try {
        if (window.parent !== window && window.parent.lembas && window.parent.lembas.closeSettings) {
          window.parent.lembas.closeSettings();
        }
      } catch (e) { /* not ours to reach */ }
    });
    return;
  }

  /* --- Around it --------------------------------------------------------- */
  var dialog = null;
  var frame = null;
  var pageLink = null;
  var changed = false;

  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#i-" + name);
    svg.appendChild(use);
    return svg;
  }

  function pageHref(href) {
    var url = settingsUrl(href);
    if (!url) return "/settings";
    url.searchParams.delete("embed");
    return url.pathname + url.search + url.hash;
  }

  function build() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.className = "dialog settings-window";
    dialog.setAttribute("aria-labelledby", "settings-window-title");

    var header = document.createElement("header");
    header.className = "settings-window__header";
    var title = document.createElement("h2");
    title.className = "settings-window__title";
    title.id = "settings-window-title";
    title.textContent = t("Your settings");
    header.appendChild(title);

    var actions = document.createElement("div");
    actions.className = "settings-window__actions";
    /* The page this window is a view of, for somebody who wants it as one:
       kept pointing at the tab the frame is on. */
    pageLink = document.createElement("a");
    pageLink.className = "btn btn--icon";
    pageLink.href = "/settings";
    pageLink.setAttribute("data-settings-page", "");
    pageLink.setAttribute("aria-label", t("Open as a page"));
    pageLink.title = t("Open as a page");
    pageLink.appendChild(icon("expand"));
    actions.appendChild(pageLink);

    var close = document.createElement("button");
    close.type = "button";
    close.className = "btn btn--icon";
    close.setAttribute("aria-label", t("Close"));
    close.title = t("Close");
    close.appendChild(icon("x"));
    close.addEventListener("click", function () { dialog.close(); });
    actions.appendChild(close);
    header.appendChild(actions);
    dialog.appendChild(header);

    frame = document.createElement("iframe");
    frame.className = "settings-window__frame";
    frame.title = t("Your settings");
    frame.addEventListener("load", arrived);
    dialog.appendChild(frame);

    /* The backdrop: the dialog has no padding and its rows fill it, so a
       click that lands on the dialog itself landed outside the window. */
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener("close", closed);
    body.appendChild(dialog);
  }

  function arrived() {
    var where;
    var inner;
    try {
      where = frame.contentWindow.location;
      inner = frame.contentDocument;
    } catch (e) { return; }
    if (!where || where.href === "about:blank") return;
    if (where.pathname !== "/settings") {
      /* Left Settings inside the frame. Whatever it is, it is a page of its
         own and the reader is now on it -- unless it is an answer from
         /api/…, a form's error the frame is showing: that address is a POST's,
         and loading it in the window would only say "method not allowed". */
      if (where.pathname.indexOf("/api/") !== 0) window.location.assign(where.href);
      return;
    }
    if (/[?&]saved=/.test(where.search)) changed = true;
    if (inner && inner.body && !inner.body.hasAttribute("data-settings-embed")) {
      var url = new URL(where.href);
      url.searchParams.set("embed", "1");
      where.replace(url.href);
      return;
    }
    pageLink.href = pageHref(where.href);
  }

  function closed() {
    /* Unloaded, so a frame left open on a long panel costs nothing and the
       next opening starts clean rather than showing the last one for a
       moment. */
    frame.src = "about:blank";
    if (changed) {
      changed = false;
      window.location.reload();
    }
  }

  function openSettings(href) {
    var url = settingsUrl(href || "/settings");
    if (!url || window.matchMedia(PHONE).matches || window.location.pathname === "/settings") {
      return false;
    }
    build();
    changed = false;
    url.searchParams.set("embed", "1");
    frame.src = url.pathname + url.search + url.hash;
    pageLink.href = pageHref(url.href);
    if (!dialog.open) dialog.showModal();
    return true;
  }

  function closeSettings() {
    if (dialog && dialog.open) dialog.close();
  }

  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var link = event.target.closest && event.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("download")) return;
    if (link.hasAttribute("data-settings-page")) return;
    if (openSettings(link.href)) event.preventDefault();
  });

  /* The theme, live. The frame's app.js stored it; this is the copy of app.js
     that paints this window. */
  window.addEventListener("storage", function (event) {
    if (event.key !== "lembas-theme" || !event.newValue) return;
    if (!dialog || !dialog.open) return;
    if (window.lembas && window.lembas.applyTheme) window.lembas.applyTheme(event.newValue);
  });

  window.lembas = window.lembas || {};
  window.lembas.openSettings = openSettings;
  window.lembas.closeSettings = closeSettings;
})();
