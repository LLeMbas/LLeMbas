/*
  Dictation and read-aloud.

  Both halves are progressive: without this file the composer and the message
  bubbles still work, they simply have two buttons that do nothing. Neither
  feature's markup is rendered at all unless an administrator has configured an
  endpoint for it, so that state is rare rather than normal.
*/
(function () {
  "use strict";
  /* The words, in the reader's language: see `lembasT` in base.html. */
  var t = window.lembasT || function (text) { return text; };

  function notify(message, kind) {
    if (window.lembas && window.lembas.notify) {
      window.lembas.notify(message, { kind: kind || "info" });
    }
  }

  /* --- Dictation ---------------------------------------------------------
     MediaRecorder writes whatever container the browser prefers -- webm/opus
     almost everywhere, mp4 on Safari. The file is passed upstream with the
     type the browser reported rather than being converted here: whisper.cpp
     and friends decode through ffmpeg and take all of them, and converting in
     the browser would mean shipping an encoder. */
  var recorder = null;
  var chunks = [];
  var micButton = null;

  function setMicState(button, state) {
    if (!button) return;
    button.dataset.micState = state;
    button.disabled = state === "working";
    button.setAttribute(
      "aria-label",
      state === "recording" ? t("Stop recording") : t("Dictate a message")
    );
    button.title = button.getAttribute("aria-label");
  }

  function composerInput() {
    return document.querySelector("[data-composer-input]");
  }

  function insertTranscript(text) {
    var input = composerInput();
    if (!input || !text) return;
    // Appended rather than replacing: dictation is usually finishing a thought
    // that was already half typed.
    var existing = input.value.trim();
    input.value = existing ? existing + " " + text : text;
    if (window.lembas && window.lembas.autosize) window.lembas.autosize(input);
    if (window.lembas && window.lembas.paintComposer) window.lembas.paintComposer();
    input.focus();
    input.selectionStart = input.selectionEnd = input.value.length;
  }

  function upload(blob, button) {
    var body = new FormData();
    // The extension only has to be something the server can name the part;
    // the endpoint sniffs the container itself.
    var extension = (blob.type.indexOf("mp4") !== -1) ? "mp4" : "webm";
    body.append("file", blob, "dictation." + extension);

    setMicState(button, "working");
    fetch("/api/audio/transcribe", {
      method: "POST",
      body: body,
      credentials: "same-origin",
    })
      .then(function (response) {
        if (!response.ok) {
          return response.json()
            .catch(function () { return {}; })
            .then(function (payload) {
              throw new Error(payload.detail || t("Transcription failed."));
            });
        }
        return response.text();
      })
      .then(function (text) {
        setMicState(button, "idle");
        if (!text.trim()) {
          notify(t("Nothing was heard in that recording."), "info");
          return;
        }
        insertTranscript(text.trim());
      })
      .catch(function (error) {
        setMicState(button, "idle");
        notify(error.message || t("Transcription failed."), "error");
      });
  }

  function startRecording(button) {
    /* getUserMedia is undefined on plain http, which a self-hosted install on
       a LAN address often is. Saying so beats a button that silently does
       nothing -- the fix is not something the page can apply for them. */
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia ||
        typeof MediaRecorder === "undefined") {
      notify(
        t("The microphone needs HTTPS or localhost. This page is served over plain HTTP, so the browser will not grant it."),
        "error"
      );
      return;
    }

    /* Out of `idle` **before** awaiting permission, or the button stays
       clickable for as long as the browser's prompt is up -- and the dispatcher
       below starts a recording on every click in that window. Each one opened
       its own stream and its own MediaRecorder, only the last of which was kept
       in `recorder`, so stopping released one and left the rest live: the
       browser's recording indicator stayed on until the tab was closed.

       `working` already exists and is already styled, so this is the state the
       button was missing rather than a new one. */
    setMicState(button, "working");

    navigator.mediaDevices.getUserMedia({ audio: true }).then(function (stream) {
      chunks = [];
      recorder = new MediaRecorder(stream);
      micButton = button;

      recorder.addEventListener("dataavailable", function (event) {
        if (event.data && event.data.size) chunks.push(event.data);
      });
      recorder.addEventListener("stop", function () {
        // Release the microphone immediately: leaving the track live keeps the
        // browser's recording indicator on long after anyone is talking.
        stream.getTracks().forEach(function (track) { track.stop(); });
        var blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
        recorder = null;
        if (blob.size) upload(blob, button); else setMicState(button, "idle");
      });

      recorder.start();
      setMicState(button, "recording");
    }).catch(function (error) {
      // Back to idle, or a refused permission leaves a button nothing can press
      // again -- including the reader who has just gone and allowed it.
      setMicState(button, "idle");
      notify(microphoneError(error), "error");
    });
  }

  /* Why the microphone did not open, in words that say what to do about it.
     Every rejection used to read "Permission may be blocked", which sent people
     to the browser's site settings when the real answer was that another
     application held the device, or that the machine had no microphone at
     all. The names are the DOMException names `getUserMedia` rejects with;
     anything else is passed through by name, because "something went wrong"
     is not a thing anybody can search for. */
  function microphoneError(error) {
    var name = (error && error.name) || "";
    if (name === "NotAllowedError" || name === "SecurityError") {
      return t("Microphone access was refused. Allow it for this site in the browser's address bar or site settings, then try again.");
    }
    if (name === "NotFoundError" || name === "OverconstrainedError") {
      return t("No microphone was found. Connect one, or check that the system can see it, then try again.");
    }
    if (name === "NotReadableError") {
      return t("The microphone is in use by another application, or the system would not let the browser open it.");
    }
    return t("The microphone could not be opened (%(error)s).", { error: name || "Error" });
  }

  function stopRecording() {
    if (recorder && recorder.state !== "inactive") recorder.stop();
  }

  /* --- Reading a reply aloud ---------------------------------------------
     One <audio> element for the whole page. Two replies talking over each
     other is never what was wanted, and a shared element makes that
     impossible rather than merely unlikely. */
  var player = null;
  var speaking = null;

  function audioPlayer() {
    if (!player) {
      player = new Audio();
      player.addEventListener("ended", function () { markSpeaking(null); });
      player.addEventListener("error", function () {
        if (speaking) notify(t("That reply could not be read out."), "error");
        markSpeaking(null);
      });
    }
    return player;
  }

  function markSpeaking(button) {
    document.querySelectorAll("[data-speak]").forEach(function (el) {
      el.classList.toggle("is-speaking", el === button);
    });
    speaking = button;
  }

  function speak(button) {
    var element = audioPlayer();
    if (speaking === button) {
      element.pause();
      markSpeaking(null);
      return;
    }
    element.pause();
    element.src = button.dataset.speak;
    markSpeaking(button);
    element.play().catch(function () {
      /* Autoplay policies reject a play() the reader did not ask for. That is
         the browser working as intended, so it is not reported as an error. */
      markSpeaking(null);
    });
  }

  /* --- Wiring ------------------------------------------------------------ */
  document.addEventListener("click", function (event) {
    var mic = event.target.closest("[data-mic]");
    if (mic) {
      event.preventDefault();
      if (mic.dataset.micState === "recording") stopRecording();
      else if (mic.dataset.micState === "idle") startRecording(mic);
      return;
    }

    var speaker = event.target.closest("[data-speak]");
    if (speaker) {
      event.preventDefault();
      speak(speaker);
    }
  });

  /* A reply that has just finished streaming carries data-speak-auto, set only
     on that one frame. Any swap can bring it in, so this watches them all and
     clears the attribute after acting -- a later swap of the same bubble must
     not start it again. */
  function playArrivals() {
    document.querySelectorAll("[data-speak-auto]").forEach(function (button) {
      button.removeAttribute("data-speak-auto");
      speak(button);
    });
  }

  document.addEventListener("DOMContentLoaded", playArrivals);
  /* Not after a stream frame: `data-speak-auto` arrives on the finished
     bubble, which the `done` frame swaps in -- and that swap is not a frame in
     this sense (`isStreamFrame` in app.js), so it is still heard here. The
     dozen frames a second before it each searched the whole page for nothing. */
  if (document.body) {
    document.body.addEventListener("htmx:afterSettle", function (event) {
      var lembas = window.lembas;
      if (lembas && lembas.isStreamFrame && lembas.isStreamFrame(event)) return;
      playArrivals();
    });
  }
})();
