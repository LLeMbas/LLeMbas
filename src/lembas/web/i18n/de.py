"""Deutsch (German).

Keyed by the English source text, as `web/i18n/__init__.py` explains: a missing
entry renders the English. Made in 2.1.0 from the Slovak catalogue's set of
strings; `tests/test_translations.py` keeps it honest (no orphans, placeholders
kept, nothing empty).
"""

from __future__ import annotations

# What this language calls itself, as the language menus show it.
NAME = "Deutsch"

# Dates: the month in the form a date with a day number wants, and the days, Monday first.
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
DAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

MESSAGES: dict[str, str] = {
    "Currency": "Währung",
    "Chat models": "Chat-Modelle",
    "Embedding": "Embeddings",
    "Image review": "Bildprüfung",
    "connections": "Verbindungen",
    "Added to every reply, whatever its length.": "Wird jeder Antwort hinzugerechnet, unabhängig von ihrer Länge.",
    "Cached input": "Eingabe aus dem Cache",
    "Input": "Eingabe",
    "Output": "Ausgabe",
    "Per million prompt tokens.": "Pro Million Prompt-Tokens.",
    "Per million the server had cached; the input price when empty.": "Pro Million Tokens, die der Server im Cache hatte; leer = Eingabepreis.",
    "Per million tokens of thinking; the output price when empty.": "Pro Million Tokens Nachdenken; leer = Ausgabepreis.",
    "Per million tokens written.": "Pro Million geschriebener Tokens.",
    "Per reply": "Pro Antwort",
    "Backed up and requested. The service will restart in a moment.": "Gesichert und angefordert. Der Dienst startet gleich neu.",
    "It will be installed as soon as nothing is running and nobody is active.": "Es wird installiert, sobald nichts mehr läuft und niemand aktiv ist.",
    "Approved. The device is signing in now; you can close this page.": "Bestätigt. Das Gerät meldet sich gerade an; Sie können diese Seite schließen.",
    "Denied. The device was not signed in.": "Abgelehnt. Das Gerät wurde nicht angemeldet.",
    "The device did not answer.": "Das Gerät hat nicht geantwortet.",
    "This device is not linked right now.": "Dieses Gerät ist gerade nicht verbunden.",
    "You may not sign devices in to this instance.": "Sie dürfen keine Geräte bei dieser Instanz anmelden.",
    "%H:%M on %A %-d %B": "%H:%M am %A, %-d. %B",
    "Everything models remembered about you has been forgotten.": "Alles, was sich Modelle über Sie gemerkt hatten, ist vergessen.",
    "Nothing was deleted.": "Es wurde nichts gelöscht.",
    "There is no such reset.": "Dieses Zurücksetzen gibt es nicht.",
    "There was nothing to delete.": "Es gab nichts zu löschen.",
    "Type %(word)s to confirm a full reset. Nothing was deleted.": "Geben Sie %(word)s ein, um das vollständige Zurücksetzen zu bestätigen. Es wurde nichts gelöscht.",
    "You have started over. Your account and settings are as they were.": "Sie haben neu angefangen. Ihr Konto und Ihre Einstellungen sind unverändert.",
    "Your chats and folders have been deleted.": "Ihre Chats und Ordner wurden gelöscht.",
    "Your password is not correct, so nothing was deleted.": "Ihr Passwort ist nicht korrekt, daher wurde nichts gelöscht.",
    "Choose at least one thing the token may do.": "Wählen Sie mindestens eine Sache, die das Token darf.",
    "Revoked.": "Widerrufen.",
    "Revoked. Anything using it is signed out now.": "Widerrufen. Alles, was es verwendet hat, ist jetzt abgemeldet.",
    "You may not make API tokens.": "Sie dürfen keine API-Tokens erstellen.",
    "You may not read the audit log.": "Sie dürfen das Audit-Protokoll nicht lesen.",
    "Accept work from this instance as a linked device": "Arbeit von dieser Instanz als verknüpftes Gerät annehmen",
    "Read every account and its usage": "Alle Konten und ihren Verbrauch lesen",
    "Read the audit log": "Das Audit-Protokoll lesen",
    "Use your models, and spend your usage": "Ihre Modelle verwenden und Ihr Kontingent verbrauchen",
    "%(n)s hours after the last message": "%(n)s Stunden nach der letzten Nachricht",
    "%(n)s minutes after the last message": "%(n)s Minuten nach der letzten Nachricht",
    "1 hour after the last message": "1 Stunde nach der letzten Nachricht",
    "when you leave it": "wenn Sie ihn verlassen",
    "API key": "API-Schlüssel",
    "Base URL": "Basis-URL",
    "Data group": "Datengruppe",
    "Extra headers": "Zusätzliche Header",
    "GET": "GET",
    "HTTP-Referer: https://example.org": "HTTP-Referer: https://example.org",
    "Holds one model at a time": "Hält jeweils nur ein Modell",
    "How to ask it to unload": "Wie es zum Entladen aufgefordert wird",
    "Name": "Name",
    "No key set": "Kein Schlüssel gesetzt",
    "No unload call": "Kein Entlade-Aufruf",
    "POST": "POST",
    "Save": "Speichern",
    'Tick this for llama-swap in front of one GPU, or anything else that unloads one model to serve another. A model here may then be its own subagent, but never send a subagent to another model on this connection: loading it would unload the model whose reply is waiting.': 'Aktivieren Sie dies für llama-swap vor einer einzelnen GPU oder alles andere, das ein Modell entlädt, um ein anderes zu bedienen. Ein Modell hier darf dann sein eigener Subagent sein, schickt aber nie einen Subagenten an ein anderes Modell dieser Verbindung: Dessen Laden würde das Modell entladen, dessen Antwort gerade wartet.',
    "Unload URL": "Entlade-URL",
    "disabled": "deaktiviert",
    "Administration": "Administration",
    "Agents": "Agenten",
    "Audio": "Audio",
    "Audit log": "Audit-Protokoll",
    "Back to chats": "Zurück zu den Chats",
    "Connections": "Verbindungen",
    "Data groups": "Datengruppen",
    "Extraction": "Extraktion",
    "General": "Allgemein",
    "Image generation": "Bildgenerierung",
    "MCP servers": "MCP-Server",
    "Models": "Modelle",
    "Prompts": "Prompts",
    "Scheduling": "Zeitplanung",
    "Suggestions": "Vorschläge",
    "Switch theme": "Design wechseln",
    "Tools": "Werkzeuge",
    "Updated to %(version)s.": "Aktualisiert auf %(version)s.",
    "Updates": "Updates",
    "Users": "Benutzer",
    "Web search": "Websuche",
    "What changed": "Was sich geändert hat",
    "private network": "privates Netzwerk",
    "restricted": "eingeschränkt",
    "Use default": "Standard verwenden",
    "edited": "bearbeitet",
    "with tools": "mit Werkzeugen",
    "Chat title request": "Anfrage für den Chat-Titel",
    "Empty, so no model is asked to name a chat. Chats are named from the first thing said in them.": "Leer, daher wird kein Modell gebeten, einen Chat zu benennen. Chats werden nach dem ersten Satz benannt, der in ihnen fällt.",
    "Sent on its own after the first reply, not as part of any conversation.": "Wird nach der ersten Antwort separat gesendet, nicht als Teil eines Gesprächs.",
    "This is what the model would read back:": "Das würde das Modell zurücklesen:",
    "A backstop, as it is above. The clock and the token ceiling are what normally end one.": "Eine Sicherung, wie oben. Normalerweise beenden die Uhr und die Token-Obergrenze einen Lauf.",
    "Allow agent chats": "Agent-Chats erlauben",
    "Allow the terminal panel": "Das Terminal-Panel erlauben",
    "Ask another model": "Ein anderes Modell fragen",
    "Asking you things": "Fragen an Sie",
    "Auto": "Automatisch",
    "Close a shell after": "Shell schließen nach",
    'Delegate to a subagent': 'An einen Subagenten delegieren',
    "Each is a whole generation against the same endpoint the reply that asked for it is waiting on. Past this a model is told to do the work itself rather than made to wait.": "Jeder ist eine vollständige Generierung gegen denselben Endpunkt, auf den die anfragende Antwort wartet. Darüber hinaus wird ein Modell angewiesen, die Arbeit selbst zu erledigen, statt warten zu müssen.",
    "Fanning out across a handful of independent questions is what this is for. A reply that wants twenty has misread the tool. Questions put to other models count against this same number, so one reply cannot spend the allowance twice.": "Dafür ist es gedacht: sich auf eine Handvoll unabhängiger Fragen aufzufächern. Eine Antwort, die zwanzig will, hat das Werkzeug missverstanden. Fragen an andere Modelle zählen zu derselben Zahl, damit eine Antwort das Kontingent nicht doppelt verbrauchen kann.",
    'Subagents': 'Subagenten',
    "How long a question waits": "Wie lange eine Frage wartet",
    'In tokens, across every round. A subagent answers one question, so this should run out well before the reply that asked does. Zero means no ceiling.': 'In Tokens, über alle Runden hinweg. Ein Subagent beantwortet eine Frage, daher sollte dies deutlich vor der fragenden Antwort aufgebraucht sein. Null bedeutet keine Obergrenze.',
    "Keep a subagent's own chat afterwards": 'Den eigenen Chat eines Subagenten danach behalten',
    "Let a model delegate": "Ein Modell delegieren lassen",
    "Let people write their own answer": "Eigene Antworten zulassen",
    'Longest a subagent may take': 'Längste Laufzeit eines Subagenten',
    'Most a subagent may write': 'Höchstens, was ein Subagent schreiben darf',
    'Most subagents one reply may send': 'Höchstzahl an Subagenten pro Antwort',
    "Most rounds of tool calls": "Höchstzahl an Runden von Werkzeugaufrufen",
    "Most shells at once": "Höchstzahl gleichzeitiger Shells",
    "Most shells per person": "Höchstzahl an Shells pro Person",
    "Off means it is deleted once its answer has been handed over, which is what keeps this cheap to use. Turn it on to work out why one came back with something odd. Kept chats are temporary either way and are swept a day later, and neither appears in anybody's sidebar.": "Aus bedeutet, dass er gelöscht wird, sobald seine Antwort übergeben ist — genau das hält die Nutzung günstig. Schalten Sie es ein, um herauszufinden, warum einer mit etwas Seltsamem zurückkam. Behaltene Chats sind ohnehin temporär und werden einen Tag später aufgeräumt, und keiner erscheint in irgendjemandes Seitenleiste.",
    "Off, nobody can start one, whatever permissions they hold.": "Aus: Niemand kann eines starten, gleich welche Berechtigungen er hat.",
    "One per chat. Each keeps a shell open on the linked device, fed over its link.": "Eines pro Chat. Jedes hält eine Shell auf dem verknüpften Gerät offen, die über dessen Verbindung läuft.",
    "Open a terminal": "Ein Terminal öffnen",
    "Run commands": "Befehle ausführen",
    "Running at once, instance-wide": "Gleichzeitig laufend, instanzweit",
    "Save changes": "Änderungen speichern",
    "Saved.": "Gespeichert.",
    "Seconds. After this the reply carries on without an answer and says so. At least a minute, whatever is typed here.": "Sekunden. Danach fährt die Antwort ohne Antwort fort und sagt das. Mindestens eine Minute, egal was hier steht.",
    "Set per chat and switchable at any time. This is what each one means; the two lists below adjust them.": "Pro Chat festgelegt und jederzeit umschaltbar. Das bedeutet jeder Modus; die beiden Listen unten passen sie an.",
    "Switch": "Aktivierung",
    "The modes": "Die Modi",
    "The terminal": "Das Terminal",
    "When a model asks a question it can offer answers to pick from, and by default a box to write something else. Turn this off if you would rather nobody typed free text into a prompt a model composed.": "Wenn ein Modell eine Frage stellt, kann es Antworten zur Auswahl anbieten und standardmäßig ein Feld, um etwas anderes zu schreiben. Schalten Sie dies aus, wenn niemand freien Text in eine Eingabeaufforderung tippen soll, die ein Modell verfasst hat.",
    "and": "und",
    "stopped": "angehalten",
    "Default voice": "Standardstimme",
    "Encrypted at rest. Clear the field to remove it.": "Verschlüsselt gespeichert. Leeren Sie das Feld, um ihn zu entfernen.",
    "Language": "Sprache",
    "Model": "Modell",
    "Save settings": "Einstellungen speichern",
    "Speed": "Geschwindigkeit",
    "detect": "erkennen",
    "off": "aus",
    "on": "an",
    "%(total)s events · page %(page)s of %(pages)s": "%(total)s Ereignisse · Seite %(page)s von %(pages)s",
    "A prefix: auth finds every sign-in.": "Ein Präfix: auth findet jede Anmeldung.",
    "Action": "Aktion",
    "Before": "Vor",
    "Download CSV": "CSV herunterladen",
    "Filter": "Filtern",
    "From": "Ab",
    "Kept for %(days)s days.": "Wird %(days)s Tage aufbewahrt.",
    "Kept for ever.": "Wird für immer aufbewahrt.",
    "Newer": "Neuer",
    "Nothing matches.": "Nichts gefunden.",
    "Older": "Älter",
    "Pages": "Seiten",
    "Part of an email, or an account id.": "Teil einer E-Mail-Adresse oder eine Konto-ID.",
    "Per page": "Pro Seite",
    "Show": "Anzeigen",
    "What": "Was",
    "When": "Wann",
    "Who": "Wer",
    "Who did what, when and from where: sign-ins and failed ones, tokens and devices, every change an administrator makes, people starting over, updates. It names things and never quotes them — no chat, note or password is ever in it.": "Wer hat was getan, wann und von wo aus: Anmeldungen und fehlgeschlagene Versuche, Tokens und Geräte, jede Änderung eines Administrators, Neuanfänge, Updates. Es benennt Dinge und zitiert sie nie — kein Chat, keine Notiz und kein Passwort steht je darin.",
    "token": "Token",
    "Encrypted before it is stored, and never sent back to the browser. Leave empty for endpoints that need no key.": "Wird vor dem Speichern verschlüsselt und nie an den Browser zurückgesendet. Leer lassen für Endpunkte, die keinen Schlüssel brauchen.",
    "Local LM Studio": "Lokales LM Studio",
    "http://localhost:1234/v1": "http://localhost:1234/v1",
    "sk-…": "sk-…",
    "Account": "Konto",
    "Delete": "Löschen",
    "None": "Keiner",
    "Plan": "Tarif",
    "A connection leaves the default group by being put into another one.": "Eine Verbindung verlässt die Standardgruppe, indem sie einer anderen zugeordnet wird.",
    "A personal group of %(who)s.": "Eine persönliche Gruppe von %(who)s.",
    "All data groups": "Alle Datengruppen",
    "Changing it leaves this group's search keyword-only until the index is rebuilt on the Extraction page.": "Nach einer Änderung sucht diese Gruppe nur nach Stichwörtern, bis der Index auf der Seite Extraktion neu aufgebaut ist.",
    "Connections in this group": "Verbindungen in dieser Gruppe",
    "Delete the data group “%(name)s”?": "Die Datengruppe „%(name)s“ löschen?",
    "Embedding model": "Embedding-Modell",
    "Every provider this group's data is sent to, as the instance has it set. A person holding “Manage their own data groups” may have arranged their own differently. Speech to text and text to speech are not grouped: audio is sent and not kept.": "Jeder Anbieter, an den die Daten dieser Gruppe gesendet werden, so wie die Instanz es eingestellt hat. Wer die Berechtigung „Eigene Datengruppen verwalten“ hat, kann seine eigenen anders eingerichtet haben. Sprache-zu-Text und Text-zu-Sprache werden nicht gruppiert: Audio wird gesendet und nicht gespeichert.",
    "Holds": "Enthält",
    "Image reviewer": "Bildprüfer",
    "Nothing: no connection is in this group, and no service reads it.": "Nirgendwohin: Keine Verbindung ist in dieser Gruppe, und kein Dienst liest sie.",
    "Only models that can see images are listed.": "Es werden nur Modelle aufgeführt, die Bilder sehen können.",
    "Services that read this group": "Dienste, die diese Gruppe lesen",
    "Settings saved.": "Einstellungen gespeichert.",
    "The embedder is sent the full text of every document, note, skill and report it indexes, and the reviewer is sent every generated picture with its prompt. A group can name its own, so its data does not have to go to the instance's.": "Das Embedding-Modell erhält den vollständigen Text jedes Dokuments, jeder Notiz, Fertigkeit und jedes Berichts, den es indexiert, und der Prüfer erhält jedes generierte Bild samt seinem Prompt. Eine Gruppe kann eigene festlegen, damit ihre Daten nicht an die der Instanz gehen müssen.",
    "The instance's choice": "Die Wahl der Instanz",
    "Their models read this group's data and nothing else. A connection taken out of a group goes back to the default one. Moving a connection moves no data: it starts reading the other group's.": "Ihre Modelle lesen die Daten dieser Gruppe und nichts anderes. Eine Verbindung, die aus einer Gruppe genommen wird, kehrt in die Standardgruppe zurück. Das Verschieben einer Verbindung verschiebt keine Daten: Sie liest dann die Daten der anderen Gruppe.",
    "There are no connections yet.": "Es gibt noch keine Verbindungen.",
    "What it is for": "Wofür sie ist",
    "Where this group's data goes": "Wohin die Daten dieser Gruppe gehen",
    "its connection is in %(group)s": "seine Verbindung ist in %(group)s",
    "%(n)s chats": "Chats: %(n)s",
    "%(n)s connections": "Verbindungen: %(n)s",
    "A new group holds nothing and reads nothing until a connection is put into it. Nothing moves on its own.": "Eine neue Gruppe enthält nichts und liest nichts, bis ihr eine Verbindung zugeordnet wird. Nichts verschiebt sich von selbst.",
    "Create data group": "Datengruppe erstellen",
    "Data group deleted.": "Datengruppe gelöscht.",
    "Instance groups": "Gruppen der Instanz",
    "New data group name": "Name der neuen Datengruppe",
    "Personal groups": "Persönliche Gruppen",
    "default": "Standard",
    "own embedder": "eigenes Embedding",
    "own reviewer": "eigener Prüfer",
    "Also treat as text": "Auch als Text behandeln",
    "Characters. Roughly four to a token, so 120,000 is about 30,000 tokens — already most of a small context window. The rest is cut and the model is told so.": "Zeichen. Etwa vier pro Token, also sind 120.000 rund 30.000 Tokens — schon der größte Teil eines kleinen Kontextfensters. Der Rest wird abgeschnitten, und das Modell erfährt es.",
    "Every number here is a trade, and a large one usually breaks a request rather than being slow — the text of a whole book does not fit in a context window, and a model handed it fails the request outright rather than reading the first half.": "Jede Zahl hier ist ein Kompromiss, und ein großer Wert lässt eine Anfrage meist scheitern, statt sie nur zu verlangsamen — der Text eines ganzen Buches passt in kein Kontextfenster, und ein Modell, dem man ihn gibt, lehnt die Anfrage rundweg ab, statt die erste Hälfte zu lesen.",
    "Extraction is slow and happens once, at upload. Beyond this the file is still stored; only its text stops.": "Die Extraktion ist langsam und geschieht einmal, beim Hochladen. Darüber hinaus wird die Datei trotzdem gespeichert; nur ihr Text endet.",
    "Hours. A file chosen in the composer and never sent. Swept at startup.": "Stunden. Eine Datei, die im Eingabefeld gewählt und nie gesendet wurde. Wird beim Start aufgeräumt.",
    "JPEG quality": "JPEG-Qualität",
    "Keep abandoned uploads for": "Verwaiste Uploads aufbewahren für",
    "Largest upload": "Größter Upload",
    "Longest image edge": "Längste Bildkante",
    "Lower this if the endpoint refuses large requests; raise it if a rebuild is slow and the far side has room.": "Senken Sie dies, wenn der Endpunkt große Anfragen ablehnt; erhöhen Sie es, wenn ein Neuaufbau langsam ist und die Gegenseite Kapazität hat.",
    "Megabytes, before anything is done to it.": "Megabyte, bevor irgendetwas mit ihr geschieht.",
    "None — keyword search only": "Keines — nur Stichwortsuche",
    "Off, a scanned PDF is stored with an explanation saying why it contributes nothing — there is no OCR here. That is usually what somebody wants: the file is still attached and still downloadable.": "Aus: Ein gescanntes PDF wird mit einer Erklärung gespeichert, warum es nichts beiträgt — hier gibt es keine Texterkennung (OCR). Das ist meist genau das, was man will: Die Datei ist trotzdem angehängt und herunterladbar.",
    "Pages read from a PDF": "Aus einem PDF gelesene Seiten",
    "Pieces per request": "Stücke pro Anfrage",
    "Pixels. Images are re-encoded before they are sent, because a phone photo is several megabytes of base64.": "Pixel. Bilder werden vor dem Senden neu kodiert, weil ein Handyfoto mehrere Megabyte Base64 sind.",
    "Refuse a PDF whose text cannot be read": "Ein PDF ablehnen, dessen Text nicht gelesen werden kann",
    "Save extraction": "Extraktion speichern",
    "Save search": "Suche speichern",
    "Searching by meaning": "Suche nach Bedeutung",
    "Text kept per file": "Pro Datei aufbewahrter Text",
    "What a file may cost": "Was eine Datei kosten darf",
    "What happens to a file between the upload and the model, and how anything is found again afterwards. The two are the same pipeline: what is extracted decides what there is to search.": "Was mit einer Datei zwischen dem Hochladen und dem Modell geschieht und wie danach irgendetwas wiedergefunden wird. Beides ist dieselbe Pipeline: Was extrahiert wird, entscheidet, worin gesucht werden kann.",
    "embeddings": "embeddings",
    "A long conversation eventually fills the model's context. When it gets close, the earlier turns are summarised and the summary is sent in their place. The messages themselves are kept and stay readable in the transcript — they simply stop being sent.": "Ein langes Gespräch füllt irgendwann den Kontext des Modells. Wird es knapp, werden die früheren Runden zusammengefasst, und die Zusammenfassung wird an ihrer Stelle gesendet. Die Nachrichten selbst bleiben erhalten und im Verlauf lesbar — sie werden nur nicht mehr gesendet.",
    "A model ends its own turn the moment it stops asking for tools — that is it saying it has what it needs, and nothing here overrides it. This is a ceiling for the case where it never says so.": "Ein Modell beendet seinen Zug, sobald es keine Werkzeuge mehr anfordert — damit sagt es, dass es hat, was es braucht, und nichts hier setzt sich darüber hinweg. Dies ist eine Obergrenze für den Fall, dass es das nie sagt.",
    "Applied to every chat that does not have a prompt of its own. A model's prompt overrides this, and a chat's prompt overrides both — most specific wins outright rather than the three being stacked together.": "Gilt für jeden Chat ohne eigenen Prompt. Der Prompt eines Modells hat Vorrang davor, und der Prompt eines Chats hat Vorrang vor beiden — der spezifischste gewinnt vollständig, statt dass alle drei aufeinandergestapelt werden.",
    "Compact at": "Zusammenfassen bei",
    "Compaction": "Zusammenfassung",
    "Default system prompt": "Standard-Systemprompt",
    "Delete a temporary chat after": "Temporären Chat löschen nach",
    "Each person then chooses for themselves, under Settings → Account: how they want to be helped, and a personality (concise, pragmatic, optimistic, funny, formal, Socratic, or their own words). It applies to every model, after the system prompt. Nothing is sent for anybody who has not chosen.": "Jede Person wählt dann selbst unter Einstellungen → Konto, wie ihr geholfen werden soll, und eine Persönlichkeit (knapp, pragmatisch, optimistisch, witzig, förmlich, sokratisch oder in eigenen Worten). Das gilt für jedes Modell, nach dem Systemprompt. Für niemanden, der nichts gewählt hat, wird etwas gesendet.",
    "Instance name": "Name der Instanz",
    "Instance-wide settings. These are stored in the database and take effect immediately — no restart, and they survive one.": "Einstellungen für die ganze Instanz. Sie werden in der Datenbank gespeichert und wirken sofort — ohne Neustart, und sie überstehen einen.",
    "Interface language": "Sprache der Oberfläche",
    "Leave empty to send no system prompt at all.": "Leer lassen, um gar keinen Systemprompt zu senden.",
    "Let people set custom instructions and a personality": "Personen eigene Anweisungen und eine Persönlichkeit festlegen lassen",
    "Minutes after its last message, up to 1440 (a day). 0 deletes it a minute after the person leaves it; one left open in a tab that was closed without saying so goes ten minutes after its last message.": "Minuten nach seiner letzten Nachricht, höchstens 1440 (ein Tag). 0 löscht ihn eine Minute, nachdem die Person ihn verlassen hat; einer, dessen Tab ohne Abmeldung geschlossen wurde, verschwindet zehn Minuten nach seiner letzten Nachricht.",
    "Personalization": "Personalisierung",
    "System prompt": "Systemprompt",
    "Temporary chats": "Temporäre Chats",
    "Tool calls in an ordinary chat": "Werkzeugaufrufe in einem gewöhnlichen Chat",
    "What a device asked to sign in here is told it is signing in to, and what the LLeMbas CLI names this connection after. Empty is LLeMbas.": "Was einem Gerät, das hier angemeldet werden soll, als Ziel der Anmeldung genannt wird, und wonach die LLeMbas CLI diese Verbindung benennt. Leer bedeutet LLeMbas.",
    "What the interface is in for anybody who has not chosen for themselves. Everyone can pick their own under Appearance in their settings, and that choice wins here.": "In welcher Sprache die Oberfläche für alle erscheint, die nicht selbst gewählt haben. Jeder kann in den Einstellungen unter Darstellung eine eigene wählen, und diese Wahl hat hier Vorrang.",
    "You are a helpful assistant.": "Du bist ein hilfreicher Assistent.",
    "A model marked public is available to everyone; one that is not is available to the groups named here. Model access is separate from permissions — one says what somebody may do, the other what with.": "Ein als öffentlich markiertes Modell steht allen zur Verfügung; eines, das es nicht ist, den hier genannten Gruppen. Der Zugriff auf Modelle ist von Berechtigungen getrennt — das eine sagt, was jemand tun darf, das andere, womit.",
    "Members": "Mitglieder",
    "Members keep their accounts and lose whatever this group granted them. Every share naming this group goes too — nothing cascades to those, so they are deleted explicitly.": "Mitglieder behalten ihre Konten und verlieren, was diese Gruppe ihnen gewährt hat. Jede Freigabe, die diese Gruppe nennt, verschwindet ebenfalls — nichts davon kaskadiert, daher werden sie ausdrücklich gelöscht.",
    "Models this group unlocks": "Modelle, die diese Gruppe freischaltet",
    "Permissions this group adds": "Berechtigungen, die diese Gruppe hinzufügt",
    "Quotas": "Kontingente",
    "Remove": "Entfernen",
    "Save group": "Gruppe speichern",
    "The one place membership is edited. A user's own page links here rather than offering a second control for the same value.": "Der einzige Ort, an dem die Mitgliedschaft bearbeitet wird. Die Seite eines Benutzers verweist hierher, statt ein zweites Bedienelement für denselben Wert anzubieten.",
    "already in the baseline": "bereits in der Grundausstattung",
    "no opinion": "keine Vorgabe",
    "Baseline permissions": "Grundberechtigungen",
    "New group name": "Name der neuen Gruppe",
    "No groups yet. Everybody gets the baseline above and nothing more.": "Noch keine Gruppen. Jeder bekommt die Grundausstattung oben und nichts weiter.",
    "Save baseline": "Grundausstattung speichern",
    "What every signed-in user can do before any group is considered. Turn something off here and grant it through a group to make it opt-in.": "Was jeder angemeldete Benutzer darf, bevor irgendeine Gruppe berücksichtigt wird. Schalten Sie hier etwas aus und gewähren Sie es über eine Gruppe, damit es nur auf Wunsch gilt.",
    "quotas": "Kontingente",
    "ComfyUI": "ComfyUI",
    "Default workflow": "Standard-Workflow",
    "Denoise": "Entrauschen",
    "Height": "Höhe",
    "Memory": "Gedächtnis",
    "Model that reviews": "Prüfendes Modell",
    "Negative prompt": "Negativer Prompt",
    "Sampler": "Sampler",
    "Scheduler": "Scheduler",
    "Steps": "Schritte",
    "Timeout (seconds)": "Zeitlimit (Sekunden)",
    "Width": "Breite",
    "Workflows": "Workflows",
    "A server's tool names and descriptions are sent to the model as instructions, and what it returns is read back as fact. Add servers you trust, the way you would a dependency.": "Die Werkzeugnamen und -beschreibungen eines Servers werden dem Modell als Anweisungen gesendet, und was er zurückgibt, wird als Tatsache gelesen. Fügen Sie Server hinzu, denen Sie vertrauen, so wie Sie eine Abhängigkeit hinzufügen würden.",
    "No servers yet. You will need the URL of an MCP endpoint that speaks streamable HTTP — local ones launched as a subprocess are not supported.": "Noch keine Server. Sie benötigen die URL eines MCP-Endpunkts, der Streamable HTTP spricht — lokale, als Unterprozess gestartete Server werden nicht unterstützt.",
    "- Use the GitHub tools for anything about our repositories.": "- Verwende für alles rund um unsere Repositories die GitHub-Werkzeuge.",
    "All servers": "Alle Server",
    "Availability": "Verfügbarkeit",
    "Available to everyone": "Für alle verfügbar",
    "Credential": "Zugangsdaten",
    "Discovered at the last refresh. Untick one to withhold it — a tool this server adds later is offered by default.": "Beim letzten Aktualisieren gefunden. Entfernen Sie den Haken, um eines zurückzuhalten — ein Werkzeug, das dieser Server später hinzufügt, wird standardmäßig angeboten.",
    "Enabled — offered in chats": "Aktiviert — wird in Chats angeboten",
    "Endpoint URL": "Endpunkt-URL",
    "GitHub": "GitHub",
    "Groups with access": "Gruppen mit Zugriff",
    "Guidance": "Hinweise",
    "Header or parameter name": "Name des Headers oder Parameters",
    "How it is sent": "Wie es gesendet wird",
    "Identifier": "Kennung",
    "Ignored while the server is available to everyone.": "Wird ignoriert, solange der Server für alle verfügbar ist.",
    "May reach private and loopback addresses": "Darf private und Loopback-Adressen erreichen",
    "Most characters to keep per call": "Höchstzahl an Zeichen pro Aufruf",
    "No secret set": "Kein Geheimnis gesetzt",
    "Position": "Position",
    "Secret": "Geheimnis",
    "The server": "Der Server",
    "The streamable-HTTP endpoint itself, the one that accepts a POST. A server that answers with a redirect to somewhere else will be refused.": "Der Streamable-HTTP-Endpunkt selbst, also der, der ein POST annimmt. Ein Server, der mit einer Weiterleitung woandershin antwortet, wird abgelehnt.",
    "Tools it offers": "Angebotene Werkzeuge",
    "github": "github",
    "https://mcp.example.com/mcp": "https://mcp.example.com/mcp",
    "All models": "Alle Modelle",
    "Capabilities": "Fähigkeiten",
    "Context length": "Kontextlänge",
    "Default reasoning effort": "Standard-Denkaufwand",
    "Description": "Beschreibung",
    "Display name": "Anzeigename",
    "Facts for other models": "Fakten für andere Modelle",
    "How many tokens this model can hold, filled in from the endpoint where it says. Leave it empty if you do not know: the context percentage and automatic compaction both stay off rather than working from a guess.": "Wie viele Tokens dieses Modell fassen kann; wird vom Endpunkt übernommen, wo er es angibt. Lassen Sie es leer, wenn Sie es nicht wissen: Kontextanteil und automatische Zusammenfassung bleiben dann aus, statt mit einer Schätzung zu arbeiten.",
    "Ignored while the model is available to everyone.": "Wird ignoriert, solange das Modell für alle verfügbar ist.",
    "Model image": "Modellbild",
    "Never shown to a person. It goes into the list of the other models that a model sees when it is allowed to ask one of them a question, so write what would help it choose: size, what this one is good and bad at, a score you trust. Leave it empty and the description above carries that on its own.": "Wird nie einer Person angezeigt. Es landet in der Liste der anderen Modelle, die ein Modell sieht, wenn es einem von ihnen eine Frage stellen darf — schreiben Sie also, was ihm bei der Wahl hilft: Größe, worin dieses gut und schlecht ist, ein Wert, dem Sie vertrauen. Bleibt es leer, übernimmt das die Beschreibung oben allein.",
    "PNG, JPEG, WEBP or GIF, under 2 MB. Without one, the model gets a generated badge whose colour is derived from its id.": "PNG, JPEG, WEBP oder GIF, unter 2 MB. Ohne Bild erhält das Modell ein generiertes Abzeichen, dessen Farbe aus seiner ID abgeleitet wird.",
    "Parameters, quantisation, a benchmark figure, what it is bad at": "Parameter, Quantisierung, ein Benchmark-Wert, worin es schwach ist",
    "Pinned — shortcut in the chat sidebar": "Angeheftet — Verknüpfung in der Chat-Seitenleiste",
    "Presentation": "Darstellung",
    "Reasoning": "Denken",
    "Reasoning efforts this model accepts": "Denkaufwände, die dieses Modell akzeptiert",
    "Remove image": "Bild entfernen",
    "Shown instead of the raw id. Empty uses the id.": "Wird statt der rohen ID angezeigt. Leer verwendet die ID.",
    "Uncheck to restrict this model to chosen groups. Administrators always have access.": "Entfernen Sie den Haken, um dieses Modell auf ausgewählte Gruppen zu beschränken. Administratoren haben immer Zugriff.",
    "Used by chats on this model that have no prompt of their own. A chat's own prompt overrides this; this overrides the instance prompt.": "Wird von Chats mit diesem Modell verwendet, die keinen eigenen Prompt haben. Der eigene Prompt eines Chats hat Vorrang davor; dieser hat Vorrang vor dem Prompt der Instanz.",
    "What is this model good at?": "Worin ist dieses Modell gut?",
    "needs tools": "braucht Werkzeuge",
    "unknown": "unbekannt",
    "All connections": "Alle Verbindungen",
    "Connection": "Verbindung",
    "Disable": "Deaktivieren",
    "Enable": "Aktivieren",
    "Make public": "Öffentlich machen",
    "Next": "Weiter",
    "Nothing matches that filter.": "Nichts passt zu diesem Filter.",
    "Previous": "Zurück",
    "Restrict": "Beschränken",
    "Search models": "Modelle suchen",
    "Search models…": "Modelle suchen…",
    "Select all": "Alle auswählen",
    "With selected:": "Mit Auswahl:",
    "pinned": "angeheftet",
    "Agent mode": "Agent-Modus",
    "Answering model": "Antwortendes Modell",
    "Attached files": "Angehängte Dateien",
    "Defined in code — part of the schema sent to the endpoint alongside the prompt, not guidance layered on top of it. They are statements of fact about what each tool does, so they change when the tool does; editing them here would let the text quietly become a lie. A custom tool's description will be editable, because a custom tool is a row rather than a function.": "Im Code definiert — Teil des Schemas, das zusammen mit dem Prompt an den Endpunkt geht, keine darübergelegte Anleitung. Es sind Tatsachenaussagen darüber, was jedes Werkzeug tut, also ändern sie sich mit dem Werkzeug; sie hier zu bearbeiten, ließe den Text stillschweigend zur Lüge werden. Die Beschreibung eines eigenen Werkzeugs ist bearbeitbar, weil ein eigenes Werkzeug ein Eintrag ist und keine Funktion.",
    "Length": "Länge",
    "No models configured": "Keine Modelle eingerichtet",
    "Preamble character cap": "Zeichenlimit der Präambel",
    "Preview": "Vorschau",
    "Prompt groups": "Prompt-Gruppen",
    "Prompts saved.": "Prompts gespeichert.",
    "Put every prompt back to its built-in wording? Everything you have edited here is lost.": "Jeden Prompt auf seinen eingebauten Wortlaut zurücksetzen? Alles, was Sie hier bearbeitet haben, geht verloren.",
    "Restore": "Wiederherstellen",
    "Restore all defaults": "Alle Standards wiederherstellen",
    "Restore defaults": "Standards wiederherstellen",
    "The whole system message, assembled from what is in the boxes below — including changes you have not saved yet. Your own memories and skills are used, because a preview against invented ones cannot tell you whether it reads well against what is actually there.": "Die vollständige Systemnachricht, zusammengesetzt aus dem Inhalt der Felder unten — einschließlich noch nicht gespeicherter Änderungen. Ihre eigenen Erinnerungen und Fertigkeiten werden verwendet, denn eine Vorschau mit erfundenen kann Ihnen nicht sagen, ob es sich mit dem, was tatsächlich da ist, gut liest.",
    "This chat is": "Dieser Chat ist",
    "Tool descriptions": "Werkzeugbeschreibungen",
    "Tools offered": "Angebotene Werkzeuge",
    "Variables": "Variablen",
    "empty": "leer",
    "only in the title request": "nur in der Titelanfrage",
    "A schedule that comes round faster than its chat can answer would build a backlog for ever. Past this, a run is skipped and says so on the schedule rather than joining the queue.": "Ein Zeitplan, der schneller wiederkehrt, als sein Chat antworten kann, würde endlos einen Rückstau aufbauen. Darüber hinaus wird ein Lauf übersprungen und das im Zeitplan vermerkt, statt sich in die Warteschlange einzureihen.",
    "Check for due work every": "Auf fällige Arbeit prüfen alle",
    "Fifty schedules due at nine o'clock must not open fifty replies against one endpoint. The rest wait their turn rather than being dropped.": "Fünfzig Zeitpläne, die um neun Uhr fällig sind, dürfen nicht fünfzig Antworten gegen einen Endpunkt öffnen. Die übrigen warten, bis sie an der Reihe sind, statt verworfen zu werden.",
    "How often it looks": "Wie oft nachgesehen wird",
    "Let scheduled work run": "Geplante Arbeit laufen lassen",
    "Nothing may repeat faster than": "Nichts darf sich öfter wiederholen als",
    "Refused at the point of creation, with the reason. Existing schedules over a lowered limit keep running; only new ones are refused.": "Wird beim Erstellen abgelehnt, mit Begründung. Bestehende Zeitpläne über einem gesenkten Limit laufen weiter; nur neue werden abgelehnt.",
    "Runs at once": "Gleichzeitige Läufe",
    "Schedule work": "Arbeit planen",
    "Scheduled": "Geplant",
    "Schedules per person": "Zeitpläne pro Person",
    "Seconds. A floor on how often one schedule may come round. Raise it if people are setting things to run more often than the work takes.": "Sekunden. Eine Untergrenze dafür, wie oft ein Zeitplan wiederkehren darf. Erhöhen Sie sie, wenn Leute Dinge öfter laufen lassen, als die Arbeit dauert.",
    "Seconds. This is how late a run can be, not how often anything happens: the finest a schedule can be set to is one minute, so anything under that buys nothing. One indexed query per tick.": "Sekunden. Es geht darum, wie verspätet ein Lauf sein kann, nicht wie oft etwas geschieht: Die feinste Einstellung eines Zeitplans ist eine Minute, also bringt alles darunter nichts. Eine indexierte Abfrage pro Takt.",
    "Turns that may pile up in one chat": "Runden, die sich in einem Chat stauen dürfen",
    "What it may spend": "Was er verbrauchen darf",
    "A ceiling — a model asking for more gets this.": "Eine Obergrenze — ein Modell, das mehr verlangt, bekommt diesen Wert.",
    "API URL": "API-URL",
    "Allow fetching addresses on this machine and this network": "Abrufen von Adressen auf diesem Rechner und in diesem Netzwerk erlauben",
    "Change only for a self-hosted Firecrawl.": "Nur bei selbst gehostetem Firecrawl ändern.",
    "DuckDuckGo region": "DuckDuckGo-Region",
    "Firecrawl": "Firecrawl",
    "Instance URL": "Instanz-URL",
    "Let a model fetch a page itself": "Ein Modell eine Seite selbst abrufen lassen",
    "Lets a model look things up while it answers. It is offered as a tool the model chooses to call, so nothing changes for a question that does not need it — and it is only offered to models marked as supporting tools, because sending a tool list to one that does not fails the whole request.": "Lässt ein Modell nachschlagen, während es antwortet. Es wird als Werkzeug angeboten, das das Modell selbst aufruft, sodass sich für eine Frage, die es nicht braucht, nichts ändert — und es wird nur Modellen angeboten, die als werkzeugfähig markiert sind, denn eine Werkzeugliste an ein Modell zu senden, das keine unterstützt, lässt die ganze Anfrage scheitern.",
    "Link": "Link",
    "Offer web search to models that support tools": "Websuche Modellen anbieten, die Werkzeuge unterstützen",
    "Only used when SearXNG is the chosen provider. Your own instance, so no third party sees the queries.": "Wird nur verwendet, wenn SearXNG der gewählte Anbieter ist. Ihre eigene Instanz, sodass kein Dritter die Suchanfragen sieht.",
    "Provider": "Anbieter",
    "Results per search": "Ergebnisse pro Suche",
    "Safe search": "SafeSearch",
    "Saving links": "Links speichern",
    "SearXNG": "SearXNG",
    "Search settings saved.": "Sucheinstellungen gespeichert.",
    "Try it": "Ausprobieren",
    "http://127.0.0.1:8888": "http://127.0.0.1:8888",
    "https://api.firecrawl.dev": "https://api.firecrawl.dev",
    "mallorn tree": "Mallornbaum",
    "no setup": "keine Einrichtung",
    "wt-wt": "wt-wt",
    "Add": "Hinzufügen",
    "Add a suggestion": "Vorschlag hinzufügen",
    "Delete suggestion": "Vorschlag löschen",
    "No suggestions": "Keine Vorschläge",
    "One line under the name, saying what it is for.": "Eine Zeile unter dem Namen, die sagt, wofür er ist.",
    "Order on the screen, lowest first.": "Reihenfolge auf dem Bildschirm, niedrigste zuerst.",
    "Prompt": "Prompt",
    "Sent as the first message when the card is clicked.": "Wird als erste Nachricht gesendet, wenn auf die Karte geklickt wird.",
    "Sent as the first message. Nothing is added to it, so a prompt that needs material should ask for it.": "Wird als erste Nachricht gesendet. Nichts wird hinzugefügt, also sollte ein Prompt, der Material braucht, danach fragen.",
    "Show this one": "Diesen anzeigen",
    "Summarise a document": "Ein Dokument zusammenfassen",
    "The heading on the card.": "Die Überschrift auf der Karte.",
    "The new-chat screen shows its empty state instead.": "Der Bildschirm für neue Chats zeigt stattdessen seinen leeren Zustand.",
    "What it is for, in one line.": "Wofür er ist, in einer Zeile.",
    "below the cut": "unterhalb der Grenze",
    "hidden": "ausgeblendet",
    "- Check the weather rather than guessing at it.": "- Prüfe das Wetter, statt es zu raten.",
    "Accept: application/json": "Accept: application/json",
    "All tools": "Alle Werkzeuge",
    "Arguments": "Argumente",
    "Body": "Body",
    "Dotted; a number indexes a list. Leave empty for the whole document. A path that leads nowhere gives the whole document rather than nothing.": "Mit Punkten getrennt; eine Zahl indexiert eine Liste. Leer lassen für das ganze Dokument. Ein Pfad, der ins Leere führt, liefert das ganze Dokument statt nichts.",
    "Headers": "Header",
    "Ignored for GET. Placeholders are escaped for JSON, so a value cannot end the string it sits in and add a field.": "Bei GET ignoriert. Platzhalter werden für JSON maskiert, sodass ein Wert die Zeichenkette, in der er steht, nicht beenden und ein Feld hinzufügen kann.",
    "Ignored while the tool is available to everyone.": "Wird ignoriert, solange das Werkzeug für alle verfügbar ist.",
    "Leave the second unticked unless this tool points at something on your own network. It is what stops a tool being aimed at this server, a router, or a cloud metadata endpoint.": "Lassen Sie das zweite ohne Haken, es sei denn, dieses Werkzeug zielt auf etwas in Ihrem eigenen Netzwerk. Genau das verhindert, dass ein Werkzeug auf diesen Server, einen Router oder einen Cloud-Metadaten-Endpunkt gerichtet wird.",
    "Look up the current weather for a city.": "Ermittle das aktuelle Wetter für eine Stadt.",
    "Method": "Methode",
    "Most characters to keep": "Höchstzahl aufbewahrter Zeichen",
    "Parameters": "Parameter",
    "Path into the JSON": "Pfad im JSON",
    "Placeholders are filled from the arguments and escaped, so a value cannot add a path segment or a query of its own. The scheme and host must be written out — they cannot come from an argument.": "Platzhalter werden aus den Argumenten gefüllt und maskiert, sodass ein Wert kein eigenes Pfadsegment und keine eigene Abfrage hinzufügen kann. Schema und Host müssen ausgeschrieben sein — sie können nicht aus einem Argument stammen.",
    "Read the response as": "Antwort lesen als",
    "Sent to the model verbatim. This is the whole basis on which it decides whether to call this tool, so say what it does and when it is the right thing to use.": "Wird dem Modell wörtlich gesendet. Darauf allein stützt es die Entscheidung, ob es dieses Werkzeug aufruft — schreiben Sie also, was es tut und wann es die richtige Wahl ist.",
    "Shown in the transcript when the model uses it.": "Wird im Verlauf angezeigt, wenn das Modell es verwendet.",
    "Spent out of the context window on every call. The rest is cut off, and the model is told so.": "Wird bei jedem Aufruf vom Kontextfenster abgezogen. Der Rest wird abgeschnitten, und das Modell erfährt es.",
    "The answer": "Die Antwort",
    "The call": "Der Aufruf",
    "URL": "URL",
    "Uncheck to restrict this tool to chosen groups. Administrators always have access.": "Entfernen Sie den Haken, um dieses Werkzeug auf ausgewählte Gruppen zu beschränken. Administratoren haben immer Zugriff.",
    "Weather": "Wetter",
    "What it is": "Was es ist",
    "data.items.0.title": "data.items.0.title",
    "weather": "weather",
    "Custom tools": "Eigene Werkzeuge",
    "Search tools": "Werkzeuge suchen",
    "Search tools…": "Werkzeuge suchen…",
    "last call failed": "letzter Aufruf fehlgeschlagen",
    "%(want)s in %(label)s": "%(want)s in %(label)s",
    "An update has been requested and is waiting for the helper to pick it up. The service restarts when it does.": "Ein Update wurde angefordert und wartet darauf, dass der Helfer es übernimmt. Der Dienst startet dann neu.",
    "Applying an update": "Ein Update wird angewendet",
    "Automatic updates": "Automatische Updates",
    "Check every (hours)": "Prüfen alle (Stunden)",
    "Check for releases on a schedule and install the ones the policy allows — only when nothing is running and nobody has been active for a while, and always after a backup. Off by default.": "Regelmäßig nach Releases suchen und diejenigen installieren, die die Richtlinie erlaubt — nur wenn nichts läuft und eine Weile niemand aktiv war, und immer nach einer Sicherung. Standardmäßig aus.",
    "Checkout": "Checkout",
    "Commit": "Commit",
    "Every release": "Jedes Release",
    "Every restart ends every open terminal session — a command still running on the far side is cut off. A reply being written is saved with whatever it has.": "Jeder Neustart beendet jede offene Terminal-Sitzung — ein Befehl, der auf der Gegenseite noch läuft, wird abgebrochen. Eine Antwort, die gerade geschrieben wird, wird mit dem gespeichert, was sie hat.",
    "Fixes and features (1.x)": "Korrekturen und Funktionen (1.x)",
    "Fixes only (1.2.x)": "Nur Korrekturen (1.2.x)",
    "Harness spec: %(have)s here": "Harness-Spezifikation: hier %(have)s",
    "Hold new replies after (minutes)": "Neue Antworten zurückhalten nach (Minuten)",
    "Idle for (minutes)": "Untätig seit (Minuten)",
    "If it is never idle, new replies wait so the running ones can finish. 0: never hold.": "Wird es nie untätig, warten neue Antworten, damit die laufenden fertig werden können. 0: nie zurückhalten.",
    "Install automatically": "Automatisch installieren",
    "Judged on the newest release, which is what the helper installs.": "Bewertet wird das neueste Release, denn das installiert der Helfer.",
    "Last checked %(when)s UTC.": "Zuletzt geprüft %(when)s UTC.",
    "Never": "Nie",
    "New replies are held until it is done.": "Neue Antworten werden zurückgehalten, bis es fertig ist.",
    "Nobody opened a page or sent anything for this long.": "So lange hat niemand eine Seite geöffnet oder etwas gesendet.",
    "Nothing known about the remote yet. Check it above.": "Über die Gegenstelle ist noch nichts bekannt. Prüfen Sie sie oben.",
    "Nothing released": "Nichts veröffentlicht",
    "Nothing to apply": "Nichts zu installieren",
    "Now": "Jetzt",
    "Only between": "Nur zwischen",
    "Replies being written are saved with what they have; open terminals are cut off. A backup is taken first.": "Antworten, die gerade geschrieben werden, werden mit dem gespeichert, was sie haben; offene Terminals werden abgebrochen. Zuerst wird eine Sicherung erstellt.",
    "Running": "Läuft",
    "Server time. Empty: any time.": "Serverzeit. Leer: jederzeit.",
    "Still: %(what)s.": "Noch: %(what)s.",
    "This checkout has uncommitted changes, and updating discards them. Nothing here is meant to be edited in place, so this usually means somebody was debugging on the box.": "Dieser Checkout hat nicht committete Änderungen, und das Update verwirft sie. Hier ist nichts dafür gedacht, direkt bearbeitet zu werden, also hat wahrscheinlich jemand auf dem Rechner debuggt.",
    "This was not installed from a git checkout — a container image, or a wheel — so there is nothing here to compare or update. Pull a new image instead.": "Dies wurde nicht aus einem Git-Checkout installiert — sondern als Container-Image oder Wheel —, daher gibt es hier nichts zu vergleichen oder zu aktualisieren. Ziehen Sie stattdessen ein neues Image.",
    "Update now": "Jetzt aktualisieren",
    "Update now?": "Jetzt aktualisieren?",
    "Update to %(label)s when idle": "Auf %(label)s aktualisieren, sobald untätig",
    "Version": "Version",
    "Waiting to install %(label)s.": "Wartet auf die Installation von %(label)s.",
    "Withdraw the request": "Anforderung zurückziehen",
    "nothing released yet": "noch nichts veröffentlicht",
    "uncommitted changes": "nicht committete Änderungen",
    "up to date": "aktuell",
    "Active": "Aktiv",
    "An administrator bypasses every permission and every quota below.": "Ein Administrator umgeht jede Berechtigung und jedes Kontingent unten.",
    "Counted from the first of the month, UTC. Recorded for every reply including one that was stopped or failed — an endpoint charges for tokens it generated whether or not anybody wanted them.": "Gezählt ab dem Ersten des Monats, UTC. Erfasst für jede Antwort, auch eine angehaltene oder fehlgeschlagene — ein Endpunkt berechnet die Tokens, die er erzeugt hat, ob sie jemand wollte oder nicht.",
    "Edited from the group's own page. One control per value, so a save here cannot undo a save there.": "Wird auf der Seite der Gruppe bearbeitet. Ein Bedienelement pro Wert, damit ein Speichern hier kein Speichern dort rückgängig machen kann.",
    "Groups": "Gruppen",
    "Images": "Bilder",
    "In no group. They get the baseline and nothing more.": "In keiner Gruppe. Sie erhalten die Grundausstattung und nichts weiter.",
    "Kind": "Art",
    "Last used": "Zuletzt verwendet",
    "Limits in force": "Geltende Limits",
    "Model access is separate from permissions: a permission says what somebody may do, this says what they may do it with.": "Der Zugriff auf Modelle ist von Berechtigungen getrennt: Eine Berechtigung sagt, was jemand tun darf, dies sagt, womit.",
    "Models they can use": "Modelle, die sie verwenden können",
    "New password": "Neues Passwort",
    "None.": "Keine.",
    "None. They cannot start a chat at all.": "Keine. Sie können überhaupt keinen Chat beginnen.",
    "Password and removal": "Passwort und Entfernen",
    "Replies": "Antworten",
    "Reset password": "Passwort zurücksetzen",
    "Revoke": "Widerrufen",
    "Revoke this token?": "Dieses Token widerrufen?",
    "Role": "Rolle",
    "Scopes": "Bereiche",
    "Signs them out everywhere. An administrator resetting a password usually means the account is compromised or the person has gone.": "Meldet sie überall ab. Setzt ein Administrator ein Passwort zurück, ist das Konto meist kompromittiert oder die Person gegangen.",
    "The API tokens this account made and the LLeMbas CLIs it signed in. Each acts as this account, with only its scopes. Revoking one deletes it and signs out whatever holds it at once.": "Die API-Tokens, die dieses Konto erstellt hat, und die LLeMbas CLIs, die es angemeldet hat. Jedes handelt als dieses Konto, nur mit seinen Bereichen. Widerrufen löscht es und meldet sofort alles ab, was es hält.",
    "This month": "Diesen Monat",
    "Tokens": "Tokens",
    "Tokens and devices": "Tokens und Geräte",
    "Turning this off signs them out everywhere at once, rather than waiting for a cookie to expire.": "Das Ausschalten meldet sie sofort überall ab, statt zu warten, bis ein Cookie abläuft.",
    "What this account can do": "Was dieses Konto darf",
    "Whatever holds %(name)s is signed out at once.": "Alles, was %(name)s hält, wird sofort abgemeldet.",
    "device": "Gerät",
    "linked now": "gerade verbunden",
    "never": "nie",
    "not granted": "nicht gewährt",
    "personal": "persönlich",
    "Add an account": "Konto hinzufügen",
    "Email": "E-Mail",
    "Password": "Passwort",
    "Search by name or email…": "Nach Name oder E-Mail suchen…",
    "Search users": "Benutzer suchen",
    "Tokens this month": "Tokens diesen Monat",
    "Without going through registration — useful when sign-up is closed.": "Ohne den Weg über die Registrierung — nützlich, wenn die Registrierung geschlossen ist.",
    "admin": "Administrator",
    "pending": "ausstehend",
    "ComfyUI API format": "ComfyUI-API-Format",
    "Lowercase letters, digits, hyphens and underscores. This is what the model writes when it picks this workflow.": "Kleinbuchstaben, Ziffern, Bindestriche und Unterstriche. Das schreibt das Modell, wenn es diesen Workflow wählt.",
    "Name the model uses": "Name, den das Modell verwendet",
    "Offer this workflow": "Diesen Workflow anbieten",
    "Photographic and slow. Best for people, interiors and product shots at 1024px.": "Fotografisch und langsam. Am besten für Menschen, Innenräume und Produktaufnahmen bei 1024 px.",
    "Required.": "Erforderlich.",
    "SDXL, photographic": "SDXL, fotografisch",
    "Shown to you, in the list.": "Wird Ihnen in der Liste angezeigt.",
    "The placeholders": "Die Platzhalter",
    "The workflow": "Der Workflow",
    "sdxl-photo": "sdxl-photo",
    "All places": "Alle Orte",
    "No directories here.": "Hier gibt es keine Ordner.",
    "Not trusted on the device": "Auf dem Gerät nicht vertrauenswürdig",
    "Places on this device": "Orte auf diesem Gerät",
    "The device will not open a session here.": "Das Gerät öffnet hier keine Sitzung.",
    "This device offers no directories: its remote roots are empty or missing.": "Dieses Gerät bietet keine Ordner an: Seine Remote-Wurzeln sind leer oder fehlen.",
    "Up a level": "Eine Ebene höher",
    "Sign in": "Anmelden",
    "At least 8 characters.": "Mindestens 8 Zeichen.",
    "Nobody has claimed this instance yet. The first account becomes its administrator.": "Diese Instanz hat noch niemand übernommen. Das erste Konto wird ihr Administrator.",
    "Copy code": "Code kopieren",
    "Dismiss": "Schließen",
    "Canvas": "Arbeitsfläche",
    "Opening…": "Wird geöffnet…",
    "Cancel": "Abbrechen",
    "/ for commands": "/ für Befehle",
    "A temporary chat reads and changes nothing. Keep it to choose another mode.": "Ein temporärer Chat liest nur und ändert nichts. Behalten Sie ihn, um einen anderen Modus zu wählen.",
    "Always allowed here": "Hier immer erlaubt",
    "Approval mode": "Freigabemodus",
    "Ask me about these again": "Mich dazu wieder fragen",
    "Attach": "Anhängen",
    "Choose the directory this chat works in": "Wählen Sie das Verzeichnis, in dem dieser Chat arbeitet",
    "Dictate a message": "Nachricht diktieren",
    "Drop to attach": "Zum Anhängen hier ablegen",
    "Fetch a page and attach its text": "Eine Seite abrufen und ihren Text anhängen",
    "File": "Datei",
    "Image": "Bild",
    "Message": "Nachricht",
    "PDF, text, code": "PDF, Text, Code",
    "Project directory": "Projektverzeichnis",
    "Reasoning effort": "Denkaufwand",
    "Send": "Senden",
    "Sent only to vision models": "Wird nur an Modelle gesendet, die Bilder sehen können",
    "Skills": "Fertigkeiten",
    "Edit message": "Nachricht bearbeiten",
    "Rewind and send": "Zurückspulen und senden",
    "Rewind the conversation": "Das Gespräch zurückspulen",
    "editing": "wird bearbeitet",
    "Close inspector": "Inspektor schließen",
    "Request inspector": "Anfrage-Inspektor",
    "Last reply": "Letzte Antwort",
    "None is being sent.": "Es wird keine gesendet.",
    "None. A model has to be marked as supporting tools, the user needs the permission, and the tool itself has to be turned on.": "Keine. Ein Modell muss als werkzeugfähig markiert sein, der Benutzer braucht die Berechtigung, und das Werkzeug selbst muss eingeschaltet sein.",
    "Nothing has been answered in this chat yet.": "In diesem Chat wurde noch nichts beantwortet.",
    "Rebuilt now against the current configuration. This is not a recording of the request that produced the last reply — if a prompt or a setting has changed since, this shows what would be sent today.": "Jetzt anhand der aktuellen Konfiguration neu aufgebaut. Dies ist keine Aufzeichnung der Anfrage, die die letzte Antwort erzeugt hat — hat sich seitdem ein Prompt oder eine Einstellung geändert, sehen Sie, was heute gesendet würde.",
    "Request body": "Anfrage-Body",
    "System message": "Systemnachricht",
    "This chat": "Dieser Chat",
    "complete": "fertig",
    "error": "Fehler",
    "estimated": "geschätzt",
    "writing": "schreibt",
    "Allow runs what is in the box. It is not checked against this chat's rules again — you typed it.": "Erlauben führt aus, was im Feld steht. Es wird nicht erneut gegen die Regeln dieses Chats geprüft — Sie haben es eingegeben.",
    "Change this before it runs": "Vor dem Ausführen ändern",
    "Something else": "Etwas anderes",
    "The model is asking you": "Das Modell fragt Sie",
    "Why not, and what to do instead": "Warum nicht, und was stattdessen zu tun ist",
    "Why not, and what to do instead…": "Warum nicht, und was stattdessen zu tun ist…",
    "Your own answer…": "Ihre eigene Antwort…",
    "A page to read": "Eine Seite zum Lesen",
    "Already in this chat": "Bereits in diesem Chat",
    "Fetch this page": "Diese Seite abrufen",
    "Background job": "Hintergrundaufgabe",
    "Continue": "Fortsetzen",
    "Continue from where it stopped": "Dort fortsetzen, wo es aufgehört hat",
    "Copy message": "Nachricht kopieren",
    "Discard": "Verwerfen",
    "Discard this message?": "Diese Nachricht verwerfen?",
    "Edit and retry from here": "Bearbeiten und ab hier neu versuchen",
    "Files the device changed are not undone": "Dateien, die das Gerät geändert hat, werden nicht zurückgesetzt",
    "Read this reply aloud": "Diese Antwort vorlesen",
    "Regenerate reply": "Antwort neu generieren",
    "Send now": "Jetzt senden",
    "Thinking": "Denkt nach",
    "Typed in the terminal on the device; this chat shows the same session": "Im Terminal auf dem Gerät eingegeben; dieser Chat zeigt dieselbe Sitzung",
    "Written by": "Geschrieben von",
    "in the terminal": "im Terminal",
    "Choose a model": "Modell wählen",
    "Context window": "Kontextfenster",
    "Filter models": "Modelle filtern",
    "Filter models…": "Modelle filtern…",
    "In another data group than this chat (%(group)s). Start a new chat to use them:": "In einer anderen Datengruppe als dieser Chat (%(group)s). Starten Sie einen neuen Chat, um sie zu verwenden:",
    "Loaded": "Geladen",
    "Loading": "Wird geladen",
    "No model matches that.": "Kein Modell passt dazu.",
    "Sees images": "Sieht Bilder",
    "Carry it out": "Ausführen",
    "Switches to Edit — commands still ask.": "Wechselt zu Edit — Befehle fragen weiterhin nach.",
    "What was found": "Was gefunden wurde",
    "Auto: copy": "Automatisch: kopieren",
    "Auto: off": "Automatisch: nichts",
    "Auto: send": "Automatisch: senden",
    "Close terminal": "Terminal schließen",
    "Connecting…": "Verbinde…",
    "Copy the last command and its output": "Den letzten Befehl und seine Ausgabe kopieren",
    "Put the last command and its output into the message box": "Den letzten Befehl und seine Ausgabe ins Nachrichtenfeld legen",
    "Resize the terminal": "Größe des Terminals ändern",
    "Send to chat": "An den Chat senden",
    "Terminal": "Terminal",
    "What to do when a command finishes": "Was geschehen soll, wenn ein Befehl fertig ist",
    "What to do with each command you run": "Was mit jedem ausgeführten Befehl geschehen soll",
    "the device": "das Gerät",
    "Carried on after the instance restarted": "Nach dem Neustart der Instanz fortgesetzt",
    "These are no longer sent to the model; a summary of them goes instead. They are kept here so nothing is lost.": "Diese werden nicht mehr an das Modell gesendet; stattdessen geht eine Zusammenfassung von ihnen. Sie bleiben hier, damit nichts verloren geht.",
    "No output.": "Keine Ausgabe.",
    "Nothing was found.": "Nichts gefunden.",
    "Show the whole change": "Die ganze Änderung anzeigen",
    "The generated image": "Das generierte Bild",
    "edited by you": "von Ihnen bearbeitet",
    "failed": "fehlgeschlagen",
    "Compacted": "Zusammengefasst",
    "Earlier turns are summarised. They are still in the transcript; they just stop being sent.": "Frühere Runden sind zusammengefasst. Sie stehen noch im Verlauf; sie werden nur nicht mehr gesendet.",
    "In the window now": "Jetzt im Fenster",
    "Of which sent": "Davon gesendet",
    "Of which written": "Davon geschrieben",
    "Spent in total": "Insgesamt verbraucht",
    "Window size": "Fenstergröße",
    "Chat": "Chat",
    "Chat settings": "Chat-Einstellungen",
    "Compact": "Zusammenfassen",
    "Inspect this chat": "Diesen Chat untersuchen",
    "Keep this chat": "Diesen Chat behalten",
    "Leave a field empty to let the provider decide. Values outside the allowed range are ignored rather than clamped.": "Lassen Sie ein Feld leer, damit der Anbieter entscheidet. Werte außerhalb des erlaubten Bereichs werden ignoriert, nicht angepasst.",
    "Makes it an ordinary chat that can save. Otherwise it is deleted %(after)s.": "Macht ihn zu einem gewöhnlichen Chat, der speichern kann. Andernfalls wird er %(after)s gelöscht.",
    "Max tokens": "Max. Tokens",
    "No models available": "Keine Modelle verfügbar",
    "Open a shell on %(device)s": "Eine Shell auf %(device)s öffnen",
    "Rename chat": "Chat umbenennen",
    "Start a temporary chat: not listed, saves nothing, and deleted %(after)s.": "Einen temporären Chat starten: nicht aufgeführt, speichert nichts und wird %(after)s gelöscht.",
    "Starting a temporary chat. Click to go back to a normal one.": "Ein temporärer Chat wird gestartet. Klicken Sie, um zu einem normalen zurückzukehren.",
    "Summarise the earlier turns so they stop costing context": "Die früheren Runden zusammenfassen, damit sie keinen Kontext mehr kosten",
    "Temperature": "Temperatur",
    "Temporary chat": "Temporärer Chat",
    "Temporary · read-only": "Temporär · schreibgeschützt",
    "Top-p": "Top-p",
    "What would you ask?": "Was möchten Sie fragen?",
    "Windows": "Fenster",
    "device missing": "Gerät fehlt",
    "A program on another machine is asking to use this instance as you.": "Ein Programm auf einem anderen Rechner bittet darum, diese Instanz in Ihrem Namen zu verwenden.",
    "Approve": "Bestätigen",
    "Asked from": "Angefragt von",
    "Back to your settings": "Zurück zu Ihren Einstellungen",
    "Deny": "Ablehnen",
    "Device": "Gerät",
    "It may": "Es darf",
    "Machine": "Rechner",
    "Program": "Programm",
    "Sign in a device": "Ein Gerät anmelden",
    "That code is not waiting for anybody. It may have expired — codes last ten minutes — or been used already.": "Auf diesen Code wartet niemand. Vielleicht ist er abgelaufen — Codes gelten zehn Minuten — oder wurde schon verwendet.",
    "The code the device shows": "Der Code, den das Gerät anzeigt",
    "Your account may not sign devices in. Ask an administrator for the “Use the API” permission.": "Ihr Konto darf keine Geräte anmelden. Bitten Sie einen Administrator um die Berechtigung „Die API verwenden“.",
    "A folder inside another inherits its system prompt where it has none of its own.": "Ein Ordner in einem anderen übernimmt dessen Systemprompt, wenn er keinen eigenen hat.",
    "A folder set to one kind shows on only that side of the sidebar's switch, and opens the new-chat screen already on that fork.": "Ein Ordner, der auf eine Art festgelegt ist, erscheint nur auf dieser Seite des Schalters in der Seitenleiste und öffnet den Bildschirm für neue Chats bereits auf diesem Zweig.",
    "Agent chat": "Agent-Chat",
    "Folder name": "Ordnername",
    "For you, not for any model. It is never sent anywhere.": "Für Sie, nicht für ein Modell. Es wird nirgendwohin gesendet.",
    "Inside": "Innerhalb von",
    "Leave empty to fall through to the model's prompt, then the instance's.": "Leer lassen, um auf den Prompt des Modells und dann den der Instanz zurückzufallen.",
    "No opinion": "Keine Vorgabe",
    "Nothing — a folder at the top": "Nichts — ein Ordner auf oberster Ebene",
    "Precedence, not concatenation: chat, then folder, then model, then instance. The most specific one wins outright.": "Vorrang, nicht Verkettung: Chat, dann Ordner, dann Modell, dann Instanz. Der spezifischste gewinnt vollständig.",
    "Seeds, copied onto a chat when it is created and its own from then on. Anything chosen on the new-chat screen wins over these.": "Startwerte, die beim Erstellen auf einen Chat kopiert werden und ab dann ihm gehören. Was auf dem Bildschirm für neue Chats gewählt wird, hat Vorrang.",
    "Used by every chat in this folder, and by folders nested inside it, unless the chat has a prompt of its own. Read each time a reply is built rather than copied when a chat is made, so editing this reaches the chats already here.": "Wird von jedem Chat in diesem Ordner und in darin verschachtelten Ordnern verwendet, sofern der Chat keinen eigenen Prompt hat. Wird bei jedem Aufbau einer Antwort gelesen statt beim Erstellen eines Chats kopiert, sodass eine Bearbeitung auch die Chats erreicht, die schon hier sind.",
    "What a new chat starts as": "Womit ein neuer Chat beginnt",
    "What this folder is for.": "Wofür dieser Ordner ist.",
    "Library": "Bibliothek",
    "Try again": "Erneut versuchen",
    "Delete chat": "Chat löschen",
    "New reply": "Neue Antwort",
    "Delete folder": "Ordner löschen",
    "Empty": "Leer",
    "Folder settings": "Ordnereinstellungen",
    "New chat here": "Neuer Chat hier",
    "New chat in this folder": "Neuer Chat in diesem Ordner",
    "Rename folder": "Ordner umbenennen",
    "New folder": "Neuer Ordner",
    "Close sidebar": "Seitenleiste schließen",
    "Toggle sidebar": "Seitenleiste umschalten",
    "Which chats to show": "Welche Chats angezeigt werden",
    "Endpoint default": "Standard des Endpunkts",
    "Admin settings": "Administrationseinstellungen",
    "Chats": "Chats",
    "Install as an app": "Als App installieren",
    "Sign out": "Abmelden",
    "At": "Um",
    "Check whether the build is passing and summarise anything that broke since last week.": "Prüfe, ob der Build durchläuft, und fasse alles zusammen, was seit letzter Woche kaputtgegangen ist.",
    "Days of the month, if you want it narrower. Optional.": "Tage im Monat, falls Sie es enger fassen wollen. Optional.",
    "Every so often": "In Abständen",
    "Leave all of them unticked for every day.": "Lassen Sie alle ohne Haken für jeden Tag.",
    "Monday build check": "Build-Prüfung am Montag",
    "Number of runs. Leave it at 0 to keep going until you stop it.": "Anzahl der Läufe. Lassen Sie 0 stehen, damit es weiterläuft, bis Sie es anhalten.",
    "On particular days": "An bestimmten Tagen",
    "On these days": "An diesen Tagen",
    "Once": "Einmal",
    "One or more times, separated by commas. These are wall-clock times: 09:00 stays 09:00 when the clocks change.": "Eine oder mehrere Uhrzeiten, durch Kommas getrennt. Es sind Uhrzeiten nach der Wanduhr: 09:00 bleibt 09:00, wenn die Uhren umgestellt werden.",
    "Only on these dates": "Nur an diesen Daten",
    "Optional. Nothing runs after this date.": "Optional. Nach diesem Datum läuft nichts mehr.",
    "Or stop on": "Oder anhalten am",
    "Repeat every": "Wiederholen alle",
    "Starting": "Beginnt",
    "Stop after": "Anhalten nach",
    "What should happen": "Was geschehen soll",
    "What this appears as in the list.": "Wie es in der Liste erscheint.",
    "Written for a model that will read it with no conversation around it, so say the whole thing. Nobody will be there to answer a question about it.": "Geschrieben für ein Modell, das es ohne umgebendes Gespräch liest — sagen Sie also alles. Niemand wird da sein, um eine Frage dazu zu beantworten.",
    "Run it now without using up the next scheduled run": "Jetzt ausführen, ohne den nächsten geplanten Lauf zu verbrauchen",
    "This chat belonged to a schedule that has been removed. It is kept as a record of what was done.": "Dieser Chat gehörte zu einem Zeitplan, der entfernt wurde. Er bleibt als Aufzeichnung dessen erhalten, was getan wurde.",
    "Nothing scheduled": "Nichts geplant",
    "Set something to run later — a daily summary, a check every Monday morning, a reminder in an hour.": "Planen Sie etwas für später — eine tägliche Zusammenfassung, eine Prüfung jeden Montagmorgen, eine Erinnerung in einer Stunde.",
    "Work that runs on its own, whether or not you are here. Each one has its own chat, and replies into it every time it comes round.": "Arbeit, die von selbst läuft, ob Sie da sind oder nicht. Jeder Zeitplan hat seinen eigenen Chat und antwortet bei jeder Wiederkehr darin.",
    "files a report": "legt einen Bericht ab",
    "set up for you": "für Sie eingerichtet",
    "Every Monday morning, check whether the build is passing and write me a report.": "Prüfe jeden Montagmorgen, ob der Build durchläuft, und schreib mir einen Bericht.",
    "Say it in your own words": "Sagen Sie es in Ihren eigenen Worten",
    "%(name)s loses access at once, and has to log in again.": "%(name)s verliert sofort den Zugriff und muss sich erneut anmelden.",
    "A group of your own, for keeping one provider away from the rest of your data. Nobody else can see it.": "Eine eigene Gruppe, um einen Anbieter vom Rest Ihrer Daten fernzuhalten. Niemand sonst kann sie sehen.",
    "A register, not a character: how answers sound, never what they say.": "Ein Tonfall, keine Rolle: wie Antworten klingen, nie was sie sagen.",
    "A reply, a filed report or a scheduled run arriving while you are looking at something else. In the page always; from the browser as well if you allow it here.": "Eine Antwort, ein abgelegter Bericht oder ein geplanter Lauf, der eintrifft, während Sie sich etwas anderes ansehen. In der Seite immer; vom Browser ebenfalls, wenn Sie es hier erlauben.",
    "API tokens": "API-Tokens",
    "Add one": "Hinzufügen",
    "Available to you": "Für Sie verfügbar",
    "Change password": "Passwort ändern",
    "Checking what this browser allows…": "Prüfe, was dieser Browser erlaubt…",
    "Confirm new password": "Neues Passwort bestätigen",
    "Connect a machine": "Einen Rechner verbinden",
    "Create": "Erstellen",
    "Current password": "Aktuelles Passwort",
    "Custom instructions": "Eigene Anweisungen",
    "Data": "Daten",
    "Default": "Standard",
    "Default model": "Standardmodell",
    "Delete all chats": "Alle Chats löschen",
    "Delete all chats?": "Alle Chats löschen?",
    "Delete this": "Dies löschen",
    "Delete your own data and begin again. None of this can be undone, and nothing here touches anybody else's data. Your account, password and settings stay as they are.": "Löschen Sie Ihre eigenen Daten und beginnen Sie neu. Nichts davon lässt sich rückgängig machen, und nichts hier berührt die Daten anderer. Ihr Konto, Passwort und Ihre Einstellungen bleiben, wie sie sind.",
    "Dictation": "Diktieren",
    "Every chat you own — archived, temporary and scheduled ones too — with its files, and your folders. A schedule that posts into a chat stops, since its chat is gone.": "Jeder Chat, der Ihnen gehört — auch archivierte, temporäre und geplante — mit seinen Dateien, und Ihre Ordner. Ein Zeitplan, der in einen Chat schreibt, hält an, da sein Chat weg ist.",
    "Every other session is signed out when the password changes. You stay signed in here.": "Alle anderen Sitzungen werden abgemeldet, wenn sich das Passwort ändert. Hier bleiben Sie angemeldet.",
    "Expires": "Läuft ab",
    "Follow the instance (%(group)s)": "Der Instanz folgen (%(group)s)",
    "For a script or a program of your own: send one as Authorization: Bearer to /v1 on this instance, an OpenAI-compatible API over your models. The value is shown once, when it is made.": "Für ein Skript oder ein eigenes Programm: Senden Sie es als Authorization: Bearer an /v1 dieser Instanz, eine OpenAI-kompatible API über Ihre Modelle. Der Wert wird einmal angezeigt, beim Erstellen.",
    "Forget": "Vergessen",
    "Forget everything models remember": "Alles vergessen, was sich Modelle merken",
    "Forget everything?": "Alles vergessen?",
    "Forget this": "Dies vergessen",
    "Forget this?": "Dies vergessen?",
    "Full reset": "Vollständiges Zurücksetzen",
    "How you want to be helped, and in what voice. It goes to every model after the system prompt, here and in the LLeMbas CLI signed in as you. Leave both empty to send nothing.": "Wie Ihnen geholfen werden soll und in welchem Ton. Es geht an jedes Modell nach dem Systemprompt, hier und in der LLeMbas CLI, die in Ihrem Namen angemeldet ist. Lassen Sie beides leer, um nichts zu senden.",
    "In 30 days": "In 30 Tagen",
    "In 90 days": "In 90 Tagen",
    "In a year": "In einem Jahr",
    "In my own words": "In meinen eigenen Worten",
    "In the order an administrator arranged them.": "In der Reihenfolge, die ein Administrator festgelegt hat.",
    "Install the LLeMbas CLI on it:": "Installieren Sie die LLeMbas CLI darauf:",
    "Installing needs a secure connection — HTTPS with a certificate this device trusts, or localhost — and some browsers never offer it. On iOS, use Share → Add to Home Screen.": "Die Installation erfordert eine sichere Verbindung — HTTPS mit einem Zertifikat, dem dieses Gerät vertraut, oder localhost —, und manche Browser bieten sie nie an. Unter iOS verwenden Sie Teilen → Zum Home-Bildschirm.",
    "It dials out to this instance; nothing on your machine listens. It decides which directories and modes remote work may use, whatever is asked here.": "Sie verbindet sich von selbst mit dieser Instanz; auf Ihrem Rechner lauscht nichts. Sie entscheidet, welche Verzeichnisse und Modi Fernarbeit nutzen darf, egal was hier verlangt wird.",
    "Kept per browser, because the permission is. Nothing is shown while you are looking at the page — the toast is there for that.": "Pro Browser gespeichert, weil auch die Berechtigung pro Browser gilt. Solange Sie die Seite ansehen, wird nichts angezeigt — dafür gibt es die Einblendung.",
    "Leave empty to use the instance default.": "Leer lassen, um den Standard der Instanz zu verwenden.",
    "Make a token": "Token erstellen",
    "No models are available to you yet.": "Für Sie sind noch keine Modelle verfügbar.",
    "Nothing to delete.": "Nichts zu löschen.",
    "Nothing yet. A model with the memory tool adds to this when you tell it something worth keeping.": "Noch nichts. Ein Modell mit dem Gedächtniswerkzeug ergänzt dies, wenn Sie ihm etwas Merkenswertes sagen.",
    "Notifications": "Benachrichtigungen",
    "Only replies that arrive while you are looking at the chat.": "Nur Antworten, die eintreffen, während Sie den Chat ansehen.",
    "Overrides what the administrator chose, for you only.": "Setzt die Wahl des Administrators außer Kraft, nur für Sie.",
    "Personality": "Persönlichkeit",
    "Prefers metric units and a 24-hour clock.": "Bevorzugt metrische Einheiten und die 24-Stunden-Uhr.",
    "RESET": "LÖSCHEN",
    "Read each reply aloud as it finishes": "Jede Antwort vorlesen, sobald sie fertig ist",
    "Reading replies aloud": "Antworten vorlesen",
    "Reads: %(group)s": "Liest: %(group)s",
    "Remember": "Merken",
    "Runs in its own window, without browser chrome. Everything still comes from your server — there is no offline mode beyond a page saying so.": "Läuft in einem eigenen Fenster, ohne Browser-Bedienelemente. Alles kommt weiterhin von Ihrem Server — einen Offline-Modus gibt es nur in Form einer Seite, die das mitteilt.",
    "Save audio preferences": "Audio-Einstellungen speichern",
    "Saved to this browser and to your account, so it follows you.": "In diesem Browser und in Ihrem Konto gespeichert, sodass es Ihnen folgt.",
    "Session": "Sitzung",
    "Set by the administrator. Changing it for yourself needs the permission to manage your own data groups.": "Vom Administrator festgelegt. Um es für sich selbst zu ändern, brauchen Sie die Berechtigung, Ihre eigenen Datengruppen zu verwalten.",
    "Settings sections": "Bereiche der Einstellungen",
    "Sign a device in": "Ein Gerät anmelden",
    "Sign it in to this instance, and approve the code it shows here:": "Melden Sie es bei dieser Instanz an und bestätigen Sie hier den Code, den es anzeigt:",
    "Sign this device out?": "Dieses Gerät abmelden?",
    "Something worth remembering": "Etwas Merkenswertes",
    "Start over": "Neu anfangen",
    "Start over completely?": "Ganz von vorn anfangen?",
    "That is not a timezone this server knows about.": "Diese Zeitzone kennt der Server nicht.",
    "The code the device shows. You see what it asks for before anything is approved.": "Der Code, den das Gerät anzeigt. Sie sehen, was es verlangt, bevor irgendetwas bestätigt wird.",
    "The interface only. Models answer in the language you write to them in, whatever this says.": "Nur die Oberfläche. Modelle antworten in der Sprache, in der Sie ihnen schreiben, egal was hier steht.",
    "The microphone needs HTTPS or localhost — browsers do not grant it over plain HTTP.": "Das Mikrofon braucht HTTPS oder localhost — über einfaches HTTP geben Browser es nicht frei.",
    "Theme": "Design",
    "This deletes %(what)s. It cannot be undone.": "Dies löscht %(what)s. Es lässt sich nicht rückgängig machen.",
    "Timezone": "Zeitzone",
    "To run agent chats on it from here, allow it in its config (~/.config/lembas/config.yaml) and start the service:": "Um von hier aus Agent-Chats darauf auszuführen, erlauben Sie es in ihrer Konfiguration (~/.config/lembas/config.yaml) und starten Sie den Dienst:",
    "Turn on notifications": "Benachrichtigungen einschalten",
    "Type %(word)s to confirm": "Geben Sie zur Bestätigung %(word)s ein",
    "Use the instance default": "Standard der Instanz verwenden",
    "Used when the personality is “In my own words”.": "Wird verwendet, wenn die Persönlichkeit „In meinen eigenen Worten“ ist.",
    "Voice": "Stimme",
    "What a new chat starts with. Existing chats keep their model.": "Womit ein neuer Chat beginnt. Bestehende Chats behalten ihr Modell.",
    "What should a model know about you, and how should it answer?": "Was sollte ein Modell über Sie wissen, und wie sollte es antworten?",
    "What this memory says": "Was diese Erinnerung besagt",
    "What time a model is told it is, and the zone anything you schedule runs in. Leave it unset to follow the server.": "Welche Uhrzeit einem Modell genannt wird und in welcher Zone alles läuft, was Sie planen. Nicht festgelegt folgt es dem Server.",
    "What you can do": "Was Sie tun können",
    "What you see the interface in. Saved to your account rather than to this browser, so it follows you — and it overrides whatever the instance is set to.": "In welcher Sprache Sie die Oberfläche sehen. In Ihrem Konto gespeichert statt in diesem Browser, sodass es Ihnen folgt — und es hat Vorrang vor der Einstellung der Instanz.",
    "Which group each connection reads": "Welche Gruppe jede Verbindung liest",
    "Your choice applies to you alone. Following the instance keeps up with whatever the administrator sets. Moving a connection moves no data: from then on it reads the other group, and your chats on it that were started in the old group can no longer use it.": "Ihre Wahl gilt nur für Sie. Wer der Instanz folgt, übernimmt, was der Administrator festlegt. Das Verschieben einer Verbindung verschiebt keine Daten: Ab dann liest sie die andere Gruppe, und Ihre Chats darauf, die in der alten Gruppe begonnen wurden, können sie nicht mehr verwenden.",
    "Your data groups": "Ihre Datengruppen",
    "Your memories, and what each model has made of you — the two lists on the Memory tab.": "Ihre Erinnerungen und was jedes Modell aus Ihnen gemacht hat — die beiden Listen im Reiter Gedächtnis.",
    "Your own words": "Ihre eigenen Worte",
    "Your password": "Ihr Passwort",
    "Your settings": "Ihre Einstellungen",
    "Your timezone": "Ihre Zeitzone",
    "chats": "Chats",
    "expired": "abgelaufen",
    "expires %(when)s": "läuft ab %(when)s",
    "folders": "Ordner",
    "last used %(when)s": "zuletzt verwendet %(when)s",
    "memories": "Erinnerungen",
    "messages": "Nachrichten",
    "never used": "nie verwendet",
    "nothing": "nichts",
    "remembered": "gemerkt",
    "revoked": "widerrufen",
    "schedules": "Zeitpläne",
    "yours": "Ihre",
    "Copy it now. It is shown this once and cannot be shown again; if it is lost, revoke it and make another.": "Kopieren Sie es jetzt. Es wird nur dieses eine Mal angezeigt und kann nicht erneut angezeigt werden; geht es verloren, widerrufen Sie es und erstellen Sie ein neues.",
    "Done": "Fertig",
    "Send it as Authorization: Bearer … to /v1 on this instance.": "Senden Sie es als Authorization: Bearer … an /v1 dieser Instanz.",
    "Token": "Token",
    "Your new token": "Ihr neues Token",
    "No expiry": "Unbegrenzt",
    "A panel beside an agent chat holding an interactive shell on the linked device, when the device allows it (remote.terminal: true). What somebody types there is theirs: the modes govern the model, not the person at the keyboard. The model cannot see the panel; sending it something is a button they press.": "Ein Bereich neben einem Agent-Chat mit einer interaktiven Shell auf dem verknüpften Gerät, sofern das Gerät es erlaubt (remote.terminal: true). Was dort jemand eingibt, ist seine Sache: Die Modi gelten für das Modell, nicht für die Person an der Tastatur. Das Modell sieht den Bereich nicht; ihm etwas zu senden, ist ein Knopf, den man drückt.",
    "An agent chat runs on a linked LLeMbas CLI: the CLI on somebody's machine does the work, under its own limits (its remote: roots and modes), and this instance shows it and passes on what is typed here. Nothing runs on this server. What you decide here is whether the feature exists, how long an approval waits, and the terminal panel.": "Ein Agent-Chat läuft auf einem verknüpften LLeMbas CLI: Das CLI auf dem Rechner einer Person erledigt die Arbeit, innerhalb seiner eigenen Grenzen (seiner remote:-Wurzeln und Modi), und diese Instanz zeigt sie an und gibt weiter, was hier eingegeben wird. Auf diesem Server läuft nichts. Hier entscheiden Sie, ob es die Funktion gibt, wie lange eine Freigabe wartet und über den Terminal-Bereich.",
    "People also need the permission “%(permission)s”.": "Außerdem brauchen Personen die Berechtigung „%(permission)s“.",
    "Shells open right now: %(n)s": "Gerade offene Shells: %(n)s",
    "This device's LLeMbas CLI is too old to list its directories. Type the path below.": "Das LLeMbas CLI dieses Geräts ist zu alt, um seine Verzeichnisse aufzulisten. Geben Sie den Pfad unten ein.",
}

# --- 2.2.0: the strings added in 2.2.0 ---------------------------------------------------------
MESSAGES.update(
    {
        "1 model": "1 Modell",
        "%(n)s models": "%(n)s Modelle",
        "Test & refresh": "Testen & aktualisieren",
        "Currently <code>%(masked)s</code>. Leave the dots alone to keep it, or clear the field to remove the key entirely.": "Derzeit <code>%(masked)s</code>. Lassen Sie die Punkte stehen, um ihn zu behalten, oder leeren Sie das Feld, um den Schlüssel ganz zu entfernen.",
        "No key is stored for this endpoint.": "Für diesen Endpunkt ist kein Schlüssel gespeichert.",
        "Only used by image generation's Preserve VRAM, to free this endpoint's memory while ComfyUI works. llama-swap answers <code>GET /unload</code>. Leave it empty for anything on another machine — its memory is not the memory being freed, and it would be an unexplained request.": "Wird nur von „VRAM schonen“ der Bildgenerierung verwendet, um den Speicher dieses Endpunkts freizugeben, während ComfyUI arbeitet. llama-swap beantwortet <code>GET /unload</code>. Leer lassen für alles auf einem anderen Rechner — dessen Speicher ist nicht der, der freigegeben wird, und es wäre eine unerklärliche Anfrage.",
        "One <code>Name: value</code> per line, sent with every request to this endpoint. OpenRouter reads <code>HTTP-Referer</code> and <code>X-Title</code> and attributes your usage with them. Leave it empty unless an endpoint has asked for something.": "Ein <code>Name: value</code> pro Zeile, wird mit jeder Anfrage an diesen Endpunkt gesendet. OpenRouter liest <code>HTTP-Referer</code> und <code>X-Title</code> und ordnet Ihren Verbrauch danach zu. Leer lassen, sofern ein Endpunkt nicht ausdrücklich etwas verlangt.",
        "Last checked %(when)s UTC": "Zuletzt geprüft %(when)s UTC",
        "Never checked": "Nie geprüft",
        "Delete the connection “%(name)s”? Existing chats keep their history.": "Die Verbindung „%(name)s“ löschen? Bestehende Chats behalten ihren Verlauf.",
        "Groups & permissions": "Gruppen & Berechtigungen",
        "1 tool": "1 Werkzeug",
        "%(n)s tools": "%(n)s Werkzeuge",
        "Edit": "Bearbeiten",
        "Put the built-in wording back in this box? Your edit is lost, but nothing is saved until you press Save settings.": "Den eingebauten Wortlaut wieder in dieses Feld setzen? Ihre Änderung geht verloren, gespeichert wird aber erst, wenn Sie auf „Einstellungen speichern“ klicken.",
        "— only appears when %(names)s has something in it.": "— erscheint nur, wenn %(names)s etwas enthält.",
        "Leave the box empty to leave this out of the prompt entirely.": "Lassen Sie das Feld leer, um dies ganz aus dem Prompt wegzulassen.",
        "The preamble is %(chars)s characters and the cap is %(limit)s. Everything past the cap is cut off before it reaches the model.": "Die Präambel hat %(chars)s Zeichen, das Limit liegt bei %(limit)s. Alles über dem Limit wird abgeschnitten, bevor es das Modell erreicht.",
        "%(chars)s of %(limit)s characters before the authored prompt.": "%(chars)s von %(limit)s Zeichen vor dem verfassten Prompt.",
        "The instance prompt is shown below the line; a model or chat prompt would replace it, never stack with it.": "Der Prompt der Instanz steht unter der Linie; ein Modell- oder Chat-Prompt würde ihn ersetzen, nie ergänzen.",
        "No instance prompt is set, so nothing follows the preamble.": "Es ist kein Prompt der Instanz gesetzt, also folgt der Präambel nichts.",
        "%(name)s answered.": "%(name)s hat geantwortet.",
        "People also need the permission “%(permission)s” and a linked LLeMbas CLI (Settings → Devices).": "Außerdem brauchen Personen die Berechtigung „%(permission)s“ und eine verknüpfte LLeMbas CLI (Einstellungen → Geräte).",
        "Seconds with nobody watching <em>and</em> nothing typed. Closing the panel does not end the session — a build carries on and is still there on the way back — so this is what eventually ends one.": "Sekunden, in denen niemand zusieht <em>und</em> nichts getippt wird. Das Schließen des Panels beendet die Sitzung nicht — ein Build läuft weiter und ist beim Zurückkommen noch da —, daher beendet erst dies eine Sitzung irgendwann.",
        "<strong>Asking another model a question uses the same switch and the same allowance below</strong>, because it costs the same thing: one reply setting another reply going. Which people may do it is a separate permission — <strong>%(permission)s</strong> — and which models may is a switch on each model's own page.": "<strong>Einem anderen Modell eine Frage zu stellen nutzt denselben Schalter und dasselbe Kontingent unten</strong>, weil es dasselbe kostet: Eine Antwort setzt eine andere Antwort in Gang. Welche Personen das dürfen, ist eine eigene Berechtigung — <strong>%(permission)s</strong> —, und welche Modelle, ein Schalter auf der Seite jedes Modells.",
        'People also need the <strong>%(permission)s</strong> permission, and the model needs the <strong>%(capability)s</strong> capability. Off by default: a reply that spawns subagents spends model time multiplicatively, and on one local endpoint four at once is four times the queue rather than four times the speed.': 'Außerdem brauchen Personen die Berechtigung <strong>%(permission)s</strong>, und das Modell braucht die Fähigkeit <strong>%(capability)s</strong>. Standardmäßig aus: Eine Antwort, die Subagenten startet, verbraucht Modellzeit vielfach, und auf einem einzigen lokalen Endpunkt bedeuten vier zugleich die vierfache Warteschlange, nicht die vierfache Geschwindigkeit.',
        'Seconds. Past it the subagent is <em>stopped</em>, not abandoned: what it had written is kept and handed back with a note saying it is partial.': 'Sekunden. Danach wird der Subagent <em>angehalten</em>, nicht aufgegeben: Was er geschrieben hatte, bleibt erhalten und wird mit dem Hinweis zurückgegeben, dass es unvollständig ist.',
        "Add and load models": "Modelle hinzufügen und laden",
        "Extraction and search": "Extraktion und Suche",
        "One extension per line, <code>.env</code> or <code>env</code>. Only needed to pick a media type — whether the bytes decode as text is what actually decides, so an unlisted extension already works.": "Eine Endung pro Zeile, <code>.env</code> oder <code>env</code>. Nur nötig, um einen Medientyp zu wählen — ob sich die Bytes als Text dekodieren lassen, entscheidet tatsächlich, also funktioniert eine nicht aufgeführte Endung bereits.",
        "Keyword search finds a document that uses your words. This finds one that means the same thing — <em>\"how do I get in\"</em> reaching a note about passwords. Both run and the two rankings are fused, so nothing that keyword search found is lost.": "Die Stichwortsuche findet ein Dokument, das Ihre Wörter verwendet. Diese findet eines, das dasselbe meint — <em>„wie komme ich rein“</em> trifft eine Notiz über Passwörter. Beide laufen, und die zwei Rangfolgen werden zusammengeführt, sodass nichts verloren geht, was die Stichwortsuche gefunden hat.",
        "No model is marked as producing embeddings. Tick <strong>%(capability)s</strong> on one under <a href=\"/admin/models\">Models</a> — usually a small dedicated model such as <code>nomic-embed-text</code> or <code>bge-m3</code>, not a chat model.": "Kein Modell ist als Embedding-Modell markiert. Setzen Sie bei einem unter <a href=\"/admin/models\">Modelle</a> den Haken <strong>%(capability)s</strong> — meist ein kleines, dafür gemachtes Modell wie <code>nomic-embed-text</code> oder <code>bge-m3</code>, kein Chat-Modell.",
        "<code>%(model)s</code> is selected but is not available — its model or its connection has gone, or it is no longer marked for embeddings. Search has fallen back to keywords.": "<code>%(model)s</code> ist ausgewählt, aber nicht verfügbar — das Modell oder seine Verbindung ist weg, oder es ist nicht mehr für Embeddings markiert. Die Suche ist auf Stichwörter zurückgefallen.",
        "<strong>%(none)s</strong> is not a degraded mode: it is the keyword search this has always had, with nothing stored and nothing sent anywhere. Changing the model changes what a vector means, so anything already indexed is ignored until it is rebuilt.": "<strong>%(none)s</strong> ist kein Notbetrieb: Es ist die Stichwortsuche, die es hier schon immer gab, ohne dass etwas gespeichert oder irgendwohin gesendet wird. Ein Modellwechsel ändert, was ein Vektor bedeutet, daher wird alles bereits Indexierte ignoriert, bis es neu aufgebaut ist.",
        "Instance mode": "Instanzmodus",
        "What this instance is": "Was diese Instanz ist",
        "Full": "Vollständig",
        "API only": "Nur API",
        "This is the interface only. Models answer in whatever language you write to them in, which is a line you can edit under <a href=\"/admin/prompts\">Prompts</a>.": "Das betrifft nur die Oberfläche. Modelle antworten in der Sprache, in der Sie ihnen schreiben — das ist eine Zeile, die Sie unter <a href=\"/admin/prompts\">Prompts</a> bearbeiten können.",
        "Percent of the model's context length. <code>0</code> turns automatic compaction off; the button in each chat still works. Nothing happens for a model whose context length is unset under <a href=\"/admin/models\">Models</a> — that is \"unknown\", not \"small\", and this will not act on a number nobody supplied. The wording of the summary is under <a href=\"/admin/prompts\">Prompts</a>.": "Prozent der Kontextlänge des Modells. <code>0</code> schaltet die automatische Zusammenfassung aus; die Schaltfläche in jedem Chat funktioniert weiterhin. Für ein Modell, dessen Kontextlänge unter <a href=\"/admin/models\">Modelle</a> nicht gesetzt ist, geschieht nichts — das heißt „unbekannt“, nicht „klein“, und hier wird nicht auf Grundlage einer Zahl gehandelt, die niemand angegeben hat. Der Wortlaut der Zusammenfassung steht unter <a href=\"/admin/prompts\">Prompts</a>.",
        "All groups": "Alle Gruppen",
        "A group only ever <em>adds</em>. Anything already in the baseline is shown below as such, so a tick here that changes nothing looks like one.": "Eine Gruppe <em>fügt</em> immer nur hinzu. Was schon in der Grundausstattung enthalten ist, wird unten als solches angezeigt, damit ein Haken, der nichts ändert, auch so aussieht.",
        "Resolved across a person's groups by <strong>maximum</strong> — the union rule applied to numbers, so a second group can only grant more.": "Über die Gruppen einer Person per <strong>Maximum</strong> aufgelöst — die Vereinigungsregel, auf Zahlen angewendet, sodass eine zweite Gruppe nur mehr gewähren kann.",
        "<strong>Leave a box empty</strong> for “no opinion”, and use <strong>0</strong> for “no limit”, which beats any number another group sets. Administrators are unlimited whatever is here.": "<strong>Lassen Sie ein Feld leer</strong> für „keine Vorgabe“, und verwenden Sie <strong>0</strong> für „kein Limit“, was jede Zahl einer anderen Gruppe schlägt. Administratoren sind unbegrenzt, egal was hier steht.",
        "Delete %(name)s? Its members keep their accounts.": "%(name)s löschen? Die Mitglieder behalten ihre Konten.",
        "Delete this group": "Diese Gruppe löschen",
        "Permissions are a <strong>union</strong>: everyone starts with the baseline below, and each group they belong to can add more. A group never takes something away, so being in a second group can only widen what someone can do — which is what keeps “why can this person not do X?” answerable without simulating every group they are in. Administrators bypass all of it.": "Berechtigungen sind eine <strong>Vereinigung</strong>: Jeder beginnt mit der Grundausstattung unten, und jede Gruppe, der er angehört, kann mehr hinzufügen. Eine Gruppe nimmt nie etwas weg, also kann eine zweite Gruppe nur erweitern, was jemand darf — so bleibt „Warum darf diese Person X nicht?“ beantwortbar, ohne jede ihrer Gruppen durchzuspielen. Administratoren umgehen all das.",
        "1 member": "1 Mitglied",
        "%(n)s members": "%(n)s Mitglieder",
        "+1 permission": "+1 Berechtigung",
        "+%(n)s permissions": "+%(n)s Berechtigungen",
        "Create group": "Gruppe erstellen",
        "Currently <code>%(masked)s</code>. Leave the dots alone to keep it, or clear the field to remove it.": "Derzeit <code>%(masked)s</code>. Lassen Sie die Punkte stehen, um ihn zu behalten, oder leeren Sie das Feld, um ihn zu entfernen.",
        "Remote servers speaking the Model Context Protocol over HTTP. Their tools are offered beside the built-in ones to models marked <strong>%(mcp)s</strong>. The list of tools is discovered and cached — press <strong>%(button)s</strong> after adding one, and again whenever the server changes.": "Entfernte Server, die das Model Context Protocol über HTTP sprechen. Ihre Werkzeuge werden neben den eingebauten den Modellen angeboten, die als <strong>%(mcp)s</strong> markiert sind. Die Werkzeugliste wird ermittelt und zwischengespeichert — klicken Sie nach dem Hinzufügen eines Servers auf <strong>%(button)s</strong>, und erneut, wann immer sich der Server ändert.",
        "Add a server": "Server hinzufügen",
        "New server": "Neuer Server",
        "New MCP server": "Neuer MCP-Server",
        "Last contacted unsuccessfully: %(error)s": "Letzter Kontakt fehlgeschlagen: %(error)s",
        "Prefixed onto every tool name this server offers, so that two servers both exposing <code>search</code> do not collide.": "Wird jedem Werkzeugnamen dieses Servers vorangestellt, damit zwei Server, die beide <code>search</code> anbieten, nicht kollidieren.",
        "One <code>Name: value</code> per line.": "Ein <code>Name: value</code> pro Zeile.",
        "Encrypted at rest and never shown again.": "Verschlüsselt gespeichert und nie wieder angezeigt.",
        "%(name)s on the server": "%(name)s auf dem Server",
        "Nothing discovered yet. Save, then press <strong>%(button)s</strong> on the <a href=\"/admin/mcp\">list</a>.": "Noch nichts gefunden. Speichern Sie und klicken Sie dann in der <a href=\"/admin/mcp\">Liste</a> auf <strong>%(button)s</strong>.",
        "Added to the system message whenever any tool from this server is offered. One piece of guidance for the server, not one per tool — the tools carry their own descriptions.": "Wird der Systemnachricht hinzugefügt, sobald irgendein Werkzeug dieses Servers angeboten wird. Ein Hinweis für den ganzen Server, nicht einer pro Werkzeug — die Werkzeuge bringen ihre eigenen Beschreibungen mit.",
        "<strong>Someone has overridden this wording under <a href=\"/admin/prompts\">Prompts</a></strong> — that is what the model sees, not this.": "<strong>Jemand hat diesen Wortlaut unter <a href=\"/admin/prompts\">Prompts</a> überschrieben</strong> — das Modell sieht jenen, nicht diesen.",
        "Tick the second only for a server on your own network. It is what stops this being aimed at %(brand)s itself, a router, or a metadata endpoint.": "Setzen Sie den zweiten Haken nur für einen Server in Ihrem eigenen Netzwerk. Genau das verhindert, dass dies auf %(brand)s selbst, einen Router oder einen Metadaten-Endpunkt gerichtet wird.",
        "No groups yet — <a href=\"/admin/groups\">create one</a> to restrict access.": "Noch keine Gruppen — <a href=\"/admin/groups\">erstellen Sie eine</a>, um den Zugriff zu beschränken.",
        "Add server": "Server hinzufügen",
        "Back to all servers": "Zurück zu allen Servern",
        "Delete the server “%(name)s”? Chats that used its tools keep their transcripts.": "Den Server „%(name)s“ löschen? Chats, die seine Werkzeuge verwendet haben, behalten ihren Verlauf.",
        "%(n)s of %(total)s": "%(n)s von %(total)s",
        "via %(connection)s": "über %(connection)s",
        "default model": "Standardmodell",
        "Make default": "Als Standard festlegen",
        "Upload image": "Bild hochladen",
        "Place in the picker, 1–%(total)s. Typing a number is the workable way to move a model a long distance.": "Platz in der Auswahl, 1–%(total)s. Eine Zahl einzutippen ist der praktikable Weg, ein Modell weit zu verschieben.",
        "Detect from the endpoint": "Vom Endpunkt ermitteln",
        "The vocabulary is <strong>not the same for every model</strong>, and sending one a model does not know is not ignored — it is rendered into the model's chat template, which raises and fails the whole reply. gpt-oss takes <span class=\"mono\">low/medium/high</span>; Bonsai takes <span class=\"mono\">low/medium/xhigh</span> and refuses <span class=\"mono\">high</span>; OpenAI has added <span class=\"mono\">minimal</span>, <span class=\"mono\">xhigh</span> and <span class=\"mono\">max</span> at various points.": "Das Vokabular ist <strong>nicht bei jedem Modell gleich</strong>, und ein Wert, den ein Modell nicht kennt, wird nicht ignoriert — er wird in die Chat-Vorlage des Modells eingesetzt, die dann einen Fehler wirft und die ganze Antwort scheitern lässt. gpt-oss nimmt <span class=\"mono\">low/medium/high</span>; Bonsai nimmt <span class=\"mono\">low/medium/xhigh</span> und lehnt <span class=\"mono\">high</span> ab; OpenAI hat zu verschiedenen Zeitpunkten <span class=\"mono\">minimal</span>, <span class=\"mono\">xhigh</span> und <span class=\"mono\">max</span> hinzugefügt.",
        "Tick none and the common three are offered, which is right for almost everything. If an endpoint ever refuses one anyway, that reply is retried without it and this list corrects itself — so this is worth setting by hand only to save that one round trip.": "Setzen Sie keinen Haken, werden die üblichen drei angeboten, was für fast alles richtig ist. Lehnt ein Endpunkt trotzdem einmal einen ab, wird die Antwort ohne ihn wiederholt, und diese Liste korrigiert sich selbst — von Hand einzustellen lohnt sich also nur, um diesen einen Umweg zu sparen.",
        "A <em>seed</em>, not a per-request setting: it is copied onto a chat when the chat is created and when somebody switches to this model, and from then on the chat's own value is what is sent. Changing it here therefore does nothing to chats that already exist. The composer's picker shows whichever level is actually in force, so what somebody sees there is what goes out. Anyone can change it per chat with <span class=\"mono\">/effort</span>, and the control only appears on a model marked <strong>Reasoning</strong> above.": "Ein <em>Startwert</em>, keine Einstellung pro Anfrage: Er wird auf einen Chat kopiert, wenn der Chat erstellt wird und wenn jemand zu diesem Modell wechselt, und ab dann wird der eigene Wert des Chats gesendet. Eine Änderung hier wirkt sich daher nicht auf bestehende Chats aus. Die Auswahl im Eingabefeld zeigt die tatsächlich geltende Stufe, sodass das, was jemand dort sieht, auch gesendet wird. Jeder kann sie pro Chat mit <span class=\"mono\">/effort</span> ändern, und das Bedienelement erscheint nur bei einem Modell, das oben als <strong>Denken</strong> markiert ist.",
        "Shown in the chat settings panel and your users' settings — and, if any model here may ask another one a question, read by those models too.": "Wird im Panel der Chat-Einstellungen und in den Einstellungen Ihrer Benutzer angezeigt — und, falls ein Modell hier einem anderen eine Frage stellen darf, auch von diesen Modellen gelesen.",
        "No instance prompt is set.": "Es ist kein Prompt der Instanz gesetzt.",
        "Leave empty to fall back to the instance prompt shown above.": "Leer lassen, um auf den oben gezeigten Prompt der Instanz zurückzufallen.",
        "Leave empty to send no system prompt.": "Leer lassen, um keinen Systemprompt zu senden.",
        "Built-in tools": "Eingebaute Werkzeuge",
        "What this model is given, as opposed to what it is capable of. None of it applies unless <strong>tools</strong> is ticked above. Each one is also subject to the instance being configured for it and to the reader's own permissions — this only decides whether it is offered to <em>this</em> model.": "Was dieses Modell bekommt, im Unterschied zu dem, was es kann. Nichts davon gilt, solange oben <strong>tools</strong> nicht angehakt ist. Jedes hängt außerdem davon ab, dass die Instanz dafür eingerichtet ist, und von den Berechtigungen des Lesers — hier wird nur entschieden, ob es <em>diesem</em> Modell angeboten wird.",
        "Back to all models": "Zurück zu allen Modellen",
        "No models yet. <a href=\"/admin/connections\">Add a connection</a> and run “%(button)s”.": "Noch keine Modelle. <a href=\"/admin/connections\">Fügen Sie eine Verbindung hinzu</a> und führen Sie „%(button)s“ aus.",
        "Clear": "Leeren",
        "Select %(name)s": "%(name)s auswählen",
        "Showing %(first)s–%(last)s of %(matched)s": "%(first)s–%(last)s von %(matched)s",
        "(filtered from %(total)s)": "(gefiltert aus %(total)s)",
        "Page %(page)s of %(pages)s": "Seite %(page)s von %(pages)s",
        "Everything %(brand)s puts in front of a model on its own: what day it is, how to use each tool, what it has been asked to remember. These sit above whichever system prompt was authored for the instance, the model or the chat — that prompt still wins where the two disagree. Clear a box to leave that piece out altogether; press <strong>%(use_default)s</strong> to put the built-in wording back.": "Alles, was %(brand)s einem Modell von sich aus voranstellt: welcher Tag ist, wie jedes Werkzeug zu verwenden ist, was es sich merken soll. Das steht über dem Systemprompt, der für die Instanz, das Modell oder den Chat verfasst wurde — dieser Prompt hat weiterhin Vorrang, wo sich beide widersprechen. Leeren Sie ein Feld, um diesen Teil ganz wegzulassen; klicken Sie auf <strong>%(use_default)s</strong>, um den eingebauten Wortlaut wiederherzustellen.",
        "Everything above is cut off past this. <code>0</code> means the built-in %(default)s. It is a backstop against a large skill index or memory list quietly eating the context window, not a budget to tune.": "Alles oben wird ab hier abgeschnitten. <code>0</code> bedeutet den eingebauten Wert %(default)s. Es ist eine Sicherung dagegen, dass ein großer Fertigkeiten-Index oder eine lange Liste von Erinnerungen still das Kontextfenster auffrisst, kein Budget zum Feintunen.",
        "Write these in double braces. Anything in double braces that is not on this list is left exactly as you typed it — variable names are lowercase letters, digits and underscores, so <code>{\"total\": 1}</code> and <code>${PATH}</code> are never mistaken for one. A variable with nothing in it takes its whole line with it, so a section never appears empty.": "Schreiben Sie diese in doppelte geschweifte Klammern. Alles in doppelten Klammern, was nicht auf dieser Liste steht, bleibt genau so, wie Sie es getippt haben — Variablennamen bestehen aus Kleinbuchstaben, Ziffern und Unterstrichen, sodass <code>{\"total\": 1}</code> und <code>${PATH}</code> nie für eine gehalten werden. Eine leere Variable nimmt ihre ganze Zeile mit, sodass ein Abschnitt nie leer erscheint.",
        "Only reaches the prompt with <code>agent</code> ticked below. The machine, directory and plan shown there are samples.": "Erreicht den Prompt nur, wenn unten <code>agent</code> angehakt ist. Rechner, Verzeichnis und Plan dort sind Beispiele.",
        "Refresh": "Aktualisieren",
        "A <strong>schedule</strong> runs a piece of work because time has passed rather than because somebody asked just now — a daily summary, a check every Monday, a reminder in an hour. Each one has its own chat and replies into it, or files a report. People make their own under <strong>%(scheduled)s</strong>; what you decide here is whether the feature exists and what it may spend.": "Ein <strong>Zeitplan</strong> führt eine Arbeit aus, weil Zeit vergangen ist, nicht weil gerade jemand gefragt hat — eine tägliche Zusammenfassung, eine Prüfung jeden Montag, eine Erinnerung in einer Stunde. Jeder hat seinen eigenen Chat und antwortet darin oder legt einen Bericht ab. Personen erstellen ihre eigenen unter <strong>%(scheduled)s</strong>; hier entscheiden Sie, ob es die Funktion gibt und was sie verbrauchen darf.",
        "This is the one thing here that spends model time with nobody watching. A schedule pointed at a chat that can run commands would run them unattended, so a scheduled task is never an agent chat — but everything else a model can reach, it can reach on a timer. Give <strong>%(permission)s</strong> under %(groups)s to the people who should have it; it is off for everybody by default.": "Das ist das Einzige hier, was Modellzeit verbraucht, während niemand zusieht. Ein Zeitplan, der auf einen Chat zielt, der Befehle ausführen kann, würde sie unbeaufsichtigt ausführen, daher ist eine geplante Aufgabe nie ein Agent-Chat — aber alles andere, was ein Modell erreichen kann, erreicht es auch per Zeitschaltuhr. Geben Sie <strong>%(permission)s</strong> unter %(groups)s den Personen, die es haben sollen; standardmäßig ist es für alle aus.",
        "Off by default. Turning this off stops everything firing and deletes nothing — schedules keep their place and resume when it is turned back on.": "Standardmäßig aus. Das Ausschalten stoppt alle Ausführungen und löscht nichts — Zeitpläne behalten ihren Platz und laufen weiter, sobald es wieder eingeschaltet wird.",
        "There is 1 schedule on this instance, %(active)s of them not paused.": "Auf dieser Instanz gibt es 1 Zeitplan, davon %(active)s nicht pausiert.",
        "There are %(total)s schedules on this instance, %(active)s of them not paused.": "Auf dieser Instanz gibt es %(total)s Zeitpläne, davon %(active)s nicht pausiert.",
        "Who may use it is a permission — <code>tools.web_search</code> under %(groups)s.": "Wer es verwenden darf, ist eine Berechtigung — <code>tools.web_search</code> unter %(groups)s.",
        "<code>wt-wt</code> is no region at all. <code>uk-en</code>, <code>de-de</code> and so on bias results to a country.": "<code>wt-wt</code> ist gar keine Region. <code>uk-en</code>, <code>de-de</code> und so weiter gewichten die Ergebnisse zugunsten eines Landes.",
        "A stock SearXNG refuses JSON. Add <code>- json</code> under <code>search.formats</code> in its <code>settings.yml</code> and restart it, or every search will fail with that message.": "Ein unverändertes SearXNG lehnt JSON ab. Fügen Sie <code>- json</code> unter <code>search.formats</code> in seiner <code>settings.yml</code> hinzu und starten Sie es neu, sonst scheitert jede Suche mit dieser Meldung.",
        "Applies to the composer's <strong>%(link)s</strong> option and to anything the model fetches: %(brand)s retrieves the page and keeps its text.": "Gilt für die Option <strong>%(link)s</strong> im Eingabefeld und für alles, was das Modell abruft: %(brand)s holt die Seite und bewahrt ihren Text auf.",
        "Offers the <code>web_fetch</code> tool, so a model can read an address it found in a search result or was given. Turning this off leaves the composer's Link option working: that one is somebody's instruction, while this is the model choosing an address — possibly one it read in a page it had just fetched.": "Bietet das Werkzeug <code>web_fetch</code> an, damit ein Modell eine Adresse lesen kann, die es in einem Suchergebnis gefunden oder bekommen hat. Ist dies aus, funktioniert die Option „Link“ im Eingabefeld weiterhin: Die ist die Anweisung eines Menschen, während hier das Modell eine Adresse wählt — womöglich eine, die es in einer gerade abgerufenen Seite gelesen hat.",
        "Off by default, and worth leaving off. This server can reach your router, your other services and %(brand)s itself; the address to fetch can come from a model, which can be talked into things by a web page it just read. Turn this on only if you actually want to archive pages from your own network.": "Standardmäßig aus, und besser so. Dieser Server kann Ihren Router, Ihre anderen Dienste und %(brand)s selbst erreichen; die abzurufende Adresse kann von einem Modell stammen, dem eine gerade gelesene Webseite etwas einreden kann. Schalten Sie dies nur ein, wenn Sie wirklich Seiten aus Ihrem eigenen Netzwerk archivieren wollen.",
        "Runs a real search against the <em>saved</em> settings, which is what a chat would do. Save first if you have just changed something.": "Führt eine echte Suche mit den <em>gespeicherten</em> Einstellungen aus, genau wie ein Chat es täte. Speichern Sie zuerst, wenn Sie gerade etwas geändert haben.",
        "Search": "Suchen",
        "Cards on the new-chat screen. Clicking one sends its prompt straight away, so each has to work on its own — write one that asks for whatever it needs, and the first reply becomes the right question rather than a guess. The first %(n)s enabled ones are shown, in this order.": "Karten auf dem Bildschirm für neue Chats. Ein Klick sendet ihren Prompt sofort, also muss jede für sich funktionieren — schreiben Sie eine, die nach allem fragt, was sie braucht, dann wird die erste Antwort die richtige Frage statt einer Vermutung. Die ersten %(n)s aktivierten werden angezeigt, in dieser Reihenfolge.",
        "Only the first %(n)s are shown": "Nur die ersten %(n)s werden angezeigt",
        "Delete the suggestion “%(name)s”?": "Den Vorschlag „%(name)s“ löschen?",
        "%(n)s is the limit. Delete one to add another — past a dozen this is a menu, and a menu on the empty screen is a worse blank page than a blank page.": "%(n)s ist das Limit. Löschen Sie einen, um einen weiteren hinzuzufügen — ab einem Dutzend ist das ein Menü, und ein Menü auf dem leeren Bildschirm ist eine schlimmere leere Seite als eine leere Seite.",
        "New tool": "Neues Werkzeug",
        "The name the model calls, and the key its guidance is stored under. Lowercase letters, digits, hyphens and underscores.": "Der Name, den das Modell aufruft, und der Schlüssel, unter dem seine Hinweise gespeichert sind. Kleinbuchstaben, Ziffern, Bindestriche und Unterstriche.",
        "Changing it starts its guidance afresh.": "Eine Änderung setzt seine Hinweise neu auf.",
        "A JSON Schema object. Each property becomes a <code>{{name}}</code> you can use below, and a <code>description</code> on each one is worth writing — the model reads those too.": "Ein JSON-Schema-Objekt. Jede Eigenschaft wird zu einem <code>{{name}}</code>, das Sie unten verwenden können, und eine <code>description</code> bei jeder lohnt sich — das Modell liest auch diese.",
        "One <code>Name: value</code> per line. Placeholders work here too.": "Ein <code>Name: value</code> pro Zeile. Platzhalter funktionieren auch hier.",
        "Encrypted at rest and never shown again. It is dropped if the endpoint redirects to another host.": "Verschlüsselt gespeichert und nie wieder angezeigt. Er wird verworfen, wenn der Endpunkt auf einen anderen Host weiterleitet.",
        "Added to the system message whenever this tool is offered, and nowhere else. Start a line with <code>- </code> and it joins the list of tool instructions. Optional: the description above is what makes the tool usable, this is for habits.": "Wird der Systemnachricht hinzugefügt, wann immer dieses Werkzeug angeboten wird, und nirgends sonst. Beginnen Sie eine Zeile mit <code>- </code>, und sie kommt in die Liste der Werkzeuganweisungen. Optional: Die Beschreibung oben macht das Werkzeug nutzbar, dies hier ist für Gewohnheiten.",
        "Create tool": "Werkzeug erstellen",
        "Back to all tools": "Zurück zu allen Werkzeugen",
        "Delete the tool “%(name)s”? Chats that used it keep their transcripts.": "Das Werkzeug „%(name)s“ löschen? Chats, die es verwendet haben, behalten ihren Verlauf.",
        "Calls the <em>saved</em> tool once, so what you see here is what a chat would get. Nothing is sent to a model.": "Ruft das <em>gespeicherte</em> Werkzeug einmal auf, sodass Sie hier sehen, was ein Chat bekäme. An ein Modell wird nichts gesendet.",
        "Run once": "Einmal ausführen",
        "HTTP calls a model can make while it answers. Each one is offered to models marked <strong>%(custom)s</strong>, to people who have the permission, and — if it is restricted — only to the groups you choose. The model decides <em>when</em> to call it from the description you write, so write that as if explaining to a colleague what the tool is for.": "HTTP-Aufrufe, die ein Modell beim Antworten machen kann. Jeder wird Modellen angeboten, die als <strong>%(custom)s</strong> markiert sind, Personen mit der Berechtigung und — falls er beschränkt ist — nur den Gruppen, die Sie wählen. <em>Wann</em> das Modell ihn aufruft, entscheidet es anhand Ihrer Beschreibung, also schreiben Sie sie so, als erklärten Sie einem Kollegen, wofür das Werkzeug da ist.",
        "No tools yet. <a href=\"/admin/tools/new\">Define one</a> — a name, a description, and a URL with <code>{{placeholders}}</code> in it.": "Noch keine Werkzeuge. <a href=\"/admin/tools/new\">Definieren Sie eines</a> — ein Name, eine Beschreibung und eine URL mit <code>{{placeholders}}</code> darin.",
        "This host follows the <strong>stable</strong> channel — the newest release tag.": "Dieser Host folgt dem Kanal <strong>stable</strong> — dem neuesten Release-Tag.",
        "This host follows the <strong>edge</strong> channel — the tip of <code>%(branch)s</code>, which is whatever was pushed most recently and may be half finished.": "Dieser Host folgt dem Kanal <strong>edge</strong> — der Spitze von <code>%(branch)s</code>, also dem, was zuletzt gepusht wurde und halb fertig sein kann.",
        "Checking reaches the remote; opening this page does not.": "Das Prüfen kontaktiert die Gegenstelle; das Öffnen dieser Seite nicht.",
        "reports %(version)s": "meldet %(version)s",
        "This is tagged <strong>%(tag)s</strong> but reports version <strong>%(version)s</strong>. A tag cut before the version bump names a release nobody can identify afterwards.": "Dies ist als <strong>%(tag)s</strong> getaggt, meldet aber die Version <strong>%(version)s</strong>. Ein Tag, das vor dem Versionssprung gesetzt wurde, benennt ein Release, das danach niemand mehr zuordnen kann.",
        "Check the remote": "Gegenstelle prüfen",
        "Available": "Verfügbar",
        "1 commit behind": "1 Commit zurück",
        "%(n)s commits behind": "%(n)s Commits zurück",
        "Nothing on <code>%(branch)s</code> has been tagged as a release yet, so the stable channel has nothing to offer. Switch this host to <code>edge</code> in <code>lembas.env</code> to follow the branch instead.": "Auf <code>%(branch)s</code> wurde noch nichts als Release getaggt, also hat der Kanal stable nichts anzubieten. Stellen Sie diesen Host in <code>lembas.env</code> auf <code>edge</code> um, um stattdessen dem Branch zu folgen.",
        "<strong>%(label)s</strong> is what is running.": "<strong>%(label)s</strong> läuft gerade.",
        "1 commit between": "1 Commit dazwischen",
        "%(n)s commits between": "%(n)s Commits dazwischen",
        "This page follows <strong>%(channel)s</strong> but the update helper on this host was installed for <strong>%(helper)s</strong> — so the button would deploy the second, not the first. They are set in two places (<code>lembas.env</code> and the systemd unit); re-run the installer with <code>INSTALL_UPDATE_HELPER=1</code> and the channel you want, which writes both.": "Diese Seite folgt <strong>%(channel)s</strong>, aber der Update-Helfer auf diesem Host wurde für <strong>%(helper)s</strong> installiert — die Schaltfläche würde also den zweiten ausrollen, nicht den ersten. Beides wird an zwei Stellen festgelegt (<code>lembas.env</code> und die systemd-Unit); führen Sie das Installationsprogramm erneut mit <code>INSTALL_UPDATE_HELPER=1</code> und dem gewünschten Kanal aus, dann schreibt es beides.",
        "This host has the update helper installed, so the button below writes a request that a systemd unit picks up and runs as root. It always deploys the <strong>%(channel)s</strong> channel — the request carries no ref and no channel, so pressing it is never “deploy something else”.": "Auf diesem Host ist der Update-Helfer installiert, also schreibt die Schaltfläche unten eine Anforderung, die eine systemd-Unit aufgreift und als root ausführt. Sie rollt immer den Kanal <strong>%(channel)s</strong> aus — die Anforderung trägt weder Ref noch Kanal, also bedeutet ein Klick nie „etwas anderes ausrollen“.",
        "The update helper is <strong>not installed on this host</strong>, so there is no button. That is the honest default: the service runs as an unprivileged account and cannot restart itself, and a web application that <em>can</em> is one whose worst day is much worse. Run this instead: <code>%(command)s</code>": "Der Update-Helfer ist <strong>auf diesem Host nicht installiert</strong>, also gibt es keine Schaltfläche. Das ist der ehrliche Standard: Der Dienst läuft unter einem unprivilegierten Konto und kann sich nicht selbst neu starten, und eine Webanwendung, die das <em>kann</em>, hat einen viel schlimmeren schlechtesten Tag. Führen Sie stattdessen dies aus: <code>%(command)s</code>",
        "To install it, re-run the installer with <code>INSTALL_UPDATE_HELPER=1</code>. Doing so means anybody who can administer this web interface can deploy the <code>%(channel)s</code> channel — which is the point, and worth deciding on purpose rather than arriving at.": "Zum Installieren führen Sie das Installationsprogramm erneut mit <code>INSTALL_UPDATE_HELPER=1</code> aus. Dann kann jeder, der diese Weboberfläche administrieren darf, den Kanal <code>%(channel)s</code> ausrollen — genau darum geht es, und das sollte man bewusst entscheiden, statt einfach dort zu landen.",
        "All users": "Alle Benutzer",
        "Read-only, and deliberately: every switch here is set somewhere else — in the <a href=\"/admin/groups\">baseline</a> or in a named group — and a control on this page would be a third place to change one thing. What it adds is the <em>source</em>, which is the question the grids could not answer without opening every group by eye.": "Schreibgeschützt, und zwar mit Absicht: Jeder Schalter hier wird anderswo gesetzt — in der <a href=\"/admin/groups\">Grundausstattung</a> oder in einer benannten Gruppe —, und ein Bedienelement auf dieser Seite wäre ein dritter Ort, um dasselbe zu ändern. Was sie hinzufügt, ist die <em>Herkunft</em> — die Frage, die die Raster nicht beantworten konnten, ohne jede Gruppe einzeln durchzusehen.",
        "being an administrator": "Administratorrolle",
        "the baseline": "Grundausstattung",
        "from %(sources)s": "Quelle: %(sources)s",
        "%(prompt)s prompt, %(written)s written": "%(prompt)s Prompt, %(written)s geschrieben",
        "%(month)s this month, %(today)s today": "%(month)s diesen Monat, %(today)s heute",
        "limit %(n)s a day": "Limit %(n)s pro Tag",
        "Resolved across their groups by <strong>maximum</strong> — the union rule applied to numbers, so a second group can only ever grant more. Zero means no limit and wins outright, because a group saying “unlimited” must not count for less than one saying “a million”.": "Über ihre Gruppen per <strong>Maximum</strong> aufgelöst — die Vereinigungsregel, auf Zahlen angewendet, sodass eine zweite Gruppe immer nur mehr gewähren kann. Null bedeutet kein Limit und gewinnt immer, denn eine Gruppe, die „unbegrenzt“ sagt, darf nicht weniger zählen als eine, die „eine Million“ sagt.",
        "no limit": "kein Limit",
        "Delete this account": "Dieses Konto löschen",
        "Every account on this instance. Open one to see what it can actually do and where each of those permissions came from. Group membership is edited from the <a href=\"/admin/groups\">group's</a> own page — one control per value, so a save on one screen cannot undo a save on another.": "Jedes Konto auf dieser Instanz. Öffnen Sie eines, um zu sehen, was es tatsächlich darf und woher jede dieser Berechtigungen stammt. Die Gruppenmitgliedschaft wird auf der Seite der <a href=\"/admin/groups\">Gruppe</a> bearbeitet — ein Bedienelement pro Wert, damit ein Speichern auf einem Bildschirm kein Speichern auf einem anderen rückgängig machen kann.",
        "Nobody matches that.": "Niemand passt dazu.",
        "No accounts yet.": "Noch keine Konten.",
        "%(n)s accounts": "%(n)s Konten",
        "Create account": "Konto erstellen",
        "New workflow": "Neuer Workflow",
        "The only thing the model has to choose with, so say what this is <em>for</em> rather than what it contains. It never sees the graph.": "Das Einzige, womit das Modell wählen kann, also sagen Sie, <em>wofür</em> er ist, nicht was er enthält. Den Graphen sieht es nie.",
        "Export this from ComfyUI with <strong>Export (API)</strong>, not Save — the two formats are different and only the API one can be submitted.": "Exportieren Sie dies aus ComfyUI mit <strong>Export (API)</strong>, nicht mit Speichern — die beiden Formate unterscheiden sich, und nur das API-Format kann übermittelt werden.",
        "Then put a placeholder where each value goes. A placeholder that is the <em>whole</em> value keeps its type, so <code>\"steps\": {{steps}}</code> sends the number 20 rather than the text “20”; one inside a longer string is substituted as text, so <code>\"{{prompt}}, masterpiece\"</code> works. Anything you leave out takes its default.": "Setzen Sie dann einen Platzhalter dort, wo jeder Wert hingehört. Ein Platzhalter, der den <em>ganzen</em> Wert bildet, behält seinen Typ, sodass <code>\"steps\": {{steps}}</code> die Zahl 20 sendet statt des Texts „20“; einer innerhalb einer längeren Zeichenkette wird als Text eingesetzt, also funktioniert <code>\"{{prompt}}, masterpiece\"</code>. Was Sie weglassen, nimmt seinen Standardwert.",
        "Every hole a template may carry, what it fills, and what it resolves to right now. <code>{{prompt}}</code> is required — without it every image would be the same one, whatever anybody typed.": "Jede Lücke, die eine Vorlage haben kann, was sie füllt und was sie gerade ergibt. <code>{{prompt}}</code> ist Pflicht — ohne ihn wäre jedes Bild dasselbe, egal was jemand eingibt.",
        "Two of these are not spelled the way ComfyUI spells them, which is the mistake that costs an afternoon: <code>{{model}}</code> fills <code>ckpt_name</code> and <code>{{sampler}}</code> fills <code>sampler_name</code>.": "Zwei davon werden nicht so geschrieben wie in ComfyUI, und genau dieser Fehler kostet einen Nachmittag: <code>{{model}}</code> füllt <code>ckpt_name</code> und <code>{{sampler}}</code> füllt <code>sampler_name</code>.",
        "The right-hand column is what an omitted placeholder resolves to today — the instance defaults from the image settings page, falling back to the built-in floor. A model may override any of them except <code>{{batch}}</code>, which is yours alone.": "Die rechte Spalte zeigt, was ein weggelassener Platzhalter heute ergibt — die Standards der Instanz von der Seite der Bildeinstellungen, ersatzweise die eingebaute Untergrenze. Ein Modell kann jeden davon überschreiben außer <code>{{batch}}</code>, der allein Ihnen gehört.",
        "Add workflow": "Workflow hinzufügen",
        "Back": "Zurück",
        "Delete the workflow “%(name)s”? Chats that used it fall back to the default.": "Den Workflow „%(name)s“ löschen? Chats, die ihn verwendet haben, fallen auf den Standard zurück.",
        "Set up %(name)s": "%(name)s einrichten",
        "Create administrator account": "Administratorkonto erstellen",
        "%(name)s — a web UI for your language models.": "%(name)s — eine Weboberfläche für Ihre Sprachmodelle.",
        "%(n)s page": "%(n)s Seite",
        "%(n)s pages": "%(n)s Seiten",
        "truncated": "gekürzt",
        "Remove %(name)s": "%(name)s entfernen",
        "On the device": "Auf dem Gerät",
        "Send a message…": "Nachricht schreiben…",
        "Ask anything…": "Fragen Sie etwas…",
        "Effort and subagents": "Aufwand und Subagenten",
        "Tools and skills": "Werkzeuge und Fertigkeiten",
        "Waiting for you to confirm it on your phone…": "Warte auf Ihre Bestätigung auf dem Telefon…",
        "Confirm it on your phone, in the app. It is not done until you do.": "Bestätigen Sie es auf Ihrem Telefon, in der App. Bis dahin geschieht nichts.",
        "Decline": "Ablehnen",
        "What to draw…": "Was gezeichnet werden soll…",
        "The reply to the next message is a picture drawn from it.": "Die Antwort auf die nächste Nachricht ist ein danach gezeichnetes Bild.",
        "Enter to send, Shift+Enter for a new line. %(commands)s, @ for files.": "Enter zum Senden, Shift+Enter für eine neue Zeile. %(commands)s, @ für Dateien.",
        "Drag files in, or paste an image.": "Dateien hineinziehen oder ein Bild einfügen.",
        "<strong>%(model)s has no vision</strong>, so images will not be sent — documents still will.": "<strong>%(model)s sieht keine Bilder</strong>, daher werden Bilder nicht gesendet — Dokumente schon.",
        "Switched on the device; this chat sends no model while it runs on this one": "Auf dem Gerät umgeschaltet; dieser Chat sendet kein Modell, solange er auf diesem läuft",
        "on the device: %(ref)s": "auf dem Gerät: %(ref)s",
        "Rewind here? The %(n)s message after this one will be deleted.": "Hierher zurückspulen? Die %(n)s Nachricht danach wird gelöscht.",
        "Rewind here? The %(n)s messages after this one will be deleted.": "Hierher zurückspulen? Die %(n)s Nachrichten danach werden gelöscht.",
        "Send again": "Erneut senden",
        "%(n)s later message will be discarded.": "%(n)s spätere Nachricht wird verworfen.",
        "%(n)s later messages will be discarded.": "%(n)s spätere Nachrichten werden verworfen.",
        "Inspector": "Inspektor",
        "Context": "Kontext",
        "%(n)s tokens": "%(n)s Tokens",
        "not set — <a href=\"/admin/models/%(id)s/edit\">set it</a>": "nicht gesetzt — <a href=\"/admin/models/%(id)s/edit\">festlegen</a>",
        "not set": "nicht gesetzt",
        "Lifetime": "Lebensdauer",
        "temporary": "temporär",
        "%(sent)s in, %(received)s out": "%(sent)s hinein, %(received)s heraus",
        "not reported": "nicht gemeldet",
        "Elapsed": "Dauer",
        "Tool calls": "Werkzeugaufrufe",
        "Rounds": "Runden",
        "State": "Status",
        "It says:": "Es sagt:",
        "recommended": "empfohlen",
        "Answer": "Antworten",
        "Send answers": "Antworten senden",
        "All of them at once. Leave any blank to skip it.": "Alle auf einmal. Lassen Sie eine leer, um sie zu überspringen.",
        "Leave any blank to skip it.": "Lassen Sie eine leer, um sie zu überspringen.",
        "The plan": "Der Plan",
        "How to carry it out": "Wie er ausgeführt werden soll",
        "Approve, and let it edit": "Bestätigen und bearbeiten lassen",
        "Edit mode: it asks only before running commands.": "Modus Edit: Es fragt nur vor dem Ausführen von Befehlen.",
        "Approve, and ask me each time": "Bestätigen und mich jedes Mal fragen",
        "Manual mode: every change waits for you.": "Modus Manual: Jede Änderung wartet auf Sie.",
        "Revise": "Überarbeiten",
        "What to change": "Was geändert werden soll",
        "What to change in the plan…": "Was am Plan geändert werden soll…",
        "Send it back": "Zurückschicken",
        "It says: %(purpose)s": "Es sagt: %(purpose)s",
        "Nothing matches “%(q)s”.": "Nichts passt zu „%(q)s“.",
        "Nothing to mention yet.": "Noch nichts zum Erwähnen.",
        "view extracted text": "extrahierten Text anzeigen",
        "Attached in the terminal; the file is on the device": "Im Terminal angehängt; die Datei liegt auf dem Gerät",
        "%(kb)s KB": "%(kb)s KB",
        "on the device": "auf dem Gerät",
        "Waiting to be sent": "Wartet auf das Senden",
        "Estimated.": "Geschätzt.",
        "What the conversation now occupies: %(n)s of %(limit)s tokens (%(percent)s%%)": "Was das Gespräch jetzt belegt: %(n)s von %(limit)s Tokens (%(percent)s%%)",
        "What the conversation now occupies: %(n)s tokens; the window's size is not known": "Was das Gespräch jetzt belegt: %(n)s Tokens; die Fenstergröße ist unbekannt",
        "in context": "im Kontext",
        "Estimated: this endpoint reports no token counts.": "Geschätzt: Dieser Endpunkt meldet keine Token-Zahlen.",
        "What this reply cost: %(sent)s in, %(received)s out, the prompt paid for once in each of %(n)s rounds of tool calls": "Was diese Antwort gekostet hat: %(sent)s hinein, %(received)s heraus, der Prompt einmal bezahlt in jeder von %(n)s Runden mit Werkzeugaufrufen",
        "What this reply cost: %(sent)s in, %(received)s out": "Was diese Antwort gekostet hat: %(sent)s hinein, %(received)s heraus",
        "%(n)s processed": "%(n)s verarbeitet",
        "The reply took %(total)s s, %(thinking)s s of it thinking": "Die Antwort dauerte %(total)s s, davon %(thinking)s s Nachdenken",
        "The reply took %(total)s s": "Die Antwort dauerte %(total)s s",
        "thought %(time)s": "Denken %(time)s",
        "%(n)s tokens in %(ms)s ms": "%(n)s Tokens in %(ms)s ms",
        "Switch this chat to Edit mode and carry out this plan? Every command will still be shown to you before it runs.": "Diesen Chat in den Modus Edit schalten und diesen Plan ausführen? Jeder Befehl wird Ihnen trotzdem vor dem Ausführen gezeigt.",
        "Carry out this plan": "Diesen Plan ausführen",
        "Close chat settings": "Chat-Einstellungen schließen",
        "Switched off here only. What is on to begin with is your default for this model, in Settings → Models.": "Nur hier ausgeschaltet. Was anfangs an ist, ist Ihr Standard für dieses Modell, unter Einstellungen → Modelle.",
        "Instructions that apply to every message in this chat.": "Anweisungen, die für jede Nachricht in diesem Chat gelten.",
        "Leave empty to use the folder prompt shown above.": "Leer lassen, um den oben gezeigten Prompt des Ordners zu verwenden.",
        "Leave empty to use the model prompt shown above.": "Leer lassen, um den oben gezeigten Prompt des Modells zu verwenden.",
        "Leave empty to use the instance prompt shown above.": "Leer lassen, um den oben gezeigten Prompt der Instanz zu verwenden.",
        "Overrides the model and instance prompts for this chat only.": "Hat nur für diesen Chat Vorrang vor den Prompts des Modells und der Instanz.",
        "Your defaults per model": "Ihre Standards pro Modell",
        "Thought %(label)s": "Nachgedacht %(label)s",
        "Thought": "Nachgedacht",
        "Nothing has been run in this shell yet.": "In dieser Shell wurde noch nichts ausgeführt.",
        "The previous command (Ctrl+Shift+↑)": "Vorheriger Befehl (Ctrl+Shift+↑)",
        "The previous command": "Vorheriger Befehl",
        "The next command (Ctrl+Shift+↓)": "Nächster Befehl (Ctrl+Shift+↓)",
        "The next command": "Nächster Befehl",
        "%(n)s earlier messages, summarised": "%(n)s frühere Nachrichten, zusammengefasst",
        "Web search failed": "Websuche fehlgeschlagen",
        "Searched the web for “%(query)s”": "Im Web gesucht nach „%(query)s“",
        "Searched the web": "Im Web gesucht",
        "%(tool)s failed": "%(tool)s fehlgeschlagen",
        "%(n)s result": "%(n)s Ergebnis",
        "%(n)s results": "%(n)s Ergebnisse",
        "%(n)s tokens — %(percent)s%% of %(limit)s": "%(n)s Tokens — %(percent)s%% von %(limit)s",
        "Nothing yet.": "Noch nichts.",
        "Not recorded for %(model)s, so there is no percentage and this chat will never compact itself.": "Für %(model)s nicht erfasst, daher gibt es keinen Prozentwert, und dieser Chat fasst sich nie selbst zusammen.",
        "Not recorded for this model, so there is no percentage and this chat will never compact itself.": "Für dieses Modell nicht erfasst, daher gibt es keinen Prozentwert, und dieser Chat fasst sich nie selbst zusammen.",
        "Set it under <a href=\"/admin/models\">Models</a>.": "Legen Sie sie unter <a href=\"/admin/models\">Modelle</a> fest.",
        "%(n)s tokens across %(replies)s reply": "%(n)s Tokens über %(replies)s Antwort",
        "%(n)s tokens across %(replies)s replies": "%(n)s Tokens über %(replies)s Antworten",
        "%(n)s tokens/second": "%(n)s Tokens/Sekunde",
        "New chat": "Neuer Chat",
        "What should this chat be called?": "Wie soll dieser Chat heißen?",
        "Add an OpenAI-compatible connection and %(name)s will load its models.": "Fügen Sie eine OpenAI-kompatible Verbindung hinzu, dann lädt %(name)s ihre Modelle.",
        "No model connections have been set up yet. Ask an administrator.": "Es sind noch keine Modellverbindungen eingerichtet. Fragen Sie einen Administrator.",
        "Set up a connection": "Verbindung einrichten",
        "Approve only a device you are signing in yourself, right now. It keeps access until you revoke it under Settings → Devices.": "Bestätigen Sie nur ein Gerät, das Sie selbst gerade anmelden. Es behält den Zugriff, bis Sie ihn unter Einstellungen → Geräte widerrufen.",
        "Back to your chats": "Zurück zu Ihren Chats",
        "Nothing found": "Nichts gefunden",
        "Offline": "Offline",
        "%(name)s answers from your server, so there is nothing to read until it can be reached again.": "%(name)s antwortet von Ihrem Server aus, also gibt es nichts zu lesen, bis er wieder erreichbar ist.",
        "Restore chat": "Chat wiederherstellen",
        "Archive chat": "Chat archivieren",
        "Put this chat back in the list": "Diesen Chat zurück in die Liste holen",
        "Hide this chat without deleting it": "Diesen Chat ausblenden, ohne ihn zu löschen",
        "Delete “%(title)s”? This cannot be undone.": "„%(title)s“ löschen? Das lässt sich nicht rückgängig machen.",
        "What should this folder be called?": "Wie soll dieser Ordner heißen?",
        "Delete the folder “%(name)s”? Chats inside it are kept.": "Den Ordner „%(name)s“ löschen? Die Chats darin bleiben erhalten.",
        "Pinned models": "Angeheftete Modelle",
        "Agent chats run on a machine with the LLeMbas CLI, signed in to this instance and linked. None is linked now.": "Agent-Chats laufen auf einem Rechner mit der LLeMbas CLI, die bei dieser Instanz angemeldet und verknüpft ist. Derzeit ist keiner verknüpft.",
        "Settings → Devices": "Einstellungen → Geräte",
        "offline": "offline",
        "New agent chat on %(device)s": "Neuer Agent-Chat auf %(device)s",
        "No agent chats yet. Nothing is stirring out there.": "Noch keine Agent-Chats. Draußen regt sich nichts.",
        "No chats yet. The road begins here.": "Noch keine Chats. Hier beginnt die Straße.",
        "Archived": "Archiviert",
        "Instance default — %(voice)s": "Standard der Instanz — %(voice)s",
        "%(voice)s (not currently offered)": "%(voice)s (derzeit nicht angeboten)",
        "Devices": "Geräte",
        "Leave the date empty for a repeating schedule and it begins now. Times are %(timezone)s.": "Lassen Sie das Datum leer für einen wiederkehrenden Zeitplan, dann beginnt er jetzt. Zeiten gelten in %(timezone)s.",
        "next %(when)s": "nächster Lauf %(when)s",
        "run %(n)s times": "%(n)s-mal ausgeführt",
        "%a %-d %b, %H:%M": "%a, %-d. %b, %H:%M",
        "Paused.": "Pausiert.",
        "Next run %(when)s.": "Nächster Lauf %(when)s.",
        "Nothing left to run.": "Nichts mehr auszuführen.",
        "Run %(n)s times so far.": "Bisher %(n)s-mal ausgeführt.",
        "Files a report each time.": "Legt jedes Mal einen Bericht ab.",
        "Run now": "Jetzt ausführen",
        "Pause": "Pausieren",
        "Resume": "Fortsetzen",
        "Open its chat": "Seinen Chat öffnen",
        "Remove schedule": "Zeitplan entfernen",
        "New scheduled task": "Neue geplante Aufgabe",
        "What do you want to schedule?": "Was möchten Sie planen?",
        "Times are read as %(timezone)s. Say how often, and what should happen.": "Zeiten gelten in %(timezone)s. Sagen Sie, wie oft und was geschehen soll.",
        "Work it out": "Ausarbeiten",
        "This will run <strong>%(summary)s</strong>. Check it below and change anything that is not what you meant.": "Dies läuft <strong>%(summary)s</strong>. Prüfen Sie es unten und ändern Sie alles, was Sie nicht so gemeint haben.",
        "Schedule it": "Planen",
        "Appearance": "Darstellung",
        "Security": "Sicherheit",
        "Language saved.": "Sprache gespeichert.",
        "Timezone saved.": "Zeitzone gespeichert.",
        "That language is not offered here.": "Diese Sprache wird hier nicht angeboten.",
        "Switched off from the LLeMbas CLI: nothing is sent. Saving here switches it on again.": "In der LLeMbas CLI ausgeschaltet: Es wird nichts gesendet. Speichern hier schaltet es wieder ein.",
        "Administrator": "Administrator",
        "Member": "Mitglied",
        "You are an administrator, so every permission applies.": "Sie sind Administrator, also gilt jede Berechtigung.",
        "From the instance baseline plus whatever your groups add.": "Aus der Grundausstattung der Instanz plus dem, was Ihre Gruppen hinzufügen.",
        "allowed": "erlaubt",
        "not allowed": "nicht erlaubt",
        "Which tools a model may use with you, and how hard a reasoning model thinks when a chat starts. These apply to you only, can only narrow what this instance allows, and a chat's own settings still win.": "Welche Werkzeuge ein Modell bei Ihnen verwenden darf und wie gründlich ein Denkmodell nachdenkt, wenn ein Chat beginnt. Das gilt nur für Sie, kann nur einschränken, was diese Instanz erlaubt, und die eigenen Einstellungen eines Chats haben weiterhin Vorrang.",
        "%(n)s tools off": "%(n)s Werkzeuge aus",
        "effort %(level)s": "Denkaufwand %(level)s",
        "Unticked ones are not offered to this model when you chat with it. Only what this instance already offers you is listed.": "Nicht angehakte werden diesem Modell nicht angeboten, wenn Sie mit ihm chatten. Aufgeführt ist nur, was diese Instanz Ihnen bereits anbietet.",
        "This model is offered no tools here.": "Diesem Modell werden hier keine Werkzeuge angeboten.",
        "As the instance sets it (%(level)s)": "Wie die Instanz es festlegt (%(level)s)",
        "Where a new chat with this model starts. Change it in the chat for that chat only.": "Womit ein neuer Chat mit diesem Modell beginnt. Ändern Sie es im Chat, gilt es nur für diesen Chat.",
        "Follow the instance (%(language)s)": "Der Instanz folgen (%(language)s)",
        "dark": "dunkel",
        "light": "hell",
        "Follow the server (%(zone)s)": "Dem Server folgen (%(zone)s)",
        "Saved. It is %(time)s where you are.": "Gespeichert. Bei Ihnen ist es %(time)s.",
        "It is currently %(time)s where you are.": "Bei Ihnen ist es gerade %(time)s.",
        "Install": "Installieren",
        "The voice list could not be read: %(error)s": "Die Stimmenliste konnte nicht gelesen werden: %(error)s",
        "%(n)s available.": "%(n)s verfügbar.",
        "An ISO code such as <code>en</code> or <code>sk</code>. Empty lets the server work it out, which is usually best.": "Ein ISO-Code wie <code>en</code> oder <code>sk</code>. Leer überlässt es dem Server, was meist am besten ist.",
        "What models remember about you": "Was sich Modelle über Sie merken",
        "One fact per record, at most %(n)s characters. Never put a password or a key here — it is sent to the model with every message.": "Ein Fakt pro Eintrag, höchstens %(n)s Zeichen. Schreiben Sie nie ein Passwort oder einen Schlüssel hierher — es wird mit jeder Nachricht an das Modell gesendet.",
        "That connection no longer exists.": "Diese Verbindung gibt es nicht mehr.",
        "The base URL must start with http:// or https://": "Die Basis-URL muss mit http:// oder https:// beginnen",
        "%(name)s: found 1 model.": "%(name)s: 1 Modell gefunden.",
        "%(name)s: found %(count)s models.": "%(name)s: %(count)s Modelle gefunden.",
        "Spoke the test phrase: %(size)s bytes of audio.": "Testsatz gesprochen: %(size)s Bytes Audio.",
        "Heard \"%(text)s\".": "Gehört: „%(text)s“.",
        "Heard nothing, as expected.": "Nichts gehört, wie erwartet.",
        "The endpoint answered. %(heard)s": "Der Endpunkt hat geantwortet. %(heard)s",
        "No such data group.": "Diese Datengruppe gibt es nicht.",
        "A data group needs a name.": "Eine Datengruppe braucht einen Namen.",
        "Extraction saved.": "Extraktion gespeichert.",
        "Search saved.": "Suche gespeichert.",
        "Search saved. Rebuild the index to use the new model.": "Suche gespeichert. Bauen Sie den Index neu auf, um das neue Modell zu verwenden.",
        "Found 1 checkpoint, %(samplers)s samplers and %(schedulers)s schedulers.": "1 Checkpoint, %(samplers)s Sampler und %(schedulers)s Scheduler gefunden.",
        "Found %(checkpoints)s checkpoints, %(samplers)s samplers and %(schedulers)s schedulers.": "%(checkpoints)s Checkpoints, %(samplers)s Sampler und %(schedulers)s Scheduler gefunden.",
        "That workflow no longer exists.": "Diesen Workflow gibt es nicht mehr.",
        "A workflow needs a name.": "Ein Workflow braucht einen Namen.",
        "The name the model uses must be lowercase letters, digits, hyphens or underscores, and start with a letter or digit.": "Der Name, den das Modell verwendet, darf nur Kleinbuchstaben, Ziffern, Bindestriche oder Unterstriche enthalten und muss mit einem Buchstaben oder einer Ziffer beginnen.",
        "There is already a workflow called “%(slug)s”.": "Es gibt bereits einen Workflow namens „%(slug)s“.",
        "Paste the workflow, in ComfyUI's API format.": "Fügen Sie den Workflow im API-Format von ComfyUI ein.",
        "That is not valid JSON: %(error)s": "Das ist kein gültiges JSON: %(error)s",
        "A ComfyUI API workflow is a JSON object keyed by node id. Use “Export (API)” in ComfyUI rather than “Save”.": "Ein ComfyUI-API-Workflow ist ein JSON-Objekt mit Knoten-IDs als Schlüsseln. Verwenden Sie in ComfyUI „Export (API)“ statt „Save“.",
        "The workflow never uses %(placeholder)s, so every image would be the same. Put it where the text prompt goes.": "Der Workflow verwendet %(placeholder)s nirgends, also wäre jedes Bild dasselbe. Setzen Sie ihn dort ein, wo der Text-Prompt hingehört.",
        "Unknown placeholder %(placeholder)s.": "Unbekannter Platzhalter %(placeholder)s.",
        "Added %(name)s.": "%(name)s hinzugefügt.",
        "Deleted %(name)s.": "%(name)s gelöscht.",
        "Saved %(name)s.": "%(name)s gespeichert.",
        "Fetch a page": "Seite abrufen",
        "Ask the reader": "Den Leser fragen",
        "That model no longer exists.": "Dieses Modell gibt es nicht mehr.",
        "All": "Alle",
        "Enabled": "Aktiviert",
        "Disabled": "Deaktiviert",
        "Restricted": "Beschränkt",
        "%(n)s model(s) updated.": "%(n)s Modell(e) aktualisiert.",
        "This model's template accepts: %(efforts)s.": "Die Vorlage dieses Modells akzeptiert: %(efforts)s.",
        "The endpoint gave up its chat template, but nothing in it names a set of reasoning efforts. Either this model does not take one, or it accepts anything and never checks.": "Der Endpunkt hat seine Chat-Vorlage herausgegeben, aber nichts darin nennt eine Reihe von Denkaufwänden. Entweder nimmt dieses Modell keinen an, oder es akzeptiert alles und prüft nie.",
        "This endpoint does not publish its chat template, so there is nothing to read. llama.cpp does; OpenAI and vLLM do not.": "Dieser Endpunkt veröffentlicht seine Chat-Vorlage nicht, also gibt es nichts zu lesen. llama.cpp tut es; OpenAI und vLLM nicht.",
        "Now the default model.": "Jetzt das Standardmodell.",
        "Image updated.": "Bild aktualisiert.",
        "Image removed.": "Bild entfernt.",
        "No such image.": "Dieses Bild gibt es nicht.",
        "An ordinary chat": "Ein gewöhnlicher Chat",
        "A scheduled task, running unattended": "Eine geplante Aufgabe, unbeaufsichtigt",
        'A subagent sent by another model': 'Ein Subagent, von einem anderen Modell geschickt',
        "%(provider)s returned 1 result.": "%(provider)s hat 1 Ergebnis geliefert.",
        "%(provider)s returned %(n)s results.": "%(provider)s hat %(n)s Ergebnisse geliefert.",
        "That suggestion no longer exists.": "Diesen Vorschlag gibt es nicht mehr.",
        "A suggestion needs a name.": "Ein Vorschlag braucht einen Namen.",
        "That is already %(n)s, which is plenty.": "Es sind bereits %(n)s, und das ist reichlich.",
        "Text — HTML reduced to prose": "Text — HTML auf Fließtext reduziert",
        "JSON — parsed, narrowed by the path below": "JSON — geparst, eingegrenzt durch den Pfad unten",
        "Raw — exactly as it arrived": "Roh — genau so, wie es ankam",
        "None — this endpoint needs no credential": "Keine — dieser Endpunkt braucht keine Zugangsdaten",
        "Bearer token in a header": "Bearer-Token in einem Header",
        "The header named below, verbatim": "Der unten genannte Header, wörtlich",
        "A query parameter named below": "Ein unten genannter Abfrageparameter",
        "That tool no longer exists.": "Dieses Werkzeug gibt es nicht mehr.",
        "A tool needs a name.": "Ein Werkzeug braucht einen Namen.",
        "The identifier must be lowercase letters, digits, hyphens or underscores, start with a letter or digit, and be at most 48 characters. It is the name the model calls.": "Die Kennung darf nur Kleinbuchstaben, Ziffern, Bindestriche oder Unterstriche enthalten, muss mit einem Buchstaben oder einer Ziffer beginnen und darf höchstens 48 Zeichen lang sein. Es ist der Name, den das Modell aufruft.",
        "“%(slug)s” is the name of a built-in tool. Choose another.": "„%(slug)s“ ist der Name eines eingebauten Werkzeugs. Wählen Sie einen anderen.",
        "There is already a tool called “%(slug)s”.": "Es gibt bereits ein Werkzeug namens „%(slug)s“.",
        "%(method)s is not a method this can send.": "%(method)s ist keine Methode, die hier gesendet werden kann.",
        "The parameters are not valid JSON: %(error)s": "Die Parameter sind kein gültiges JSON: %(error)s",
        "The parameters must be a JSON object whose \"type\" is \"object\".": "Die Parameter müssen ein JSON-Objekt sein, dessen \"type\" \"object\" ist.",
        "Arguments must be a JSON object.": "Argumente müssen ein JSON-Objekt sein.",
        "Those arguments are not a JSON object: %(error)s": "Diese Argumente sind kein JSON-Objekt: %(error)s",
        "That server no longer exists.": "Diesen Server gibt es nicht mehr.",
        "A server needs a name.": "Ein Server braucht einen Namen.",
        "The identifier must be lowercase letters, digits, hyphens or underscores, and at most 24 characters. It prefixes every tool name this server offers.": "Die Kennung darf nur Kleinbuchstaben, Ziffern, Bindestriche oder Unterstriche enthalten und höchstens 24 Zeichen lang sein. Sie wird jedem Werkzeugnamen dieses Servers vorangestellt.",
        "There is already a server called “%(slug)s”.": "Es gibt bereits einen Server namens „%(slug)s“.",
        "Added %(name)s, but it could not be reached: %(error)s": "%(name)s hinzugefügt, aber nicht erreichbar: %(error)s",
        "Added %(name)s — %(count)s tool(s).": "%(name)s hinzugefügt — %(count)s Werkzeug(e).",
        "%(name)s: found 1 tool.": "%(name)s: 1 Werkzeug gefunden.",
        "%(name)s: found %(count)s tools.": "%(name)s: %(count)s Werkzeuge gefunden.",
        "The update helper is not installed on this host.": "Der Update-Helfer ist auf diesem Host nicht installiert.",
        "Request withdrawn.": "Anforderung zurückgezogen.",
        "That user no longer exists.": "Diesen Benutzer gibt es nicht mehr.",
        "That group no longer exists.": "Diese Gruppe gibt es nicht mehr.",
        "That email is already registered.": "Diese E-Mail-Adresse ist bereits registriert.",
        "Created %(email)s.": "%(email)s erstellt.",
        "That is the only administrator. Promote someone else first.": "Das ist der einzige Administrator. Befördern Sie zuerst jemand anderen.",
        "Saved %(email)s.": "%(email)s gespeichert.",
        "Password reset. Sessions revoked.": "Passwort zurückgesetzt. Sitzungen widerrufen.",
        "You cannot delete your own account.": "Sie können Ihr eigenes Konto nicht löschen.",
        "That is the only administrator.": "Das ist der einzige Administrator.",
        "Deleted %(email)s.": "%(email)s gelöscht.",
        "A group needs a name.": "Eine Gruppe braucht einen Namen.",
        "A group with that name already exists.": "Eine Gruppe mit diesem Namen gibt es bereits.",
        "Created %(name)s.": "%(name)s erstellt.",
        "Default permissions saved.": "Standardberechtigungen gespeichert.",
        "Dictation is not enabled on this instance.": "Diktieren ist auf dieser Instanz nicht aktiviert.",
        "That recording is too long.": "Diese Aufnahme ist zu lang.",
        "The recording was empty.": "Die Aufnahme war leer.",
        "Read-aloud is not enabled on this instance.": "Vorlesen ist auf dieser Instanz nicht aktiviert.",
        "There is nothing to read out.": "Es gibt nichts vorzulesen.",
        "That chat no longer exists.": "Diesen Chat gibt es nicht mehr.",
        "That message no longer exists.": "Diese Nachricht gibt es nicht mehr.",
        "Too many failed attempts. Try again in %(seconds)s seconds.": "Zu viele fehlgeschlagene Versuche. Versuchen Sie es in %(seconds)s Sekunden erneut.",
        "That email and password do not match.": "E-Mail-Adresse und Passwort passen nicht zusammen.",
        "This account has been deactivated. Ask an administrator.": "Dieses Konto wurde deaktiviert. Fragen Sie einen Administrator.",
        "Registration is closed. Ask an administrator for an account.": "Die Registrierung ist geschlossen. Bitten Sie einen Administrator um ein Konto.",
        "Please enter a name.": "Bitte geben Sie einen Namen ein.",
        "Please enter a valid email address.": "Bitte geben Sie eine gültige E-Mail-Adresse ein.",
        "An account with that email already exists.": "Ein Konto mit dieser E-Mail-Adresse gibt es bereits.",
        "This chat runs on a device, which keeps its own history. Send a new message instead.": "Dieser Chat läuft auf einem Gerät, das seinen eigenen Verlauf führt. Senden Sie stattdessen eine neue Nachricht.",
        "The inspector is restricted to administrators.": "Der Inspektor ist Administratoren vorbehalten.",
        "Wait for the current reply to finish, then compact.": "Warten Sie, bis die aktuelle Antwort fertig ist, und fassen Sie dann zusammen.",
        "There is nothing here to summarise yet.": "Hier gibt es noch nichts zusammenzufassen.",
        "Not compacted: %(error)s": "Nicht zusammengefasst: %(error)s",
        "The device returned no summary, so nothing changed.": "Das Gerät hat keine Zusammenfassung geliefert, also hat sich nichts geändert.",
        "Compaction is turned off: its prompt is empty under Admin → Prompts.": "Die Zusammenfassung ist ausgeschaltet: Ihr Prompt unter Administration → Prompts ist leer.",
        "This chat has no model that can summarise it -- pick one in the composer first.": "Dieser Chat hat kein Modell, das ihn zusammenfassen kann -- wählen Sie zuerst eines im Eingabefeld.",
        "Could not compact: %(error)s": "Zusammenfassen fehlgeschlagen: %(error)s",
        "The model returned no summary, so nothing changed.": "Das Modell hat keine Zusammenfassung geliefert, also hat sich nichts geändert.",
        "Say what to turn on or off.": "Sagen Sie, was ein- oder ausgeschaltet werden soll.",
        "Too many things switched off.": "Zu vieles ausgeschaltet.",
        "You already have 1 reply being written, which is this account's limit. Wait for one to finish.": "Bei Ihnen wird bereits 1 Antwort geschrieben, und das ist das Limit dieses Kontos. Warten Sie, bis sie fertig ist.",
        "You already have %(n)s replies being written, which is this account's limit. Wait for one to finish.": "Bei Ihnen werden bereits %(n)s Antworten geschrieben, und das ist das Limit dieses Kontos. Warten Sie, bis eine fertig ist.",
        "There are already %(n)s messages waiting to be sent.": "Es warten bereits %(n)s Nachrichten darauf, gesendet zu werden.",
        "A background job's message cannot be edited.": "Die Nachricht einer Hintergrundaufgabe kann nicht bearbeitet werden.",
        "A message cannot be empty.": "Eine Nachricht darf nicht leer sein.",
        "Wait for the current reply to finish, or stop it.": "Warten Sie, bis die aktuelle Antwort fertig ist, oder halten Sie sie an.",
        "There is no plan on that message.": "Zu dieser Nachricht gibt es keinen Plan.",
        "That message is not waiting to be sent.": "Diese Nachricht wartet nicht darauf, gesendet zu werden.",
        "That question is no longer waiting for an answer.": "Diese Frage wartet nicht mehr auf eine Antwort.",
        "This chat will not ask about 1 more action. Clear that from the menu beside the composer.": "Dieser Chat fragt bei 1 weiteren Aktion nicht mehr nach. Das lässt sich im Menü neben dem Eingabefeld zurücknehmen.",
        "This chat will not ask about %(n)s more actions. Clear that from the menu beside the composer.": "Dieser Chat fragt bei %(n)s weiteren Aktionen nicht mehr nach. Das lässt sich im Menü neben dem Eingabefeld zurücknehmen.",
        "Allowed once. A command line that runs more than one thing cannot be stored as a rule, so this chat will ask again.": "Einmal erlaubt. Eine Befehlszeile, die mehr als eine Sache ausführt, kann nicht als Regel gespeichert werden, also fragt dieser Chat erneut.",
        "A chat's connection is fixed when it is created. Start a new chat to work somewhere else.": "Die Verbindung eines Chats wird beim Erstellen festgelegt. Starten Sie einen neuen Chat, um anderswo zu arbeiten.",
        "You may not change the model for a chat.": "Sie dürfen das Modell eines Chats nicht ändern.",
        "That model is in another data group than this chat (%(group)s), so it cannot be given this chat's history. Start a new chat with it instead.": "Dieses Modell ist in einer anderen Datengruppe als dieser Chat (%(group)s), daher kann es den Verlauf dieses Chats nicht bekommen. Starten Sie stattdessen einen neuen Chat damit.",
        "That model is not available to you.": "Dieses Modell steht Ihnen nicht zur Verfügung.",
        "You may not set a system prompt.": "Sie dürfen keinen Systemprompt festlegen.",
        "You may not change sampling parameters.": "Sie dürfen die Sampling-Parameter nicht ändern.",
        "That reply no longer exists.": "Diese Antwort gibt es nicht mehr.",
        "This area is restricted to administrators.": "Dieser Bereich ist Administratoren vorbehalten.",
        "You do not have permission to do that.": "Dazu haben Sie keine Berechtigung.",
        "No such device.": "Dieses Gerät gibt es nicht.",
        "That file no longer exists.": "Diese Datei gibt es nicht mehr.",
        "That file is part of a sent message.": "Diese Datei gehört zu einer gesendeten Nachricht.",
        "That file is no longer on disk.": "Diese Datei liegt nicht mehr auf der Festplatte.",
        "That folder no longer exists.": "Diesen Ordner gibt es nicht mehr.",
        "Folders cannot be nested more than %(n)s deep.": "Ordner können nicht tiefer als %(n)s Ebenen verschachtelt werden.",
        "A folder cannot be moved inside itself.": "Ein Ordner kann nicht in sich selbst verschoben werden.",
        "Memory added.": "Erinnerung hinzugefügt.",
        "That memory no longer exists.": "Diese Erinnerung gibt es nicht mehr.",
        "Memory updated.": "Erinnerung aktualisiert.",
        "Memory removed.": "Erinnerung entfernt.",
        "You cannot manage folders.": "Sie können keine Ordner verwalten.",
        "Default model updated.": "Standardmodell aktualisiert.",
        "Saved for %(model)s.": "Für %(model)s gespeichert.",
        "Audio preferences updated.": "Audio-Einstellungen aktualisiert.",
        "Your current password is not correct.": "Ihr aktuelles Passwort ist nicht korrekt.",
        "The new passwords do not match.": "Die neuen Passwörter stimmen nicht überein.",
        "That is already your password.": "Das ist bereits Ihr Passwort.",
        "Password changed. Any other sessions have been signed out.": "Passwort geändert. Alle anderen Sitzungen wurden abgemeldet.",
        "You may not manage your own data groups.": "Sie dürfen Ihre eigenen Datengruppen nicht verwalten.",
        "Data groups updated.": "Datengruppen aktualisiert.",
        "Data group created.": "Datengruppe erstellt.",
        "Personalization is off here.": "Personalisierung ist hier ausgeschaltet.",
        "Personalization saved.": "Personalisierung gespeichert.",
        "There is no model configured to work this out, so fill it in yourself.": "Es ist kein Modell eingerichtet, das dies ausarbeiten kann, also füllen Sie es selbst aus.",
        "That schedule is not available.": "Dieser Zeitplan ist nicht verfügbar.",
        "The shell exited.": "Die Shell wurde beendet.",
        "This terminal was closed after sitting idle.": "Dieses Terminal wurde geschlossen, weil es untätig war.",
        "LLeMbas restarted, so this shell was closed.": "LLeMbas wurde neu gestartet, daher wurde diese Shell geschlossen.",
        "The connection behind this terminal was closed.": "Die Verbindung hinter diesem Terminal wurde geschlossen.",
        "The connection to the machine was lost.": "Die Verbindung zum Rechner ist abgebrochen.",
        "You do not have permission to open a terminal.": "Sie haben keine Berechtigung, ein Terminal zu öffnen.",
        "This is an ordinary chat, so it has no machine to open a shell on.": "Dies ist ein gewöhnlicher Chat, also gibt es keinen Rechner, auf dem sich eine Shell öffnen ließe.",
        "The terminal is switched off on this instance.": "Das Terminal ist auf dieser Instanz ausgeschaltet.",
        "That device is not yours.": "Dieses Gerät gehört nicht Ihnen.",
        "%(device)s is not linked right now.": "%(device)s ist gerade nicht verbunden.",
        "This chat is not on a linked device, so it has no shell to open.": "Dieser Chat läuft nicht auf einem verknüpften Gerät, also gibt es keine Shell zu öffnen.",
        "The shell could not be started.": "Die Shell konnte nicht gestartet werden.",
        "Reconnecting: output arrived faster than this window could draw it.": "Neu verbinden: Die Ausgabe kam schneller, als dieses Fenster sie zeichnen konnte.",
        "This terminal closed.": "Dieses Terminal wurde geschlossen.",
        "There is no such token.": "Dieses Token gibt es nicht.",
        "Something went wrong.": "Etwas ist schiefgegangen.",
        "Password must be at least %(n)s characters.": "Das Passwort muss mindestens %(n)s Zeichen lang sein.",
        "Start chats": "Chats beginnen",
        "Create new conversations.": "Neue Gespräche erstellen.",
        "Delete chats": "Chats löschen",
        "Delete their own conversations.": "Eigene Gespräche löschen.",
        "Set system prompts": "Systemprompts festlegen",
        "Give an individual chat its own system prompt.": "Einem einzelnen Chat einen eigenen Systemprompt geben.",
        "Adjust sampling": "Sampling anpassen",
        "Change temperature, top-p and similar per chat.": "Temperatur, Top-p und Ähnliches pro Chat ändern.",
        "Choose the model": "Das Modell wählen",
        "Switch a chat to a different model. Without this, chats use the default.": "Einen Chat auf ein anderes Modell umstellen. Ohne dies verwenden Chats das Standardmodell.",
        "Manage folders": "Ordner verwalten",
        "Create, rename, nest and delete folders.": "Ordner erstellen, umbenennen, verschachteln und löschen.",
        "Attach files": "Dateien anhängen",
        "Attach images, PDFs and text files to a message. Images only reach models marked as having vision.": "Bilder, PDFs und Textdateien an eine Nachricht anhängen. Bilder erreichen nur Modelle, die als bildfähig markiert sind.",
        "Search the web": "Im Web suchen",
        "Let a model look things up while it answers. Only offered to models marked as supporting tools, and only when web search is configured.": "Ein Modell nachschlagen lassen, während es antwortet. Wird nur Modellen angeboten, die als werkzeugfähig markiert sind, und nur, wenn die Websuche eingerichtet ist.",
        "Let a model retrieve one web page and read it, given its address. Addresses on this machine and this network are refused unless an administrator has allowed them.": "Ein Modell eine Webseite anhand ihrer Adresse abrufen und lesen lassen. Adressen auf diesem Rechner und in diesem Netzwerk werden abgelehnt, sofern ein Administrator sie nicht erlaubt hat.",
        "Generate images": "Bilder generieren",
        "Let a model draw a picture and show it in the conversation. Only offered when an image generator has been configured, and every generation spends time on whatever machine is running it.": "Ein Modell ein Bild zeichnen und im Gespräch zeigen lassen. Wird nur angeboten, wenn ein Bildgenerator eingerichtet ist, und jede Generierung kostet Zeit auf dem Rechner, der ihn betreibt.",
        "Use custom tools": "Eigene Werkzeuge verwenden",
        "Let a model call the HTTP tools an administrator has defined. Which ones depends on the groups each tool is restricted to.": "Ein Modell die HTTP-Werkzeuge aufrufen lassen, die ein Administrator definiert hat. Welche, hängt von den Gruppen ab, auf die jedes Werkzeug beschränkt ist.",
        "Use MCP servers": "MCP-Server verwenden",
        "Let a model call tools from the MCP servers an administrator has added. Which ones depends on the groups each server is restricted to.": "Ein Modell Werkzeuge der MCP-Server aufrufen lassen, die ein Administrator hinzugefügt hat. Welche, hängt von den Gruppen ab, auf die jeder Server beschränkt ist.",
        "Let them start agent chats on a LLeMbas CLI they have linked: its model reads files, writes files and runs commands on that machine, within the CLI's own limits and the chat's mode. Nothing runs on this server.": "Agent-Chats auf einer selbst verknüpften LLeMbas CLI starten: Ihr Modell liest Dateien, schreibt Dateien und führt Befehle auf diesem Rechner aus, innerhalb der eigenen Grenzen der CLI und des Modus des Chats. Auf diesem Server läuft nichts.",
        "Open an interactive shell on their linked device, from inside an agent chat, where the device allows it. What they type there is theirs: the chat's mode governs the model, not the person at the keyboard.": "Aus einem Agent-Chat heraus eine interaktive Shell auf dem eigenen verknüpften Gerät öffnen, sofern das Gerät es erlaubt. Was dort getippt wird, ist Sache der Person: Der Modus des Chats gilt für das Modell, nicht für den Menschen an der Tastatur.",
        'Let a model hand a self-contained piece of work to a second one that runs on its own and reports back — reading and searching in parallel rather than one thing at a time. A subagent cannot ask questions, cannot spawn subagents of its own, and can only do what this chat could already do without stopping to ask.': 'Ein Modell ein in sich geschlossenes Stück Arbeit an ein zweites übergeben lassen, das selbstständig läuft und sich zurückmeldet — Lesen und Suchen parallel statt nacheinander. Ein Subagent kann keine Fragen stellen, keine eigenen Subagenten starten und nur tun, was dieser Chat schon ohne Nachfrage durfte.',
        "Be asked questions": "Gefragt werden",
        "Let a model stop mid-reply and ask you something, with answers to pick from or a box to write your own.": "Ein Modell mitten in der Antwort innehalten und Sie etwas fragen lassen, mit Antworten zur Auswahl oder einem Feld für eine eigene.",
        "Set things to run later, on their own — once, or on a repeating timetable. This spends model time with nobody at the keyboard, so it is a capability chosen on purpose rather than one everybody has.": "Dinge später von selbst laufen lassen — einmal oder nach einem wiederkehrenden Plan. Das verbraucht Modellzeit, während niemand an der Tastatur sitzt, daher ist es eine bewusst vergebene Fähigkeit, keine, die jeder hat.",
        "Dictate messages": "Nachrichten diktieren",
        "Speak a message instead of typing it. Needs a transcription endpoint.": "Eine Nachricht sprechen statt tippen. Braucht einen Transkriptions-Endpunkt.",
        "Play replies aloud": "Antworten vorlesen",
        "Have a reply read out. Needs a speech endpoint.": "Eine Antwort vorlesen lassen. Braucht einen Sprach-Endpunkt.",
        "Use the library": "Die Bibliothek verwenden",
        "Remember things": "Sich Dinge merken",
        "Let a model record short facts about this user, shown to it on every turn.": "Ein Modell kurze Fakten über diesen Benutzer festhalten lassen, die ihm in jeder Runde gezeigt werden.",
        "Use and write skills": "Fertigkeiten verwenden und schreiben",
        "Let a model follow saved instructions, and write new ones. Every change is recorded and can be rolled back.": "Ein Modell gespeicherten Anweisungen folgen und neue schreiben lassen. Jede Änderung wird protokolliert und lässt sich zurücknehmen.",
        "Record memories": "Erinnerungen festhalten",
        "Let a model add and forget short facts about this person. Without it, the memories it already has are still shown to it every turn.": "Ein Modell kurze Fakten über diese Person hinzufügen und vergessen lassen. Ohne dies werden ihm die Erinnerungen, die es schon hat, weiterhin in jeder Runde gezeigt.",
        "Write skills": "Fertigkeiten schreiben",
        "Let a model write new skills and change existing ones. Without it, it follows the skills that are there and cannot add to them — which is the setting for an instance whose skills are curated by hand.": "Ein Modell neue Fertigkeiten schreiben und bestehende ändern lassen. Ohne dies folgt es den vorhandenen Fertigkeiten und kann keine hinzufügen — die Einstellung für eine Instanz, deren Fertigkeiten von Hand gepflegt werden.",
        "Manage their own data groups": "Eigene Datengruppen verwalten",
        "Make personal data groups, choose which of them each connection reads for this person, and move their own records between groups.": "Persönliche Datengruppen anlegen, wählen, welche davon jede Verbindung für diese Person liest, und eigene Einträge zwischen Gruppen verschieben.",
        "Use the API": "Die API verwenden",
        "Make API tokens and sign devices in (the LLeMbas CLI's /login): their own models through /v1, as themselves, counted in their usage.": "API-Tokens erstellen und Geräte anmelden (/login der LLeMbas CLI): die eigenen Modelle über /v1, im eigenen Namen, auf den eigenen Verbrauch angerechnet.",
        "See who signed in, who changed what and which devices were signed in, across the instance.": "Sehen, wer sich angemeldet hat, wer was geändert hat und welche Geräte angemeldet wurden, instanzweit.",
        "Workspace": "Arbeitsbereich",
        "Agent": "Agent",
        "API": "API",
        "Tools a model may use": "Werkzeuge, die ein Modell verwenden darf",
        "Agents, devices and the API": "Agenten, Geräte und die API",
        "Scheduling and reports": "Zeitplanung und Berichte",
        "Other people": "Andere Personen",
        "Start new chats.": "Neue Chats beginnen.",
        "Delete your own chats.": "Eigene Chats löschen.",
        "Give a chat its own system prompt.": "Einem Chat einen eigenen Systemprompt geben.",
        "Change temperature, top-p and the like for a chat.": "Temperatur, Top-p und Ähnliches für einen Chat ändern.",
        "Switch a chat to another model.": "Einen Chat auf ein anderes Modell umstellen.",
        "Attach images, PDFs and text files to a message.": "Bilder, PDFs und Textdateien an eine Nachricht anhängen.",
        "Look things up on the web while answering.": "Beim Antworten im Web nachschlagen.",
        "Read a web page from its address.": "Eine Webseite anhand ihrer Adresse lesen.",
        "Draw pictures in the conversation.": "Bilder im Gespräch zeichnen.",
        "Call the HTTP tools an administrator has defined.": "Die HTTP-Werkzeuge aufrufen, die ein Administrator definiert hat.",
        "Use tools from the MCP servers an administrator has added.": "Werkzeuge der MCP-Server verwenden, die ein Administrator hinzugefügt hat.",
        'Hand a self-contained task to a subagent.': 'Eine in sich geschlossene Aufgabe an einen Subagenten übergeben.',
        "Stop mid-reply and ask you a question.": "Mitten in der Antwort innehalten und Ihnen eine Frage stellen.",
        "Start agent chats on a LLeMbas CLI you have linked.": "Agent-Chats auf einer von Ihnen verknüpften LLeMbas CLI starten.",
        "Open a shell on your linked device from an agent chat.": "Aus einem Agent-Chat eine Shell auf Ihrem verknüpften Gerät öffnen.",
        "Make API tokens and sign the LLeMbas CLI in as you.": "API-Tokens erstellen und die LLeMbas CLI in Ihrem Namen anmelden.",
        "Set work to run later, once or on a timetable.": "Arbeit für später planen, einmalig oder nach Zeitplan.",
        "Speak a message instead of typing it.": "Eine Nachricht sprechen statt tippen.",
        "Have replies read aloud.": "Antworten vorlesen lassen.",
        "Let a model see what it remembers about you.": "Ein Modell sehen lassen, was es sich über Sie gemerkt hat.",
        "Let a model add and forget facts about you.": "Ein Modell Fakten über Sie hinzufügen und vergessen lassen.",
        "Let a model follow your saved skills.": "Ein Modell Ihren gespeicherten Fertigkeiten folgen lassen.",
        "Let a model write and change skills.": "Ein Modell Fertigkeiten schreiben und ändern lassen.",
        "Make your own data groups and choose what each connection reads.": "Eigene Datengruppen anlegen und wählen, was jede Verbindung liest.",
        "Read the instance's audit log.": "Das Audit-Protokoll der Instanz lesen.",
        "Everything is shown to you before it happens.": "Alles wird Ihnen gezeigt, bevor es geschieht.",
        "Files are read and written freely; commands are shown to you first.": "Dateien werden frei gelesen und geschrieben; Befehle werden Ihnen zuerst gezeigt.",
        "Nothing is shown to you first. Only for work you would do yourself.": "Nichts wird Ihnen vorher gezeigt. Nur für Arbeit, die Sie auch selbst erledigen würden.",
        "Reads freely, changes nothing, and finishes by proposing a plan.": "Liest frei, ändert nichts und schlägt zum Schluss einen Plan vor.",
        "A terminal opens only on a linked LLeMbas CLI device.": "Ein Terminal öffnet sich nur auf einem Gerät mit verknüpfter LLeMbas CLI.",
        "This instance already has as many terminals open as it allows. Close one, or ask an administrator to raise the limit.": "Auf dieser Instanz sind bereits so viele Terminals offen, wie sie erlaubt. Schließen Sie eines, oder bitten Sie einen Administrator, das Limit anzuheben.",
        "No audio endpoint has been configured.": "Es ist kein Audio-Endpunkt eingerichtet.",
        "The transcription endpoint returned no text.": "Der Transkriptions-Endpunkt hat keinen Text geliefert.",
        "The endpoint returned a response that was not JSON.": "Der Endpunkt hat eine Antwort geliefert, die kein JSON war.",
        "Waybread for the long road of thought.": "Wegbrot für den langen Weg des Denkens.",
        "Speak friend and enter.": "Sprich, Freund, und tritt ein.",
        "No road from here": "Von hier führt kein Weg weiter",
        "The Road goes ever on and on — but not without a connection.": "Die Straße gleitet fort und fort — doch nicht ohne Verbindung.",
        "Speak friend and enter. This door is not yours to open.": "Sprich, Freund, und tritt ein. Diese Tür steht Ihnen nicht offen.",
        "Not all those who wander are lost. This page, however, is.": "Nicht jeder, der wandert, ist verloren. Diese Seite allerdings schon.",
        "The Road goes ever on, but this stretch of it has washed out.": "Die Straße gleitet fort und fort, doch dieses Stück ist weggespült.",
        "This chat has no model selected.": "In diesem Chat ist kein Modell ausgewählt.",
        "No enabled connection currently offers the model '%(model)s'. Pick another model for this chat.": "Keine aktivierte Verbindung bietet derzeit das Modell '%(model)s' an. Wählen Sie für diesen Chat ein anderes Modell.",
        "This chat belongs to the data group %(chat_group)s, and its model is now in %(model_group)s, so it cannot be sent this chat's history. Pick a model in %(chat_group)s, or start a new chat.": "Dieser Chat gehört zur Datengruppe %(chat_group)s, und sein Modell ist jetzt in %(model_group)s, daher kann ihm der Verlauf dieses Chats nicht gesendet werden. Wählen Sie ein Modell in %(chat_group)s, oder starten Sie einen neuen Chat.",
        "The default group cannot be deleted.": "Die Standardgruppe kann nicht gelöscht werden.",
        "%(n)s %(label)s": "%(n)s %(label)s",
        "The group still holds %(listed)s. Move them out first.": "Die Gruppe enthält noch %(listed)s. Verschieben Sie sie zuerst heraus.",
        "This account cannot run agent chats.": "Dieses Konto kann keine Agent-Chats ausführen.",
        "This instance serves the API only; agent chats are switched off.": "Diese Instanz stellt nur die API bereit; Agent-Chats sind abgeschaltet.",
        "Agent chats are switched off on this instance.": "Agent-Chats sind auf dieser Instanz abgeschaltet.",
        "You no longer have permission to run agent chats.": "Sie haben keine Berechtigung mehr, Agent-Chats auszuführen.",
        "This chat's device is no longer signed in as you.": "Das Gerät dieses Chats ist nicht mehr in Ihrem Namen angemeldet.",
        "Taking in what you just added…": "Nimmt auf, was Sie gerade hinzugefügt haben…",
        "The device reported an error.": "Das Gerät hat einen Fehler gemeldet.",
        "That image's dimensions are implausibly large.": "Die Abmessungen dieses Bildes sind unplausibel groß.",
        "That image could not be read. Is it corrupt?": "Dieses Bild konnte nicht gelesen werden. Ist es beschädigt?",
        "That file is not text, and is not a format LLeMbas can read.": "Diese Datei ist kein Text und auch kein Format, das LLeMbas lesen kann.",
        "That is not an image.": "Das ist kein Bild.",
        "That file is empty.": "Diese Datei ist leer.",
        "Files must be under %(mb)s MB.": "Dateien müssen kleiner als %(mb)s MB sein.",
        "Something went wrong while generating this reply.": "Beim Generieren dieser Antwort ist etwas schiefgegangen.",
        "Summarising earlier messages…": "Frühere Nachrichten werden zusammengefasst…",
        "Your reply is ready.": "Ihre Antwort ist fertig.",
        "Waiting for your answer…": "Wartet auf Ihre Antwort…",
        "Waiting for your answers…": "Wartet auf Ihre Antworten…",
        "Waiting for you to allow %(tool)s…": "Wartet darauf, dass Sie %(tool)s erlauben…",
        "Waiting for you to allow %(n)s actions…": "Wartet darauf, dass Sie %(n)s Aktionen erlauben…",
        "A memory cannot be empty.": "Eine Erinnerung darf nicht leer sein.",
        "There are already %(n)s memories, which is the limit, so nothing was saved. Do not remove one to make room — you are not shown all of them and would be guessing. Say that the limit has been reached, and put this in a note instead.": "Es gibt bereits %(n)s Erinnerungen, das ist das Limit, daher wurde nichts gespeichert. Entferne keine, um Platz zu schaffen — du siehst nicht alle und würdest raten. Sag, dass das Limit erreicht ist, und halte dies stattdessen in einer Notiz fest.",
        "A reply is still being written in one of your chats. Stop it, or wait for it to finish, and try again.": "In einem Ihrer Chats wird noch eine Antwort geschrieben. Halten Sie sie an oder warten Sie, bis sie fertig ist, und versuchen Sie es dann erneut.",
        "%(n)s replies are still being written in your chats. Stop them, or wait for them to finish, and try again.": "In Ihren Chats werden noch %(n)s Antworten geschrieben. Halten Sie sie an oder warten Sie, bis sie fertig sind, und versuchen Sie es dann erneut.",
        "Your schedule “%(title)s” is running right now. Try again when it has finished.": "Ihr Zeitplan „%(title)s“ läuft gerade. Versuchen Sie es erneut, wenn er fertig ist.",
        "That does not describe a time anything could run at. Say when it should happen — a date and time, or how often.": "Das beschreibt keinen Zeitpunkt, zu dem etwas laufen könnte. Sagen Sie, wann es geschehen soll — ein Datum und eine Uhrzeit, oder wie oft.",
        "That schedule has no next run — its time has already passed.": "Dieser Zeitplan hat keinen nächsten Lauf — sein Zeitpunkt ist schon vorbei.",
        "You already have %(limit)s schedules, which is the most this instance allows. Remove one before adding another.": "Sie haben bereits %(limit)s Zeitpläne, mehr erlaubt diese Instanz nicht. Entfernen Sie einen, bevor Sie einen weiteren hinzufügen.",
        "Start a session": "Sitzung beginnen",
        "Open a fresh conversation with nothing assumed.": "Ein neues Gespräch öffnen, ohne Vorannahmen.",
        "What can you do?": "Was kannst du?",
        "Have the model say which tools, instructions and limits it has.": "Das Modell sagen lassen, welche Werkzeuge, Anweisungen und Grenzen es hat.",
        "Show me some code": "Zeig mir etwas Code",
        "See how the model writes, in a language of its own choosing.": "Sehen, wie das Modell schreibt, in einer Sprache seiner Wahl.",
        "Tell me something": "Erzähl mir etwas",
        "A fact worth knowing, from a subject picked at random.": "Ein wissenswerter Fakt aus einem zufällig gewählten Gebiet.",
        "Fetching pages": "Seiten abrufen",
        "Asking you questions": "Fragen an Sie",
        "Generating images": "Bilder generieren",
        "Scheduling work": "Arbeit planen",
        'Sending subagents': 'Subagenten schicken',
        "Running commands": "Befehle ausführen",
        "Updating in a moment — try again shortly.": "Gleich wird aktualisiert — versuchen Sie es in Kürze erneut.",
        "The backup before the update failed, so nothing was requested: %(error)s": "Die Sicherung vor dem Update ist fehlgeschlagen, daher wurde nichts angefordert: %(error)s",
        "The file was empty.": "Die Datei war leer.",
        "Images must be under %(mb)s MB.": "Bilder müssen kleiner als %(mb)s MB sein.",
        "That does not look like a PNG, JPEG, WEBP or GIF image.": "Das sieht nicht nach einem PNG-, JPEG-, WEBP- oder GIF-Bild aus.",
        "Switch to the light theme": "Zum hellen Design wechseln",
        "Switch to the dark theme": "Zum dunklen Design wechseln",
        "Copied": "Kopiert",
        "Upload failed.": "Hochladen fehlgeschlagen.",
        "Attach a web page": "Eine Webseite anhängen",
        "The page is fetched now and its text attached, so it will not change between writing this and sending it.": "Die Seite wird jetzt abgerufen und ihr Text angehängt, damit sie sich zwischen dem Schreiben und dem Senden nicht ändert.",
        "Fetch": "Abrufen",
        "Close": "Schließen",
        "Where this chat works on the device: its trusted places and anything in them. The device has the last word.": "Wo dieser Chat auf dem Gerät arbeitet: seine vertrauenswürdigen Orte und alles darin. Das letzte Wort hat das Gerät.",
        "Where this chat starts, and what a relative path is measured from. You can walk anywhere the account can reach.": "Wo dieser Chat beginnt und wovon ein relativer Pfad ausgeht. Sie können überallhin, wo das Konto hinkommt.",
        "Path": "Pfad",
        "Use this directory": "Dieses Verzeichnis verwenden",
        "Could not reach that machine.": "Dieser Rechner war nicht erreichbar.",
        "Already installed — you are using the installed app now.": "Bereits installiert — Sie verwenden gerade die installierte App.",
        "This page is not a secure context, so the browser will not install it. That means plain http, or https with a certificate this device does not trust — a private or self-signed certificate has to be installed on the device before any browser will treat the site as secure.": "Diese Seite ist kein sicherer Kontext, daher installiert der Browser sie nicht. Das bedeutet einfaches http oder https mit einem Zertifikat, dem dieses Gerät nicht vertraut — ein privates oder selbstsigniertes Zertifikat muss erst auf dem Gerät installiert werden, bevor irgendein Browser die Seite als sicher behandelt.",
        "The service worker could not be registered, so the browser will not offer an install. The usual cause is a certificate this device does not trust. The browser said: %(reason)s": "Der Service Worker konnte nicht registriert werden, daher bietet der Browser keine Installation an. Die übliche Ursache ist ein Zertifikat, dem dieses Gerät nicht vertraut. Der Browser meldete: %(reason)s",
        "This browser does not support installing. On iOS, use Share → Add to Home Screen.": "Dieser Browser unterstützt keine Installation. Unter iOS verwenden Sie Teilen → Zum Home-Bildschirm.",
        "Everything this end is ready and your browser has not offered an install. Some never do — Firefox and desktop Safari — and Chrome will not offer one twice for the same app.": "Auf dieser Seite ist alles bereit, und Ihr Browser hat keine Installation angeboten. Manche tun das nie — Firefox und Safari auf dem Desktop —, und Chrome bietet sie für dieselbe App kein zweites Mal an.",
        "A new version is ready. Reload to use it.": "Eine neue Version ist bereit. Laden Sie neu, um sie zu verwenden.",
        "Reload": "Neu laden",
        "Stop recording": "Aufnahme beenden",
        "Transcription failed.": "Transkription fehlgeschlagen.",
        "Nothing was heard in that recording.": "In dieser Aufnahme war nichts zu hören.",
        "The microphone needs HTTPS or localhost. This page is served over plain HTTP, so the browser will not grant it.": "Das Mikrofon braucht HTTPS oder localhost. Diese Seite wird über einfaches HTTP ausgeliefert, daher gibt der Browser es nicht frei.",
        "That reply could not be read out.": "Diese Antwort konnte nicht vorgelesen werden.",
        "Open the command menu": "Befehlsmenü öffnen",
        "Send, from anywhere on the page": "Senden, von überall auf der Seite",
        "Dictate": "Diktieren",
        "Read the last reply aloud": "Die letzte Antwort vorlesen",
        "Sidebar": "Seitenleiste",
        "↑ in an empty box": "↑ in einem leeren Feld",
        "Edit your last message": "Ihre letzte Nachricht bearbeiten",
        "Close what is open, or stop the reply": "Schließen, was offen ist, oder die Antwort anhalten",
        "Copy and paste inside the terminal": "Im Terminal kopieren und einfügen",
        "Commands and keyboard shortcuts": "Befehle und Tastenkürzel",
        "Tokens and context used by this chat": "Tokens und Kontext, die dieser Chat verbraucht hat",
        "Usage": "Verbrauch",
        "Could not read this chat's usage.": "Der Verbrauch dieses Chats konnte nicht gelesen werden.",
        "How hard a reasoning model should think": "Wie gründlich ein Denkmodell nachdenken soll",
        "Approval mode: manual, edit, auto or plan": "Freigabemodus: manual, edit, auto oder plan",
        "Modes: manual, edit, auto, plan.": "Modi: manual, edit, auto, plan.",
        "“%(mode)s” is not a mode.": "„%(mode)s“ ist kein Modus.",
        "Mode set to %(mode)s.": "Modus auf %(mode)s gesetzt.",
        "Rename this chat": "Diesen Chat umbenennen",
        "the new title": "der neue Titel",
        "Give it a title: /title Something.": "Geben Sie einen Titel an: /title Irgendwas.",
        "Renamed.": "Umbenannt.",
        "Show or hide the terminal": "Terminal ein- oder ausblenden",
        "Show or hide the request inspector": "Anfrage-Inspektor ein- oder ausblenden",
        "Show or hide the sidebar": "Seitenleiste ein- oder ausblenden",
        "Draw a picture": "Ein Bild zeichnen",
        "what to draw": "was gezeichnet werden soll",
        "Say what to draw: /image a red bicycle in the rain.": "Sagen Sie, was gezeichnet werden soll: /image ein rotes Fahrrad im Regen.",
        "Could not start the image.": "Das Bild konnte nicht gestartet werden.",
        "Start a new chat": "Einen neuen Chat beginnen",
        "Start a temporary chat that saves nothing": "Einen temporären Chat beginnen, der nichts speichert",
        "Carry on from where the reply stopped": "Dort fortsetzen, wo die Antwort aufgehört hat",
        "There is no reply to continue yet.": "Es gibt noch keine Antwort, die fortgesetzt werden könnte.",
        "Stop the reply being written": "Die Antwort anhalten, die gerade geschrieben wird",
        "Nothing is being written.": "Es wird gerade nichts geschrieben.",
        "Your devices: the LLeMbas CLI signed in as you": "Ihre Geräte: die LLeMbas CLI, in Ihrem Namen angemeldet",
        "Your scheduled tasks": "Ihre geplanten Aufgaben",
        "Effort set to %(level)s.": "Denkaufwand auf %(level)s gesetzt.",
        "Already summarising.": "Wird bereits zusammengefasst.",
        "Summarise everything before the last reply? The messages stay in the transcript; they just stop being sent to the model.": "Alles vor der letzten Antwort zusammenfassen? Die Nachrichten bleiben im Verlauf; sie werden nur nicht mehr an das Modell gesendet.",
        "Compact this chat": "Diesen Chat zusammenfassen",
        "Summarising the earlier messages…": "Die früheren Nachrichten werden zusammengefasst…",
        "Could not compact.": "Zusammenfassen fehlgeschlagen.",
        "Could not compact this chat.": "Dieser Chat konnte nicht zusammengefasst werden.",
        "No command called “%(name)s”. It will be sent as a message.": "Keinen Befehl namens „%(name)s“. Er wird als Nachricht gesendet.",
        "Commands": "Befehle",
        "Commands and shortcuts": "Befehle und Kürzel",
        "Keyboard": "Tastatur",
        "A message that starts with a slash but is not a command is sent as written. Type <span class='mono'>//</span> to start one with a literal slash.": "Eine Nachricht, die mit einem Schrägstrich beginnt, aber kein Befehl ist, wird so gesendet, wie sie geschrieben ist. Tippen Sie <span class='mono'>//</span>, um eine mit einem echten Schrägstrich zu beginnen.",
        "↑↓ choose · Tab complete · Enter run · Esc dismiss": "↑↓ wählen · Tab ergänzen · Enter ausführen · Esc schließen",
        "↑↓ choose · Enter attach · Esc dismiss": "↑↓ wählen · Enter anhängen · Esc schließen",
        "Esc dismiss": "Esc schließen",
        "Could not attach that.": "Das konnte nicht angehängt werden.",
        "Choose a connection above, and this panel will open a shell on it.": "Wählen Sie oben eine Verbindung, dann öffnet dieses Panel darauf eine Shell.",
        "Could not connect. If this instance is behind a proxy, it may not be passing WebSocket upgrades through.": "Verbindung fehlgeschlagen. Steht diese Instanz hinter einem Proxy, leitet er WebSocket-Upgrades womöglich nicht weiter.",
        "Connected. This shell is also open in another tab, and they share a size.": "Verbunden. Diese Shell ist auch in einem anderen Tab offen, und beide teilen sich eine Größe.",
        "Connected.": "Verbunden.",
        "Reconnecting…": "Verbinde neu…",
        "The terminal could not be loaded.": "Das Terminal konnte nicht geladen werden.",
        "This shell did not load the LLeMbas command markers, so there is no way to tell where the output of one command ends.": "Diese Shell hat die Befehlsmarkierungen von LLeMbas nicht geladen, daher lässt sich nicht erkennen, wo die Ausgabe eines Befehls endet.",
        "Commands are no longer attached automatically.": "Befehle werden nicht mehr automatisch angehängt.",
        "Every command you run will be put into the message box.": "Jeder Befehl, den Sie ausführen, wird ins Nachrichtenfeld gelegt.",
        "Every command you run will be sent as a message on its own.": "Jeder Befehl, den Sie ausführen, wird als eigene Nachricht gesendet.",
        "Nothing to send: select some output first.": "Nichts zu senden: Markieren Sie zuerst etwas Ausgabe.",
        "Copied the last of the screen, as it appeared. This shell does not mark where commands begin.": "Das Ende des Bildschirms so kopiert, wie es erschien. Diese Shell markiert nicht, wo Befehle beginnen.",
        "Copied.": "Kopiert.",
        "Value": "Wert",
        "OK": "OK",
        "Model switched to %(model)s": "Modell gewechselt zu %(model)s",
        "%(n)s new arrivals": "%(n)s neue Eingänge",
        "New message": "Neue Nachricht",
        "Reply ready in “%(title)s”": "Antwort fertig in „%(title)s“",
        "This browser has no notifications to offer.": "Dieser Browser bietet keine Benachrichtigungen an.",
        "Notifications are blocked for this site in your browser's own settings, which is the only place that can be undone.": "Benachrichtigungen sind für diese Seite in den Einstellungen Ihres Browsers blockiert — nur dort lässt sich das rückgängig machen.",
        "Left off — nothing was changed.": "Ausgelassen — nichts wurde geändert.",
        "Notifications on, including while LLeMbas is closed.": "Benachrichtigungen an, auch wenn LLeMbas geschlossen ist.",
        "Notifications on while LLeMbas is open.": "Benachrichtigungen an, solange LLeMbas geöffnet ist.",
        "Let LLeMbas tell you when a reply, a report or a scheduled run arrives while you are looking at something else?": "Soll LLeMbas Ihnen Bescheid geben, wenn eine Antwort, ein Bericht oder ein geplanter Lauf eintrifft, während Sie sich etwas anderes ansehen?",
        "Turn on": "Einschalten",
        "Not now": "Nicht jetzt",
        "Turn off notifications": "Benachrichtigungen ausschalten",
        "Blocked for this site in your browser's settings, which is the only place that can be undone.": "Für diese Seite in den Einstellungen Ihres Browsers blockiert — nur dort lässt sich das rückgängig machen.",
        "On in this browser. Nothing is shown while you are looking at the page.": "In diesem Browser an. Solange Sie die Seite ansehen, wird nichts angezeigt.",
        "Off in this browser.": "In diesem Browser aus.",
        "Stop generating": "Generierung anhalten",
        "Send — it waits for its turn": "Senden — wartet, bis es an der Reihe ist",
        "Sent now, taken in at the reply's next step or after it": "Jetzt gesendet, aufgenommen beim nächsten Schritt der Antwort oder danach",
        "the connection's own default": "der eigene Standard der Verbindung",
        "Choose a connection first — there is nothing to browse without one.": "Wählen Sie zuerst eine Verbindung — ohne sie gibt es nichts zu durchsuchen.",
    }
)

# --- 2.2.0: Anthropic connections and provider names -------------------------------------------
MESSAGES.update(
    {
        "Provider name": "Anbietername",
        "Type": "Typ",
        "How clients such as the LLeMbas CLI name this connection's models: <code>provider/model</code>, for example <code>llama/bonsai</code>. Lowercase letters, digits and hyphens, unique among connections.": "So benennen Clients wie die LLeMbas CLI die Modelle dieser Verbindung: <code>provider/model</code>, zum Beispiel <code>llama/bonsai</code>. Kleinbuchstaben, Ziffern und Bindestriche, eindeutig unter den Verbindungen.",
        "llama": "llama",
        "A provider name is 1 to 32 lowercase letters, digits and hyphens, and starts with a letter or a digit.": "Ein Anbietername besteht aus 1 bis 32 Kleinbuchstaben, Ziffern und Bindestrichen und beginnt mit einem Buchstaben oder einer Ziffer.",
        "The connection “%(name)s” already uses the provider name “%(provider)s”.": "Die Verbindung „%(name)s“ verwendet den Anbieternamen „%(provider)s“ bereits.",
    }
)

# --- 2.3.0: sidebar and search -------------------------------------------
MESSAGES.update(
    {
        "Which sessions to show on %(device)s": "Welche Sitzungen auf %(device)s angezeigt werden",
        "All sessions": "Alle Sitzungen",
        "Active sessions": "Aktive Sitzungen",
        "Archived sessions": "Archivierte Sitzungen",
        "No archived sessions.": "Keine archivierten Sitzungen.",
        "No active sessions.": "Keine aktiven Sitzungen.",
        "Search…": "Suchen…",
        "What to search": "Was durchsucht wird",
        "Chats & agent sessions": "Chats und Agent-Sitzungen",
        "Type a word or a phrase to search everything you have here.": "Geben Sie ein Wort oder eine Wendung ein, um alles zu durchsuchen, was Sie hier haben.",
        "Show all": "Alle anzeigen",
        "archived": "archiviert",
        "Nothing matches “%(q)s”.": "Nichts passt zu „%(q)s“.",
    }
)

# --- 2.3.0: settings window and composer ---------------------------------
MESSAGES.update(
    {
        "Open as a page": "Als Seite öffnen",
    }
)

# --- 2.4.0: credits worth money, every AI service over the API, the phone folder menu ---
MESSAGES.update(
    {
        "More for the folder %(name)s": "Mehr zum Ordner %(name)s",
        "Price": "Preis",
        "Chat models answer through the API's chat completions; embedding models only through its embeddings. A model saved before this existed answers both until it is set.": "Chat-Modelle antworten über die Chat-Completions der API, Embedding-Modelle nur über ihre Embeddings. Ein Modell, das vor dieser Einstellung gespeichert wurde, beantwortet beides, bis sie gesetzt ist.",
        "Cost": "Kosten",
    }
)

# --- 1.1: the agent's directory, picked from the ones already used ---------------
MESSAGES.update(
    {
        "Choose a directory": "Verzeichnis wählen",
        "Recent directories": "Zuletzt verwendete Verzeichnisse",
        "Browse…": "Durchsuchen…",
        "Choose the directory this agent works in before sending.": "Wählen Sie vor dem Senden das Verzeichnis, in dem dieser Agent arbeitet.",
    }
)

MESSAGES.update(
    {
        "Local": "Lokal",
        "This computer (%(host)s)": "Dieser Computer (%(host)s)",
        "This computer allows no folders yet. Add one in LLeMbas Desktop, or run <code>%(command)s</code>.": "Dieser Computer gibt noch keinen Ordner frei. Fügen Sie einen in LLeMbas Desktop hinzu oder führen Sie <code>%(command)s</code> aus.",
        "This device allows no folders yet. Add one there in LLeMbas Desktop, or run <code>%(command)s</code> on it.": "Dieses Gerät gibt noch keinen Ordner frei. Fügen Sie dort einen in LLeMbas Desktop hinzu oder führen Sie dort <code>%(command)s</code> aus.",
    }
)

# --- 1.3: the plan in every chat, and Plan mode for a chat --------------------
MESSAGES.update(
    {
        "An older override, saved for <code>%(key)s</code> before it became part of this fragment — review it: it is no longer sent.": "Eine ältere Anpassung, gespeichert für <code>%(key)s</code>, bevor es Teil dieses Fragments wurde — prüfen Sie sie: sie wird nicht mehr gesendet.",
        "Mode": "Modus",
        "Answers and acts as usual.": "Antwortet und handelt wie gewohnt.",
        "Normal": "Normal",
        "Approving ends Plan mode: the model carries the plan out in this reply, keeping it current above the message box.": "Mit der Freigabe endet der Plan-Modus: Das Modell führt den Plan in dieser Antwort aus und hält ihn über dem Eingabefeld aktuell.",
        "Carry out this plan? The model works through it in order, and the plan stays above the message box while it does.": "Diesen Plan ausführen? Das Modell arbeitet ihn der Reihe nach ab, und der Plan bleibt dabei über dem Eingabefeld stehen.",
        "Show the whole plan": "Den ganzen Plan anzeigen",
        "This chat has no plan.": "Dieser Chat hat keinen Plan.",
        "Waiting for you to look at the plan…": "Wartet darauf, dass Sie sich den Plan ansehen…",
        "Ask it something back": "Etwas zurückfragen",
        "Your question for the model…": "Ihre Frage an das Modell…",
        "Keeping a plan": "Einen Plan führen",
    }
)

# --- 1.6.0: background jobs, the helper panel, the subagent preamble ---
MESSAGES["A reply can hand a self-contained piece of work to a second model that runs on its own and reports back — several at once, which is what makes research fan out instead of queueing, and in the background, so the reply carries on and is told when the subagent is done. There are two kinds: explore, which reads and searches only, and general, which may also change things the chat itself may change. An agent chat's subagents run on its linked device instead, under the device's own limits."] = 'Eine Antwort kann ein abgeschlossenes Stück Arbeit an ein zweites Modell geben, das für sich arbeitet und berichtet — mehrere zugleich, weshalb sich Recherche verzweigt statt anzustehen; und im Hintergrund, so dass die Antwort weiterläuft und erfährt, wenn der Subagent fertig ist. Es gibt zwei Arten: explore, das nur liest und sucht, und general, das auch ändern darf, was der Chat selbst ändern darf. Die Subagenten eines Agenten-Chats laufen stattdessen auf dessen verbundenem Gerät, unter dessen eigenen Grenzen.'
MESSAGES['%(job)s asks:'] = '%(job)s fragt:'
MESSAGES['Subagent'] = 'Subagent'
MESSAGES['Close the subagent panel'] = 'Subagent-Panel schließen'
MESSAGES['Open a subagent from the strip above the composer to follow it here.'] = 'Öffnen Sie einen Subagenten aus der Leiste über dem Eingabefeld, um ihm hier zu folgen.'
MESSAGES['subagent'] = 'Subagent'
MESSAGES['Stop'] = 'Stoppen'
MESSAGES['A message to the subagent'] = 'Eine Nachricht an den Subagenten'
MESSAGES['Message the subagent — it reads it at its next step; a finished one is started again with it'] = 'Dem Subagenten schreiben — er liest es bei seinem nächsten Schritt; ein fertiger wird damit neu gestartet'
MESSAGES['From the main agent'] = 'Vom Hauptagenten'
MESSAGES['The task'] = 'Der Auftrag'
MESSAGES['Working…'] = 'Arbeitet…'
MESSAGES['Background jobs'] = 'Hintergrundaufträge'
MESSAGES['Running for %(time)s'] = 'Läuft seit %(time)s'
MESSAGES['Follow this subagent'] = 'Diesem Subagenten folgen'
MESSAGES['Follow this subagent and message it'] = 'Diesem Subagenten folgen und ihm schreiben'
MESSAGES['Stop %(job)s'] = '%(job)s stoppen'
MESSAGES['Stop this job'] = 'Diesen Auftrag stoppen'
MESSAGES['Scheduled run'] = 'Geplanter Lauf'
MESSAGES['The device did not answer in time.'] = 'Das Gerät hat nicht rechtzeitig geantwortet.'
MESSAGES['Not done: %(error)s'] = 'Nicht ausgeführt: %(error)s'
MESSAGES['That job is not here any more.'] = 'Dieser Auftrag ist nicht mehr da.'
MESSAGES['Write something to send.'] = 'Schreiben Sie etwas zum Senden.'
MESSAGES["That subagent's conversation is gone, so it cannot be messaged."] = 'Die Unterhaltung dieses Subagenten ist weg, ihm kann nicht mehr geschrieben werden.'
MESSAGES['running'] = 'läuft'
MESSAGES['done'] = 'fertig'
MESSAGES['lost'] = 'verloren'

# The app's door (/api/v2) and its notifications.
MESSAGES['Read and write your chats, as the web interface does'] = 'Ihre Chats lesen und schreiben, wie es die Weboberfläche tut'
MESSAGES['A reply is waiting'] = 'Eine Antwort wartet'
MESSAGES['A reply is waiting for your answer.'] = 'Eine Antwort wartet auf Ihre Rückmeldung.'
MESSAGES['That push address cannot be used: it must be https and reachable from here.'] = 'Diese Push-Adresse ist nicht verwendbar: Sie muss https sein und von hier erreichbar.'

# Stopping a reply, and why the microphone did not open.
MESSAGES['Stopping…'] = 'Wird angehalten…'
MESSAGES['The reply could not be stopped. Try again.'] = 'Die Antwort konnte nicht angehalten werden. Versuchen Sie es erneut.'
MESSAGES["Microphone access was refused. Allow it for this site in the browser's address bar or site settings, then try again."] = 'Der Zugriff auf das Mikrofon wurde verweigert. Erlauben Sie ihn für diese Website in der Adressleiste oder in den Website-Einstellungen des Browsers und versuchen Sie es erneut.'
MESSAGES['No microphone was found. Connect one, or check that the system can see it, then try again.'] = 'Es wurde kein Mikrofon gefunden. Schließen Sie eines an oder prüfen Sie, ob das System es erkennt, und versuchen Sie es erneut.'
MESSAGES['The microphone is in use by another application, or the system would not let the browser open it.'] = 'Das Mikrofon wird von einer anderen Anwendung verwendet, oder das System hat dem Browser das Öffnen nicht erlaubt.'
MESSAGES['The microphone could not be opened (%(error)s).'] = 'Das Mikrofon konnte nicht geöffnet werden (%(error)s).'
MESSAGES['Move to folder'] = 'In Ordner verschieben'
MESSAGES['Move'] = 'Verschieben'
MESSAGES['No folder'] = 'Kein Ordner'
MESSAGES['Order saved.'] = 'Reihenfolge gespeichert.'
MESSAGES['Move %(name)s: drag, or use the arrow keys'] = '%(name)s verschieben: ziehen oder die Pfeiltasten verwenden'
MESSAGES['Drag to put in order'] = 'Zum Sortieren ziehen'
MESSAGES['That did not work.'] = 'Das hat nicht geklappt.'
MESSAGES['That file is not here any more.'] = 'Diese Datei ist nicht mehr da.'
MESSAGES['That is not a path.'] = 'Das ist kein Pfad.'
MESSAGES['That did not work: %(reason)s'] = 'Das hat nicht geklappt: %(reason)s'
MESSAGES['Uploaded.'] = 'Hochgeladen.'
MESSAGES['Nothing was uploaded.'] = 'Es wurde nichts hochgeladen.'
MESSAGES['A folder needs a name without a slash.'] = 'Ein Ordner braucht einen Namen ohne Schrägstrich.'
MESSAGES['Folder made.'] = 'Ordner angelegt.'
MESSAGES['A name cannot be empty or hold a slash.'] = 'Ein Name darf weder leer sein noch einen Schrägstrich enthalten.'
MESSAGES['Moved.'] = 'Verschoben.'
MESSAGES['Moved to the trash.'] = 'In den Papierkorb verschoben.'
MESSAGES['Restored.'] = 'Wiederhergestellt.'
MESSAGES['That version is the current one again.'] = 'Diese Version ist wieder die aktuelle.'
MESSAGES['This file changed since you opened it. Reload it, or save anyway.'] = 'Die Datei hat sich geändert, seit Sie sie geöffnet haben. Laden Sie sie neu oder speichern Sie trotzdem.'
MESSAGES['The files are back as they were.'] = 'Die Dateien sind wieder, wie sie waren.'
MESSAGES['Nothing to put back.'] = 'Es gibt nichts zurückzusetzen.'
MESSAGES['No access'] = 'Kein Zugriff'
MESSAGES['Read only'] = 'Nur lesen'
MESSAGES['Read and write'] = 'Lesen und schreiben'
MESSAGES['Read, write and commit'] = 'Lesen, schreiben und committen'
MESSAGES['That data group no longer exists.'] = 'Diese Datengruppe gibt es nicht mehr.'
MESSAGES['What this chat may reach'] = 'Worauf dieser Chat zugreifen darf'
MESSAGES['Tick what the chat may use, and how. Everything below a ticked folder comes with it unless you tick it differently. The chat never gets more than you have yourself, or more than its data group allows.'] = 'Haken Sie an, was der Chat verwenden darf und wie. Alles unter einem angehakten Ordner gehört dazu, sofern Sie es nicht anders anhaken. Der Chat bekommt nie mehr, als Sie selbst haben, und nie mehr, als seine Datengruppe erlaubt.'
MESSAGES["Back to the data group's preset."] = 'Zurück zur Voreinstellung der Datengruppe.'
MESSAGES['What a new chat in %(name)s starts with'] = 'Womit ein neuer Chat in %(name)s beginnt'
MESSAGES['A new chat in this data group may reach what is ticked here, until somebody changes it for their chat. Nothing ticked means everything its person has.'] = 'Ein neuer Chat in dieser Datengruppe darf auf das zugreifen, was hier angehakt ist, bis jemand es für seinen Chat ändert. Nichts angehakt heißt: alles, was seine Person hat.'
MESSAGES['The most any chat in %(name)s may reach'] = 'Das Höchste, worauf ein Chat in %(name)s zugreifen darf'
MESSAGES['A ceiling: no chat in this data group goes above what is ticked here, whatever it is granted. Nothing ticked means no ceiling.'] = 'Eine Obergrenze: Kein Chat dieser Datengruppe geht über das hier Angehakte hinaus, was auch immer ihm gewährt wird. Nichts angehakt heißt: keine Obergrenze.'
MESSAGES['Where you are'] = 'Wo Sie sind'
MESSAGES['All files'] = 'Alle Dateien'
MESSAGES['Files'] = 'Dateien'
MESSAGES['%(n)s B'] = '%(n)s B'
MESSAGES['%(n)s KB'] = '%(n)s KB'
MESSAGES['%(n)s MB'] = '%(n)s MB'
MESSAGES['This chat has not changed any file that is still as it left it.'] = 'Dieser Chat hat keine Datei geändert, die noch so ist, wie er sie hinterlassen hat.'
MESSAGES['Undoing puts these files back as they were before this chat changed them. Each is a new version, so this can be undone too.'] = 'Rückgängig macht diese Dateien wieder so, wie sie vor den Änderungen dieses Chats waren. Jede wird eine neue Version, also lässt sich auch das rückgängig machen.'
MESSAGES['restored'] = 'wird wiederhergestellt'
MESSAGES['moved to the trash'] = 'in den Papierkorb'
MESSAGES['changed since by someone else'] = 'seitdem von jemand anderem geändert'
MESSAGES['Undo these changes'] = 'Diese Änderungen rückgängig machen'
MESSAGES['an older version'] = 'eine ältere Version'
MESSAGES['Open in Files'] = 'In Dateien öffnen'
MESSAGES['Download'] = 'Herunterladen'
MESSAGES['The page in %(name)s, sealed off from this application'] = 'Die Seite in %(name)s, von dieser Anwendung abgeschottet'
MESSAGES['This file is too large to show here.'] = 'Diese Datei ist zu groß, um sie hier anzuzeigen.'
MESSAGES['This is not a text file, so it is not shown here.'] = 'Das ist keine Textdatei, deshalb wird sie hier nicht angezeigt.'
MESSAGES['Contents of %(name)s'] = 'Inhalt von %(name)s'
MESSAGES['Not here'] = 'Nicht hier'
MESSAGES['Back to all files'] = 'Zurück zu allen Dateien'
MESSAGES['What should the folder be called?'] = 'Wie soll der Ordner heißen?'
MESSAGES['Upload'] = 'Hochladen'
MESSAGES['Trash'] = 'Papierkorb'
MESSAGES['Folders'] = 'Ordner'
MESSAGES['Contents'] = 'Inhalt'
MESSAGES['Nothing here yet. Not even a hobbit hole.'] = 'Hier ist noch nichts. Nicht einmal eine Hobbithöhle.'
MESSAGES['Size'] = 'Größe'
MESSAGES['Changed'] = 'Geändert'
MESSAGES['Actions'] = 'Aktionen'
MESSAGES['read-only'] = 'schreibgeschützt'
MESSAGES['mount'] = 'Einhängung'
MESSAGES['More for %(name)s'] = 'Mehr für %(name)s'
MESSAGES['Open'] = 'Öffnen'
MESSAGES['New name'] = 'Neuer Name'
MESSAGES['Rename'] = 'Umbenennen'
MESSAGES['Move “%(name)s” to the trash? It can be restored from there.'] = '„%(name)s“ in den Papierkorb verschieben? Von dort lässt es sich wiederherstellen.'
MESSAGES['Move to the trash'] = 'In den Papierkorb'
MESSAGES['The trash is empty.'] = 'Der Papierkorb ist leer.'
MESSAGES["Undo this chat's changes"] = 'Änderungen dieses Chats rückgängig machen'
MESSAGES['Versions'] = 'Versionen'
MESSAGES['by a chat'] = 'von einem Chat'
MESSAGES['Make this version the current one? The current one stays in the list.'] = 'Diese Version zur aktuellen machen? Die jetzige bleibt in der Liste.'
MESSAGES['Restore this version'] = 'Diese Version wiederherstellen'
MESSAGES['current'] = 'aktuell'
MESSAGES['Give %(path)s a level of its own'] = '%(path)s eine eigene Stufe geben'
MESSAGES['Show what is in %(path)s'] = 'Zeigen, was in %(path)s ist'
MESSAGES['Passage: not listed or readable; what is ticked inside is reached by its full path.'] = 'Durchgang: wird weder aufgelistet noch gelesen; Angehaktes darin erreicht man nur über den vollen Pfad.'
MESSAGES['As above: %(level)s'] = 'Wie oben: %(level)s'
MESSAGES['Level for %(path)s'] = 'Stufe für %(path)s'
MESSAGES['Access'] = 'Zugriff'
MESSAGES['Which rules'] = 'Welche Regeln'
MESSAGES['Preset'] = 'Voreinstellung'
MESSAGES['Ceiling'] = 'Obergrenze'
MESSAGES["These are this chat's own grants."] = 'Das sind die eigenen Freigaben dieses Chats.'
MESSAGES["This chat uses its data group's preset. Saving here gives it grants of its own."] = 'Dieser Chat nutzt die Voreinstellung seiner Datengruppe. Speichern hier gibt ihm eigene Freigaben.'
MESSAGES['Another path'] = 'Ein anderer Pfad'
MESSAGES['For a path not in the tree above.'] = 'Für einen Pfad, der oben nicht im Baum steht.'
MESSAGES['Level'] = 'Stufe'
MESSAGES["Use the data group's preset"] = 'Voreinstellung der Datengruppe verwenden'
MESSAGES['Saving…'] = 'Wird gespeichert…'
MESSAGES['Not saved: the server could not be reached.'] = 'Nicht gespeichert: Der Server war nicht erreichbar.'
MESSAGES['Changed meanwhile'] = 'Inzwischen geändert'
MESSAGES['Save anyway'] = 'Trotzdem speichern'
MESSAGES['Not saved yet'] = 'Noch nicht gespeichert'
MESSAGES['What it may reach'] = 'Worauf er zugreifen darf'
MESSAGES['Undo its file changes'] = 'Seine Dateiänderungen rückgängig machen'
MESSAGES['Files a chat may reach'] = 'Dateien, auf die ein Chat zugreifen darf'
MESSAGES['The preset is what a new chat in this group may reach until its person changes it; the ceiling is the most any chat in it ever may.'] = 'Die Voreinstellung ist, worauf ein neuer Chat dieser Gruppe zugreifen darf, bis seine Person sie ändert; die Obergrenze ist das Höchste, was ein Chat darin je darf.'
MESSAGES['%(failed)s did not answer (%(reason)s), so %(answered)s answered instead.'] = '%(failed)s hat nicht geantwortet (%(reason)s), deshalb hat %(answered)s geantwortet.'
MESSAGES['%(ms)s ms'] = '%(ms)s ms'
MESSAGES['%(name)s: balance read.'] = '%(name)s: Guthaben abgefragt.'
MESSAGES['Add a fallback model'] = 'Ausweichmodell hinzufügen'
MESSAGES['Add a fallback model…'] = 'Ausweichmodell hinzufügen…'
MESSAGES['An admin key from the provider, used only to read what the account has spent. Never used for a reply; a reply never fails on it.'] = 'Ein Admin-Schlüssel des Anbieters, nur um abzufragen, was das Konto ausgegeben hat. Nie für eine Antwort verwendet; eine Antwort scheitert nie daran.'
MESSAGES['Answering'] = 'Antwortet'
MESSAGES['Answering with an error'] = 'Antwortet mit einem Fehler'
MESSAGES['As detected'] = 'Wie erkannt'
MESSAGES['As the provider reported it at %(when)s UTC.'] = 'So vom Anbieter um %(when)s UTC gemeldet.'
MESSAGES['Balance at the provider'] = 'Guthaben beim Anbieter'
MESSAGES['Billing key (optional)'] = 'Abrechnungsschlüssel (optional)'
MESSAGES['Check balance'] = 'Guthaben abfragen'
MESSAGES['Default currency'] = 'Standardwährung'
MESSAGES['Detect'] = 'Erkennen'
MESSAGES['Detected: no'] = 'Erkannt: nein'
MESSAGES['Detected: yes'] = 'Erkannt: ja'
MESSAGES['Fallback models'] = 'Ausweichmodelle'
MESSAGES['Found:'] = 'Gefunden:'
MESSAGES['Loading a model'] = 'Lädt ein Modell'
MESSAGES['No'] = 'Nein'
MESSAGES['Not checked yet'] = 'Noch nicht geprüft'
MESSAGES['Not detected'] = 'Nicht erkannt'
MESSAGES['Not read yet.'] = 'Noch nicht abgefragt.'
MESSAGES['Nothing at that address answered like a model server.'] = 'Unter dieser Adresse hat nichts wie ein Modellserver geantwortet.'
MESSAGES['Nothing reported.'] = 'Nichts gemeldet.'
MESSAGES['OpenAI-compatible (llama.cpp, llama-swap, vLLM, Ollama, LM Studio)'] = 'OpenAI-kompatibel (llama.cpp, llama-swap, vLLM, Ollama, LM Studio)'
MESSAGES["OpenRouter's answer had no credits in it."] = 'Die Antwort von OpenRouter enthielt kein Guthaben.'
MESSAGES['Spent at the provider, last 30 days'] = 'Beim Anbieter ausgegeben, letzte 30 Tage'
MESSAGES['The address could not be asked.'] = 'Die Adresse konnte nicht abgefragt werden.'
MESSAGES['The endpoint says %(n)s tokens (%(source)s). A number typed here wins over it; empty follows the endpoint.'] = 'Der Endpunkt meldet %(n)s Tokens (%(source)s). Eine hier eingetragene Zahl hat Vorrang; leer folgt dem Endpunkt.'
MESSAGES['The key was refused for this.'] = 'Der Schlüssel wurde dafür abgelehnt.'
MESSAGES['The provider did not answer with a balance.'] = 'Der Anbieter hat kein Guthaben zurückgegeben.'
MESSAGES["The provider's own figure. A reply's cost here is this instance's estimate from the model's prices, or the provider's when it reports one."] = 'Die eigene Angabe des Anbieters. Die Kosten einer Antwort sind hier die Schätzung dieser Instanz aus den Modellpreisen oder die des Anbieters, wenn er sie meldet.'
MESSAGES['This needs an admin key from the provider (the billing key).'] = 'Dafür ist ein Admin-Schlüssel des Anbieters nötig (der Abrechnungsschlüssel).'
MESSAGES['This provider has no balance to read.'] = 'Dieser Anbieter hat kein abfragbares Guthaben.'
MESSAGES['Time to answer the last check'] = 'Antwortzeit der letzten Prüfung'
MESSAGES["Tried in order when this model's connection cannot be reached, times out or answers with a server error, before anything of the reply was written — never on Stop or on a refused request. A reply from a fallback says which model answered, and keeps its cost. Only models in the same data group are tried. None listed: a failed reply fails, as it always has."] = 'Der Reihe nach versucht, wenn die Verbindung dieses Modells nicht erreichbar ist, in eine Zeitüberschreitung läuft oder mit einem Serverfehler antwortet, bevor etwas von der Antwort geschrieben wurde — nie bei Stopp oder einer abgelehnten Anfrage. Eine Antwort eines Ausweichmodells sagt, welches Modell geantwortet hat, und behält dessen Kosten. Nur Modelle derselben Datengruppe werden versucht. Keines eingetragen: Eine fehlgeschlagene Antwort schlägt fehl, wie bisher.'
MESSAGES['Unreachable'] = 'Nicht erreichbar'
MESSAGES["What a new connection starts in, and what speech and picture prices are typed in. Each connection has its own currency for its models' prices (Connections). A reply keeps the currency it was priced in."] = 'Womit eine neue Verbindung beginnt und worin Sprach- und Bildpreise eingegeben werden. Jede Verbindung hat für die Preise ihrer Modelle eine eigene Währung (Verbindungen). Eine Antwort behält die Währung, in der sie berechnet wurde.'
MESSAGES["What this connection's model prices are typed in, and what a reply on one of them costs in."] = 'Worin die Modellpreise dieser Verbindung eingegeben werden und worin eine Antwort auf einem davon kostet.'
MESSAGES['What this model can do. <strong>reasoning</strong> shows the thinking block, <strong>vision</strong> lets images be sent, and <strong>tools</strong> is whether a tool list may be sent at all — turn it on for a model that does not support tool calling and every one of its replies fails. <strong>embeddings</strong> is the odd one out: it says this is not a chat model at all, but one that turns text into vectors, and it is what <a href="/admin/extraction">Extraction</a> picks from.'] = 'Was dieses Modell kann. <strong>reasoning</strong> zeigt den Denkblock, <strong>vision</strong> erlaubt das Senden von Bildern, und <strong>tools</strong> bestimmt, ob überhaupt eine Werkzeugliste gesendet werden darf — bei einem Modell ohne Werkzeugaufrufe eingeschaltet, scheitert jede seiner Antworten. <strong>embeddings</strong> fällt aus der Reihe: Es sagt, dass dies gar kein Chatmodell ist, sondern eines, das Text in Vektoren umwandelt, und daraus wählt <a href="/admin/extraction">Extraktion</a>.'
MESSAGES['Where the endpoint says (llama.cpp, Ollama, LM Studio, OpenRouter and others do), it is filled in on every refresh; what you choose here always wins.'] = 'Wo der Endpunkt es sagt (llama.cpp, Ollama, LM Studio, OpenRouter und andere tun es), wird es bei jeder Aktualisierung eingetragen; was Sie hier wählen, hat immer Vorrang.'
MESSAGES["With or without the trailing <code>/v1</code> — both are accepted. Empty takes the type's own address."] = 'Mit oder ohne abschließendes <code>/v1</code> — beides wird akzeptiert. Leer nimmt die eigene Adresse des Typs.'
MESSAGES['Yes'] = 'Ja'
MESSAGES['loading %(model)s'] = 'lädt %(model)s'
MESSAGES['Invites'] = 'Einladungen'
MESSAGES['The search index'] = 'Der Suchindex'
MESSAGES['Files and chats are indexed as they change. A rebuild reads everything again — after changing the embedding model, or when a search misses something it should find.'] = 'Dateien und Chats werden indiziert, sobald sie sich ändern. Ein Neuaufbau liest alles neu ein — nach einem Wechsel des Embedding-Modells oder wenn eine Suche etwas verfehlt, das sie finden sollte.'
MESSAGES['Waiting'] = 'Wartend'
MESSAGES['Failed'] = 'Fehlgeschlagen'
MESSAGES['Pieces'] = 'Teile'
MESSAGES['With a vector'] = 'Mit Vektor'
MESSAGES['The indexer is running.'] = 'Der Indexer läuft.'
MESSAGES['The indexer is not running.'] = 'Der Indexer läuft nicht.'
MESSAGES['Search by meaning is not available on this server (sqlite-vec is not installed); keywords still work.'] = 'Die Suche nach Bedeutung ist auf diesem Server nicht verfügbar (sqlite-vec ist nicht installiert); Stichwörter funktionieren weiterhin.'
MESSAGES['Last error: %(error)s'] = 'Letzter Fehler: %(error)s'
MESSAGES['The index could not be read.'] = 'Der Index konnte nicht gelesen werden.'
MESSAGES['Rebuild the index'] = 'Index neu aufbauen'
MESSAGES['Logo'] = 'Logo'
MESSAGES["Shown where the instance's name is: the sidebar, the sign-in page, the empty pages. A PNG, JPEG or WebP of at most 512 KB, square looks best. The themes stay as they are."] = 'Erscheint dort, wo der Name der Instanz steht: in der Seitenleiste, auf der Anmeldeseite, auf leeren Seiten. Ein PNG, JPEG oder WebP bis 512 KB, am besten quadratisch. Die Designs bleiben, wie sie sind.'
MESSAGES['The current logo'] = 'Das aktuelle Logo'
MESSAGES['Logo image'] = 'Logobild'
MESSAGES['Use the LLeMbas mark'] = 'Das LLeMbas-Zeichen verwenden'
MESSAGES['Python in the browser'] = 'Python im Browser'
MESSAGES['Offer the python tool in chats'] = 'Das python-Werkzeug in Chats anbieten'
MESSAGES["Code runs in the person's own browser (Pyodide), never on this server, with only the files the chat hands it, and only while a page of that chat is open."] = 'Der Code läuft im eigenen Browser der Person (Pyodide), nie auf diesem Server, nur mit den Dateien, die der Chat ihm übergibt, und nur, solange eine Seite dieses Chats offen ist.'
MESSAGES['Notifications when LLeMbas is closed'] = 'Benachrichtigungen, wenn LLeMbas geschlossen ist'
MESSAGES['Allow Web Push'] = 'Web Push erlauben'
MESSAGES["A browser's push goes through its maker's relay — Google's, Mozilla's or Apple's — so they learn that this server sent something, and when. What is sent says only that something is waiting, never what. Off, people still get notified while a LLeMbas page is open, and the apps are not affected."] = 'Ein Browser-Push läuft über den Dienst seines Herstellers — Google, Mozilla oder Apple —, die so erfahren, dass dieser Server etwas gesendet hat, und wann. Gesendet wird nur, dass etwas wartet, nie was. Ausgeschaltet werden Personen weiter benachrichtigt, solange eine LLeMbas-Seite offen ist, und die Apps sind nicht betroffen.'
MESSAGES['Nobody can sign up here on their own. Send a person an invite link: it works once, and stops working when it expires or is revoked.'] = 'Niemand kann sich hier selbst registrieren. Schicken Sie einer Person einen Einladungslink: Er funktioniert einmal und nicht mehr, sobald er abläuft oder widerrufen wird.'
MESSAGES['The invite link'] = 'Der Einladungslink'
MESSAGES['Copy it now: it is not shown again. Send it to the person it is for.'] = 'Kopieren Sie ihn jetzt: Er wird nicht noch einmal angezeigt. Schicken Sie ihn der Person, für die er gedacht ist.'
MESSAGES['Copy'] = 'Kopieren'
MESSAGES['New invite'] = 'Neue Einladung'
MESSAGES['For one address (optional)'] = 'Für eine Adresse (optional)'
MESSAGES['Then only that address can use it.'] = 'Dann kann nur diese Adresse sie verwenden.'
MESSAGES['Joins as'] = 'Tritt bei als'
MESSAGES['Organisation administrator'] = 'Administrator der Organisation'
MESSAGES['An organisation administrator manages its folders and people, not the instance.'] = 'Ein Administrator der Organisation verwaltet ihre Ordner und Personen, nicht die Instanz.'
MESSAGES['Expires after'] = 'Läuft ab nach'
MESSAGES['1 day'] = '1 Tag'
MESSAGES['%(n)s days'] = '%(n)s Tage'
MESSAGES['Make the link'] = 'Link erstellen'
MESSAGES['Waiting to be used'] = 'Warten auf Verwendung'
MESSAGES['Anyone with the link'] = 'Jeder mit dem Link'
MESSAGES['Revoke this invite? Its link stops working at once.'] = 'Diese Einladung widerrufen? Ihr Link funktioniert sofort nicht mehr.'
MESSAGES['Join %(name)s'] = '%(name)s beitreten'
MESSAGES['You are invited to %(org)s.'] = 'Sie sind zu %(org)s eingeladen.'
MESSAGES['Create my account'] = 'Mein Konto anlegen'
MESSAGES['Step 2 of 3: what this place is called. Next, a connection to your models.'] = 'Schritt 2 von 3: wie dieser Ort heißt. Danach eine Verbindung zu Ihren Modellen.'
MESSAGES['Organisation'] = 'Organisation'
MESSAGES['Your team, your company, or your own name. People you invite join it.'] = 'Ihr Team, Ihre Firma oder Ihr eigener Name. Wen Sie einladen, tritt ihr bei.'
MESSAGES['What a device asked to sign in here is told it is signing in to. Empty is LLeMbas.'] = 'Was einem Gerät bei der Anmeldung hier als Ziel genannt wird. Leer ist LLeMbas.'
MESSAGES['Next: a connection'] = 'Weiter: eine Verbindung'
MESSAGES['Skip for now'] = 'Vorerst überspringen'
MESSAGES['Memory import'] = 'Gedächtnis importieren'
MESSAGES['Back to Memory'] = 'Zurück zum Gedächtnis'
MESSAGES['Imported'] = 'Importiert'
MESSAGES['Nothing.'] = 'Nichts.'
MESSAGES['Left out'] = 'Ausgelassen'
MESSAGES['Export, import, forget'] = 'Exportieren, importieren, vergessen'
MESSAGES['Export'] = 'Exportieren'
MESSAGES['Every data group'] = 'Alle Datengruppen'
MESSAGES['A file you can import here or on another LLeMbas.'] = 'Eine Datei, die Sie hier oder auf einem anderen LLeMbas importieren können.'
MESSAGES['Import'] = 'Importieren'
MESSAGES['Into which data group'] = 'In welche Datengruppe'
MESSAGES['The group of the same name'] = 'Die Gruppe gleichen Namens'
MESSAGES['A memory export from LLeMbas 1.7 or later. What cannot be taken is listed, with the reason.'] = 'Ein Gedächtnisexport aus LLeMbas 1.7 oder neuer. Was nicht übernommen werden kann, wird mit Grund aufgeführt.'
MESSAGES['Forget every memory in this data group? This cannot be undone; export first if you may want them.'] = 'Alle Erinnerungen in dieser Datengruppe vergessen? Das lässt sich nicht rückgängig machen; exportieren Sie zuerst, falls Sie sie noch brauchen.'
MESSAGES['Forget all'] = 'Alles vergessen'
MESSAGES['Every memory in that data group, at once.'] = 'Alle Erinnerungen dieser Datengruppe auf einmal.'
MESSAGES['The logo can be at most 512 KB.'] = 'Das Logo darf höchstens 512 KB groß sein.'
MESSAGES['The logo must be a PNG, JPEG or WebP image.'] = 'Das Logo muss ein PNG-, JPEG- oder WebP-Bild sein.'
MESSAGES['Logo saved.'] = 'Logo gespeichert.'
MESSAGES['The LLeMbas mark is back.'] = 'Das LLeMbas-Zeichen ist zurück.'
MESSAGES['The index is being rebuilt.'] = 'Der Index wird neu aufgebaut.'
MESSAGES['This invite link is not valid.'] = 'Dieser Einladungslink ist ungültig.'
MESSAGES['This invite has already been used.'] = 'Diese Einladung wurde bereits verwendet.'
MESSAGES['This invite has expired.'] = 'Diese Einladung ist abgelaufen.'
MESSAGES['This invite is for another email address.'] = 'Diese Einladung gilt für eine andere E-Mail-Adresse.'
MESSAGES['Only an administrator can invite.'] = 'Nur ein Administrator kann einladen.'
MESSAGES['That invite is not there any more.'] = 'Diese Einladung gibt es nicht mehr.'
MESSAGES['Invite revoked.'] = 'Einladung widerrufen.'
MESSAGES['That data group is not one of yours.'] = 'Diese Datengruppe gehört nicht zu Ihren.'
MESSAGES['Forgotten.'] = 'Vergessen.'
MESSAGES['There was nothing to forget.'] = 'Es gab nichts zu vergessen.'
MESSAGES['Settings'] = 'Einstellungen'
MESSAGES['Python is not switched on here.'] = 'Python ist hier nicht eingeschaltet.'
MESSAGES['That file is too large to be a memory export.'] = 'Diese Datei ist zu groß für einen Gedächtnisexport.'
MESSAGES['That file is not an export: it is not JSON.'] = 'Diese Datei ist kein Export: Sie ist kein JSON.'
MESSAGES['That file is not a LLeMbas export.'] = 'Diese Datei ist kein LLeMbas-Export.'
MESSAGES['That export is format version %(theirs)s; this server reads version %(ours)s.'] = 'Dieser Export hat Formatversion %(theirs)s; dieser Server liest Version %(ours)s.'
MESSAGES["That is not a memory export; an administrator's export is imported elsewhere."] = 'Das ist kein Gedächtnisexport; der Export eines Administrators wird anderswo importiert.'
MESSAGES['That export has no items.'] = 'Dieser Export hat keine Einträge.'
MESSAGES['not an item'] = 'kein Eintrag'
MESSAGES['not a memory (%(type)s)'] = 'keine Erinnerung (%(type)s)'
MESSAGES['already remembered'] = 'schon gemerkt'
MESSAGES['Go to'] = 'Gehe zu'
MESSAGES['Go to a chat, a file, a model or a setting…'] = 'Zu einem Chat, einer Datei, einem Modell oder einer Einstellung…'
MESSAGES['Commands…'] = 'Befehle…'
MESSAGES['Something new is waiting for you.'] = 'Etwas Neues wartet auf Sie.'

# --- Import from 1.x and backups (2.0) ---
MESSAGES['%(count)s memories were left in the 1.x database; they come across only when asked for, into the account with the same email address.'] = '%(count)s Erinnerungen sind in der 1.x-Datenbank geblieben; sie kommen nur auf Wunsch herüber, in das Konto mit derselben E-Mail-Adresse.'
MESSAGES['%(count)s values'] = '%(count)s Werte'
MESSAGES['%(field)s did not open with the passphrase'] = '%(field)s ließ sich mit der Passphrase nicht öffnen'
MESSAGES['%(field)s was left out of the file (%(why)s)'] = '%(field)s fehlt in der Datei (%(why)s)'
MESSAGES['%(n)s notes, reports, skills and documents'] = '%(n)s Notizen, Berichte, Fertigkeiten und Dokumente'
MESSAGES['%(name)s in the backup does not match its manifest.'] = '%(name)s in der Sicherung passt nicht zu ihrem Manifest.'
MESSAGES['%(name)s in the backup failed its integrity check.'] = '%(name)s in der Sicherung hat die Integritätsprüfung nicht bestanden.'
MESSAGES['%(path)s belongs to someone else; the import cannot write there.'] = '%(path)s gehört jemand anderem; der Import kann dort nicht schreiben.'
MESSAGES['%d %B %Y, %H:%M UTC'] = '%d. %B %Y, %H:%M UTC'
MESSAGES['2.0 has no credits'] = '2.0 hat keine Credits'
MESSAGES['A backup from the list'] = 'Eine Sicherung aus der Liste'
MESSAGES["A backup is one file: both databases, the uploaded files, and a manifest with every file's size and checksum. Taken while the instance runs, without stopping anything."] = 'Eine Sicherung ist eine Datei: beide Datenbanken, die hochgeladenen Dateien und ein Manifest mit Größe und Prüfsumme jeder Datei. Erstellt im laufenden Betrieb, ohne etwas anzuhalten.'
MESSAGES['A restore is waiting: the backup of %(when)s (LLeMbas %(version)s). It is put in place when the service restarts; the data it replaces is kept in a pre-restore folder.'] = 'Eine Wiederherstellung wartet: die Sicherung vom %(when)s (LLeMbas %(version)s). Sie wird beim Neustart des Dienstes eingesetzt; die Daten, die sie ersetzt, bleiben in einem pre-restore-Ordner.'
MESSAGES['Also bring memories across'] = 'Auch Erinnerungen übernehmen'
MESSAGES["An administrator's export; a memory export is imported under Settings → Memory."] = 'Ein Export der Administration; ein Gedächtnis-Export wird unter Einstellungen → Gedächtnis importiert.'
MESSAGES['And the newest of each of the last this many weeks. Both 0 keeps every backup.'] = 'Und die neueste jeder der letzten so vielen Wochen. Beide 0 behalten jede Sicherung.'
MESSAGES['Back to Import'] = 'Zurück zum Import'
MESSAGES['Back up every day'] = 'Jeden Tag sichern'
MESSAGES['Back up now'] = 'Jetzt sichern'
MESSAGES['Backed up: %(name)s'] = 'Gesichert: %(name)s'
MESSAGES['Backups'] = 'Sicherungen'
MESSAGES['Backups go to <code>%(dir)s</code> (set by <code>LEMBAS_BACKUP_DIR</code>).'] = 'Sicherungen landen in <code>%(dir)s</code> (festgelegt durch <code>LEMBAS_BACKUP_DIR</code>).'
MESSAGES['Cancel the restore'] = 'Wiederherstellung abbrechen'
MESSAGES["Chats, attachments, connections and models are not imported from the 1.x database. Connections and models come from an administrator's export file."] = 'Chats, Anhänge, Verbindungen und Modelle werden nicht aus der 1.x-Datenbank importiert. Verbindungen und Modelle kommen aus einer Exportdatei der Administration.'
MESSAGES['Check and stage'] = 'Prüfen und vormerken'
MESSAGES['Choose a backup, or upload one.'] = 'Wählen Sie eine Sicherung oder laden Sie eine hoch.'
MESSAGES['Custom tool'] = 'Eigenes Werkzeug'
MESSAGES['Days to keep'] = 'Tage behalten'
MESSAGES['Delete this backup? It cannot be brought back.'] = 'Diese Sicherung löschen? Sie lässt sich nicht zurückholen.'
MESSAGES['Deleted.'] = 'Gelöscht.'
MESSAGES['Document'] = 'Dokument'
MESSAGES['Every 1.x account, a folder each'] = 'Jedes 1.x-Konto, jedes in einem eigenen Ordner'
MESSAGES['Export file'] = 'Exportdatei'
MESSAGES['From an export file'] = 'Aus einer Exportdatei'
MESSAGES['From the 1.x database'] = 'Aus der 1.x-Datenbank'
MESSAGES['Image workflow'] = 'Bild-Workflow'
MESSAGES['Import the file'] = 'Datei importieren'
MESSAGES['Import the library'] = 'Bibliothek importieren'
MESSAGES['Imported skills are files, not live skills yet: move one into <code>/skills/</code> or your own <code>skills/</code> folder to use it.'] = 'Importierte Fertigkeiten sind noch Dateien, keine aktiven Fertigkeiten: Verschieben Sie eine nach <code>/skills/</code> oder in Ihren eigenen Ordner <code>skills/</code>, um sie zu nutzen.'
MESSAGES["In the server's time."] = 'In der Zeit des Servers.'
MESSAGES['Into the 2.0 account with the same email address only, as memories rather than files.'] = 'Nur in das 2.0-Konto mit derselben E-Mail-Adresse, als Erinnerungen statt als Dateien.'
MESSAGES['It failed: %(error)s'] = 'Fehlgeschlagen: %(error)s'
MESSAGES['Its passphrase (optional)'] = 'Ihre Passphrase (optional)'
MESSAGES['Key'] = 'Schlüssel'
MESSAGES['Keys to enter again'] = 'Erneut einzugebende Schlüssel'
MESSAGES['LLeMbas 2.0 started on a new database. What you want to keep from 1.x comes across here, by hand: the library from the old database, and the connections, models and services from an export file made on 1.7.'] = 'LLeMbas 2.0 hat mit einer neuen Datenbank begonnen. Was Sie aus 1.x behalten wollen, kommt hier von Hand herüber: die Bibliothek aus der alten Datenbank und die Verbindungen, Modelle und Dienste aus einer auf 1.7 erstellten Exportdatei.'
MESSAGES['Last run: %(day)s.'] = 'Zuletzt: %(day)s.'
MESSAGES['MCP server'] = 'MCP-Server'
MESSAGES['None yet.'] = 'Noch keine.'
MESSAGES['None: upload one instead'] = 'Keine: stattdessen hochladen'
MESSAGES['Note'] = 'Notiz'
MESSAGES['Notes, reports, skills and knowledge from <code>lembas.db.old</code> become files in the home folder of the person you choose, under <code>Imported/</code>, private to them. The old file is only read. Running it again skips what is already there.'] = 'Notizen, Berichte, Fertigkeiten und Wissen aus <code>lembas.db.old</code> werden zu Dateien im Home-Ordner der gewählten Person, unter <code>Imported/</code>, nur für sie sichtbar. Die alte Datei wird nur gelesen. Ein erneuter Lauf überspringt, was schon da ist.'
MESSAGES['Only the library of (optional)'] = 'Nur die Bibliothek von (optional)'
MESSAGES['Or a backup file'] = 'Oder eine Sicherungsdatei'
MESSAGES['Passphrase'] = 'Passphrase'
MESSAGES['Passphrase for the secret key'] = 'Passphrase für den geheimen Schlüssel'
MESSAGES['Passphrase for the secret key (optional)'] = 'Passphrase für den geheimen Schlüssel (optional)'
MESSAGES['Report'] = 'Bericht'
MESSAGES['Schedule'] = 'Zeitplan'
MESSAGES['Scheduled backups carry the secret key under this passphrase. Empty leaves it out. Keep the passphrase somewhere other than this server.'] = 'Geplante Sicherungen enthalten den geheimen Schlüssel unter dieser Passphrase. Leer lässt ihn weg. Bewahren Sie die Passphrase woanders als auf diesem Server auf.'
MESSAGES['Skill'] = 'Fertigkeit'
MESSAGES['Stage this backup? At the next restart it replaces everything here.'] = 'Diese Sicherung vormerken? Beim nächsten Neustart ersetzt sie alles hier.'
MESSAGES['That account is not here.'] = 'Dieses Konto gibt es hier nicht.'
MESSAGES['That backup is format version %(theirs)s; this server reads version %(ours)s.'] = 'Diese Sicherung hat Formatversion %(theirs)s; dieser Server liest Version %(ours)s.'
MESSAGES['That backup is from LLeMbas %(theirs)s; only a backup from %(major)s.x can be restored here.'] = 'Diese Sicherung stammt von LLeMbas %(theirs)s; hier lässt sich nur eine Sicherung von %(major)s.x wiederherstellen.'
MESSAGES['That backup is not there any more.'] = 'Diese Sicherung ist nicht mehr da.'
MESSAGES['That file carries no encrypted keys.'] = 'Diese Datei enthält keine verschlüsselten Schlüssel.'
MESSAGES['That file is not a LLeMbas 1.x database.'] = 'Diese Datei ist keine LLeMbas-1.x-Datenbank.'
MESSAGES['That file is not a LLeMbas backup.'] = 'Diese Datei ist keine LLeMbas-Sicherung.'
MESSAGES["That file is too large to be an administrator's export."] = 'Diese Datei ist für einen Export der Administration zu groß.'
MESSAGES["That is not an administrator's export; memories are imported under Settings."] = 'Das ist kein Export der Administration; Erinnerungen werden unter Einstellungen importiert.'
MESSAGES['The 1.x database could not be read: %(error)s'] = 'Die 1.x-Datenbank ließ sich nicht lesen: %(error)s'
MESSAGES['The 1.x database has no account %(email)s.'] = 'Die 1.x-Datenbank hat kein Konto %(email)s.'
MESSAGES["The backup carries its secret key, but no passphrase was given: stored keys will open only if this instance's secret key is the one the backup was taken under."] = 'Die Sicherung enthält ihren geheimen Schlüssel, aber es wurde keine Passphrase angegeben: Gespeicherte Schlüssel öffnen sich nur, wenn diese Instanz denselben geheimen Schlüssel hat, unter dem die Sicherung erstellt wurde.'
MESSAGES["The backup does not carry its secret key: stored keys will open only if this instance's secret key is the one the backup was taken under."] = 'Die Sicherung enthält ihren geheimen Schlüssel nicht: Gespeicherte Schlüssel öffnen sich nur, wenn diese Instanz denselben geheimen Schlüssel hat, unter dem die Sicherung erstellt wurde.'
MESSAGES['The backup failed: %(error)s'] = 'Die Sicherung ist fehlgeschlagen: %(error)s'
MESSAGES['The backup has no database in it.'] = 'In der Sicherung ist keine Datenbank.'
MESSAGES['The backup holds a file its manifest does not name: %(name)s'] = 'Die Sicherung enthält eine Datei, die ihr Manifest nicht nennt: %(name)s'
MESSAGES['The backup is checked and staged. It is put in place when the service restarts.'] = 'Die Sicherung ist geprüft und vorgemerkt. Sie wird beim Neustart des Dienstes eingesetzt.'
MESSAGES["The backup is checked first: its manifest, every file's checksum, both databases' integrity, and that it is from this major version. Then it waits until the service restarts, and the data it replaces is moved aside, not deleted."] = 'Die Sicherung wird zuerst geprüft: ihr Manifest, die Prüfsumme jeder Datei, die Integrität beider Datenbanken und ob sie von dieser Hauptversion stammt. Dann wartet sie auf den Neustart des Dienstes, und die Daten, die sie ersetzt, werden beiseitegelegt, nicht gelöscht.'
MESSAGES['The backup is missing files its manifest names: %(names)s'] = 'In der Sicherung fehlen Dateien, die ihr Manifest nennt: %(names)s'
MESSAGES["The backup's manifest is damaged."] = 'Das Manifest der Sicherung ist beschädigt.'
MESSAGES['The database in the backup is not a 2.x database.'] = 'Die Datenbank in der Sicherung ist keine 2.x-Datenbank.'
MESSAGES['The file 1.7 writes under Admin → Updates → Export the configuration: connections and their models, data groups, speech, web search, image generation, custom tools and MCP servers. Anything 2.0 has no place for is listed, with the reason.'] = 'Die Datei, die 1.7 unter Administration → Updates → Konfiguration exportieren schreibt: Verbindungen und ihre Modelle, Datengruppen, Sprache, Websuche, Bilderzeugung, eigene Werkzeuge und MCP-Server. Wofür 2.0 keinen Platz hat, wird mit Grund aufgeführt.'
MESSAGES['The newest backup of each of the last this many days.'] = 'Die neueste Sicherung jedes der letzten so vielen Tage.'
MESSAGES["The one chosen at export, if the file's keys were encrypted. Without one, the keys were left out and are listed to be entered again."] = 'Die beim Export gewählte, falls die Schlüssel der Datei verschlüsselt wurden. Ohne sie fehlen die Schlüssel und werden zur erneuten Eingabe aufgeführt.'
MESSAGES['The staged restore is cancelled.'] = 'Die vorgemerkte Wiederherstellung ist abgebrochen.'
MESSAGES['The time must be HH:MM, for example 03:00.'] = 'Die Uhrzeit muss HH:MM sein, zum Beispiel 03:00.'
MESSAGES['There is no 1.x database here (lembas.db.old) to import from.'] = 'Hier gibt es keine 1.x-Datenbank (lembas.db.old), aus der importiert werden könnte.'
MESSAGES['There is no <code>lembas.db.old</code> in the data directory. The update to 2.0 leaves the 1.x database there under that name; a new installation has none.'] = 'Im Datenverzeichnis gibt es keine <code>lembas.db.old</code>. Das Update auf 2.0 lässt die 1.x-Datenbank dort unter diesem Namen; eine neue Installation hat keine.'
MESSAGES["They go into this person's organisation, and only this person can see them until they share them."] = 'Sie kommen in die Organisation dieser Person, und nur diese Person sieht sie, bis sie sie teilt.'
MESSAGES['This backup is zstd-compressed and the zstandard module is not installed here.'] = 'Diese Sicherung ist mit zstd komprimiert, und das Modul zstandard ist hier nicht installiert.'
MESSAGES["This file's keys are encrypted: enter the passphrase it was exported with."] = 'Die Schlüssel dieser Datei sind verschlüsselt: Geben Sie die Passphrase ein, mit der sie exportiert wurde.'
MESSAGES['This instance runs on a temporary secret key, so no backup carries one.'] = 'Diese Instanz läuft mit einem vorläufigen geheimen Schlüssel, daher enthält keine Sicherung einen.'
MESSAGES['Weeks to keep'] = 'Wochen behalten'
MESSAGES["When the backup carries its secret key, the stored keys are re-encrypted for this instance. Without it, they open only if this instance's secret key is the one the backup was taken under."] = 'Enthält die Sicherung ihren geheimen Schlüssel, werden die gespeicherten Schlüssel für diese Instanz neu verschlüsselt. Ohne ihn öffnen sie sich nur, wenn diese Instanz denselben geheimen Schlüssel hat, unter dem die Sicherung erstellt wurde.'
MESSAGES['Whose files they become'] = 'Wem die Dateien gehören'
MESSAGES["With more than one account, each one's files go into a folder named after it."] = 'Bei mehreren Konten kommen die Dateien jedes Kontos in einen nach ihm benannten Ordner.'
MESSAGES["With one, the secret key goes into the backup, encrypted, and a restore on another machine can read the stored keys. Empty uses the schedule's passphrase, if one is set."] = 'Damit kommt der geheime Schlüssel verschlüsselt in die Sicherung, und eine Wiederherstellung auf einem anderen Rechner kann die gespeicherten Schlüssel lesen. Leer verwendet die Passphrase des Zeitplans, falls eine gesetzt ist.'
MESSAGES['Worth knowing'] = 'Gut zu wissen'
MESSAGES['Wrong passphrase for the secret key in this backup.'] = 'Falsche Passphrase für den geheimen Schlüssel in dieser Sicherung.'
MESSAGES["Wrong passphrase, or the file's encryption is damaged."] = 'Falsche Passphrase, oder die Verschlüsselung der Datei ist beschädigt.'
MESSAGES['a connection with the provider name %(provider)s is already here'] = 'eine Verbindung mit dem Anbieternamen %(provider)s ist schon da'
MESSAGES['a data group of that name is already here'] = 'eine Datengruppe dieses Namens ist schon da'
MESSAGES['already here'] = 'schon da'
MESSAGES['already imported'] = 'schon importiert'
MESSAGES['as %(type)s'] = 'als %(type)s'
MESSAGES['capabilities as set by hand'] = 'Fähigkeiten von Hand gesetzt'
MESSAGES['it could not be saved: %(error)s'] = 'ließ sich nicht speichern: %(error)s'
MESSAGES['it failed in 1.x'] = 'in 1.x fehlgeschlagen'
MESSAGES['it has no model id'] = 'keine Modell-ID'
MESSAGES['it has no name'] = 'kein Name'
MESSAGES['it has no name or no http(s) address'] = 'kein Name oder keine http(s)-Adresse'
MESSAGES['its connection was not imported'] = 'seine Verbindung wurde nicht importiert'
MESSAGES["its name cannot be a skill's name"] = 'ihr Name kann kein Fertigkeitsname sein'
MESSAGES['its prices were credits, which 2.0 does not have'] = 'seine Preise waren Credits, die 2.0 nicht hat'
MESSAGES['neither the original file nor any extracted text is left'] = 'weder die Originaldatei noch extrahierter Text ist übrig'
MESSAGES['no account with that email address here (%(count)s memories)'] = 'hier gibt es kein Konto mit dieser E-Mail-Adresse (%(count)s Erinnerungen)'
MESSAGES['no place here for: %(keys)s'] = 'hier kein Platz für: %(keys)s'
MESSAGES['not a server this release can use'] = 'kein Server, den diese Version nutzen kann'
MESSAGES['not a tool this release can use'] = 'kein Werkzeug, das diese Version nutzen kann'
MESSAGES['not a workflow this release can use'] = 'kein Workflow, den diese Version nutzen kann'
MESSAGES["the default group is this instance's own"] = 'die Standardgruppe ist die eigene dieser Instanz'
MESSAGES['this release does not know what that is'] = 'diese Version weiß nicht, was das ist'
MESSAGES['this release has no such settings'] = 'diese Version hat keine solchen Einstellungen'
MESSAGES['unknown kind %(kind)s'] = 'unbekannte Art %(kind)s'
MESSAGES['user groups not here: %(names)s'] = 'Benutzergruppen, die es hier nicht gibt: %(names)s'
MESSAGES['with %(count)s earlier versions'] = 'mit %(count)s früheren Versionen'
# --- 2.0: files, memory and money in place of the library and credits ---------
MESSAGES["Its models read only this group's memories, and a chat started on one of them stays in it. Moving a connection does not move any data: it starts reading the other group."] = 'Ihre Modelle lesen nur die Erinnerungen dieser Gruppe, und ein Chat, der mit einem davon begonnen wurde, bleibt darin. Das Verschieben einer Verbindung verschiebt keine Daten: Sie liest dann die andere Gruppe.'
MESSAGES["A subagent cannot ask anybody anything, so nothing in its chat can stop for approval. It therefore gets only what this chat could already do <em>without</em> asking: it reads and searches files and the web, and the general kind writes what the chat may write. It cannot send subagents of its own; it can message the main agent, and be messaged, while it works. <strong>%(mode)s</strong> is not inherited: a subagent's task text can have come from a page it read."] = 'Ein Subagent kann niemanden etwas fragen, also kann in seinem Chat nichts auf eine Freigabe warten. Er bekommt deshalb nur, was dieser Chat schon <em>ohne</em> Nachfrage darf: er liest und durchsucht Dateien und das Web, und die Art general schreibt, was der Chat schreiben darf. Eigene Subagenten kann er nicht schicken; dem Hauptagenten kann er während der Arbeit schreiben und von ihm Nachrichten erhalten. <strong>%(mode)s</strong> wird nicht vererbt: der Auftragstext eines Subagenten kann von einer Seite stammen, die er gelesen hat.'
MESSAGES["A data group keeps one provider's models away from the data another provider's models have been given. Every connection is in one group. Its models read only that group's memories and personalities, and a chat stays in the group it was started in."] = 'Eine Datengruppe hält die Modelle eines Anbieters von den Daten fern, die den Modellen eines anderen Anbieters gegeben wurden. Jede Verbindung gehört zu genau einer Gruppe. Ihre Modelle lesen nur die Erinnerungen und Persönlichkeiten dieser Gruppe, und ein Chat bleibt in der Gruppe, in der er begonnen wurde.'
MESSAGES['Prices'] = 'Preise'
MESSAGES['API only switches off chats, agent chats, scheduled tasks and the MCP server; the API, device sign-in, Settings and Admin keep working.'] = '„Nur API“ schaltet Chats, Agent-Chats, geplante Aufgaben und den MCP-Server ab; die API, die Geräteanmeldung, die Einstellungen und die Administration funktionieren weiter.'
MESSAGES['Several tools can be called in one round, so this is not a count of tools. Leave room for at least two: <code>search</code> finds a passage and <code>read</code> reads the file around it, so a ceiling of one leaves files searchable and not readable. <code>0</code> means no ceiling, which is how an agent chat already works — those are bounded under <a href="/admin/agents">Agents</a> by time and tokens instead.'] = 'In einer Runde können mehrere Werkzeuge aufgerufen werden, das ist also keine Anzahl von Werkzeugen. Lassen Sie Platz für mindestens zwei: <code>search</code> findet eine Stelle und <code>read</code> liest die Datei um sie herum — bei einer Obergrenze von eins bleiben Dateien durchsuchbar, aber nicht lesbar. <code>0</code> bedeutet keine Obergrenze, so wie ein Agent-Chat ohnehin arbeitet — diese werden unter <a href="/admin/agents">Agenten</a> stattdessen durch Zeit und Tokens begrenzt.'
MESSAGES['A temporary chat is not listed in the sidebar, may read files and memories but cannot change anything — no files, memories, skills or schedules — and is deleted when its time is up. Keep this chat turns one into an ordinary chat.'] = 'Ein temporärer Chat wird nicht in der Seitenleiste aufgeführt, darf Dateien und Erinnerungen lesen, aber nichts ändern — keine Dateien, Erinnerungen, Fertigkeiten oder Zeitpläne — und wird gelöscht, wenn seine Zeit abgelaufen ist. „Chat behalten“ macht daraus einen gewöhnlichen Chat.'
MESSAGES['%d %B %Y'] = '%d. %B %Y'
MESSAGES["What a reply on this model costs, in %(currency)s. Per million tokens, except per reply. Each reply's cost is worked out when it is written and kept with it, so a new price does not change old replies. Leave everything empty to make it free — a local model, say."] = 'Was eine Antwort dieses Modells kostet, in %(currency)s. Pro Million Tokens, außer pro Antwort. Die Kosten jeder Antwort werden beim Schreiben berechnet und mit ihr gespeichert, ein neuer Preis ändert also keine alten Antworten. Alles leer lassen macht es kostenlos — etwa ein lokales Modell.'
MESSAGES['Delete %(email)s? Their chats and folders go with them.'] = '%(email)s löschen? Chats und Ordner verschwinden mit.'
MESSAGES['Their chats and folders go too.'] = 'Seine Chats und Ordner verschwinden mit.'
MESSAGES['Tools, skills, prompt, sampling'] = 'Werkzeuge, Fertigkeiten, Prompt, Sampling'
MESSAGES['Not listed in the sidebar. It may read your files but saves nothing, and is deleted %(after)s.'] = 'Nicht in der Seitenleiste aufgeführt. Er darf Ihre Dateien lesen, speichert aber nichts und wird %(after)s gelöscht.'
MESSAGES['This instance serves the API only; chats and scheduled tasks are switched off.'] = 'Diese Instanz stellt nur die API bereit; Chats und geplante Aufgaben sind abgeschaltet.'
MESSAGES['Short facts, shown to every model you talk to on every turn — so they are few and short on purpose. Anything longer belongs in a file. Nothing here is shared with anyone else, ever.'] = 'Kurze Fakten, die jedem Modell, mit dem Sie sprechen, in jeder Runde gezeigt werden — deshalb sind es mit Absicht wenige und kurze. Alles Längere gehört in eine Datei. Nichts hier wird je mit jemand anderem geteilt.'
MESSAGES['The LLeMbas CLI on your machines, signed in as you. Each uses your models, your memory and your usage, and an agent chat here runs on one of them — under its own limits — while it is linked.'] = 'Die LLeMbas CLI auf Ihren Rechnern, in Ihrem Namen angemeldet. Jede nutzt Ihre Modelle, Ihr Gedächtnis und Ihren Verbrauch, und ein Agent-Chat hier läuft auf einer davon — unter ihren eigenen Limits —, solange sie verbunden ist.'
MESSAGES["A model reads only the memories of the data group its connection is in, and a chat stays in the group it was started in. Two providers in different groups never see each other's."] = 'Ein Modell liest nur die Erinnerungen der Datengruppe, in der seine Verbindung ist, und ein Chat bleibt in der Gruppe, in der er begonnen wurde. Zwei Anbieter in verschiedenen Gruppen sehen nie die Daten des anderen.'
MESSAGES['Both of the above, and your schedules. Kept: your account, password, settings, groups, usage, and your files, which you delete from the file tree.'] = 'Beides oben, dazu Ihre Zeitpläne. Erhalten bleiben: Ihr Konto, Passwort, Ihre Einstellungen, Gruppen, Ihr Verbrauch und Ihre Dateien, die Sie im Dateibaum löschen.'
MESSAGES['Read and write your memory'] = 'Ihr Gedächtnis lesen und schreiben'
MESSAGES['Keep memories and skills of their own.'] = 'Eigene Erinnerungen und Fertigkeiten führen.'
MESSAGES['Keep memories and skills of your own.'] = 'Eigene Erinnerungen und Fertigkeiten führen.'
MESSAGES['Working with files'] = 'Arbeit mit Dateien'
MESSAGES['Running Python'] = 'Python ausführen'
MESSAGES['Recalling earlier results'] = 'Frühere Ergebnisse abrufen'
# --- Pyodide by download, apps beside devices (2.0) ---
MESSAGES['Pyodide %(version)s'] = 'Pyodide %(version)s'
MESSAGES['Downloading Pyodide'] = 'Pyodide wird heruntergeladen'
MESSAGES['Downloading: %(done)s of %(total)s'] = 'Wird heruntergeladen: %(done)s von %(total)s'
MESSAGES['On this server: %(count)s files, %(size)s, each checked against its pinned SHA-256.'] = 'Auf diesem Server: %(count)s Dateien, %(size)s, jede gegen ihr festgelegtes SHA-256 geprüft.'
MESSAGES["Not on this server. Downloading fetches %(count)s files (%(size)s) once, from the Pyodide release's own address, and refuses them unless each matches the SHA-256 pinned in this version of LLeMbas. Nothing is loaded from anywhere else afterwards."] = 'Nicht auf diesem Server. Das Herunterladen holt %(count)s Dateien (%(size)s) einmal von der eigenen Adresse der Pyodide-Veröffentlichung und weist sie zurück, wenn eine nicht dem in dieser LLeMbas-Version festgelegten SHA-256 entspricht. Danach wird nichts mehr von anderswo geladen.'
MESSAGES['The last download was refused: %(why)s'] = 'Der letzte Download wurde zurückgewiesen: %(why)s'
MESSAGES['Remove Pyodide?'] = 'Pyodide entfernen?'
MESSAGES['The python tool is switched off, and its files are deleted from this server. It can be downloaded again.'] = 'Das python-Werkzeug wird ausgeschaltet und seine Dateien werden von diesem Server gelöscht. Es kann erneut heruntergeladen werden.'
MESSAGES['Download Pyodide'] = 'Pyodide herunterladen'
MESSAGES['Pyodide is not on this server yet, so there is nothing to switch on.'] = 'Pyodide ist noch nicht auf diesem Server, es gibt also nichts einzuschalten.'
MESSAGES['app'] = 'App'
MESSAGES['Its agent may'] = 'Sein Agent darf'
MESSAGES['Apps'] = 'Apps'
MESSAGES['Sign this app out?'] = 'Diese App abmelden?'
MESSAGES['Pyodide is already on this server.'] = 'Pyodide ist bereits auf diesem Server.'
MESSAGES['Downloading Pyodide.'] = 'Pyodide wird heruntergeladen.'
MESSAGES['There is no Pyodide on this server to remove.'] = 'Auf diesem Server ist kein Pyodide, das entfernt werden könnte.'
MESSAGES['Pyodide removed, and the python tool switched off.'] = 'Pyodide entfernt und das python-Werkzeug ausgeschaltet.'
MESSAGES['The download was answered with %(status)s.'] = 'Der Download wurde mit %(status)s beantwortet.'
MESSAGES['%(name)s is larger than its pinned size.'] = '%(name)s ist größer als seine festgelegte Größe.'
MESSAGES['%(name)s is not its pinned size.'] = '%(name)s hat nicht seine festgelegte Größe.'
MESSAGES['%(name)s does not match its pinned SHA-256.'] = '%(name)s entspricht nicht seinem festgelegten SHA-256.'
MESSAGES['The download failed: %(why)s'] = 'Der Download ist fehlgeschlagen: %(why)s'
MESSAGES['LLeMbas %(label)s, a new major version, is out. It is not offered here: moving to a new major version is done by hand.'] = 'LLeMbas %(label)s ist erschienen, eine neue Hauptversion. Sie wird hier nicht angeboten: Der Wechsel auf eine neue Hauptversion erfolgt von Hand.'
MESSAGES['The update helper on this host was installed by an older release and does not keep to one major version: it would install a new major version as soon as one is out. Re-run the installer with <code>INSTALL_UPDATE_HELPER=1</code> to replace it.'] = 'Der Update-Helfer auf diesem Host wurde von einem älteren Release installiert und bleibt nicht bei einer Hauptversion: Er würde eine neue Hauptversion installieren, sobald sie erscheint. Führen Sie das Installationsprogramm mit <code>INSTALL_UPDATE_HELPER=1</code> erneut aus, um ihn zu ersetzen.'
MESSAGES['The update helper on this host would install %(label)s, a new major version. Re-run the installer with INSTALL_UPDATE_HELPER=1 to replace it; nothing was requested.'] = 'Der Update-Helfer auf diesem Host würde %(label)s installieren, eine neue Hauptversion. Führen Sie das Installationsprogramm mit INSTALL_UPDATE_HELPER=1 erneut aus, um ihn zu ersetzen; es wurde nichts angefordert.'

# The explorer in the whole window, new files, and Run (2.0.0-beta.2).
MESSAGES['A file needs a name without a slash.'] = 'Eine Datei braucht einen Namen ohne Schrägstrich.'
MESSAGES['Back to the current one'] = 'Zurück zur aktuellen'
MESSAGES['Back to your files'] = 'Zurück zu Ihren Dateien'
MESSAGES['Failed after %(s)s s'] = 'Fehlgeschlagen nach %(s)s s'
MESSAGES['Finished in %(s)s s'] = 'Fertig in %(s)s s'
MESSAGES['Home'] = 'Start'
MESSAGES['Make a file or a folder above, or drop files here to upload them.'] = 'Legen Sie oben eine Datei oder einen Ordner an, oder ziehen Sie Dateien hierher, um sie hochzuladen.'
MESSAGES['New file'] = 'Neue Datei'
MESSAGES['Python could not start.'] = 'Python konnte nicht starten.'
MESSAGES['Run'] = 'Ausführen'
MESSAGES['Run %(name)s with Python in this browser'] = '%(name)s mit Python in diesem Browser ausführen'
MESSAGES['Run with Python (Pyodide), in this browser'] = 'Mit Python (Pyodide) in diesem Browser ausführen'
MESSAGES['Running…'] = 'Läuft…'
MESSAGES['Starting Python… (the first run downloads it)'] = 'Python startet… (der erste Lauf lädt es herunter)'
MESSAGES['Stopped'] = 'Angehalten'
MESSAGES['There is already something called %(name)s here.'] = 'Hier gibt es schon etwas namens %(name)s.'
MESSAGES['What should the file be called? With its extension, such as notes.md.'] = 'Wie soll die Datei heißen? Mit Endung, etwa notes.md.'
MESSAGES['folder'] = 'Ordner'

# One way to say what an action did, and confirmations (2.0.0-beta.3).
MESSAGES['Not saved'] = 'Nicht gespeichert'
MESSAGES['Remove the uploaded logo and show the LLeMbas mark again?'] = 'Das hochgeladene Logo entfernen und wieder das LLeMbas-Zeichen zeigen?'
MESSAGES['Remove this model’s image? It gets its generated badge again.'] = 'Das Bild dieses Modells entfernen? Es bekommt wieder sein erzeugtes Abzeichen.'

# Teams: subagents that work together (2.0.0-beta.5, harness spec 3.2.0).
MESSAGES['Stop %(name)s'] = '%(name)s stoppen'
MESSAGES['Stop %(name)s and the members under it? They leave the team.'] = '%(name)s und die Mitglieder darunter stoppen? Sie verlassen das Team.'
MESSAGES['Room'] = 'Raum'
MESSAGES['report'] = 'Bericht'
MESSAGES['Nothing has been said in the room yet.'] = 'Im Raum wurde noch nichts gesagt.'
MESSAGES['That member is not on the team.'] = 'Dieses Mitglied ist nicht im Team.'
MESSAGES["You may not change the team's size."] = 'Sie dürfen die Teamgröße nicht ändern.'
MESSAGES['Team size for new chats saved.'] = 'Teamgröße für neue Chats gespeichert.'
MESSAGES['working'] = 'arbeitet'
# The activity panel: context, outputs, background tasks and the team's room.
MESSAGES['Outputs'] = 'Ausgaben'
MESSAGES['No files written in this chat yet.'] = 'In diesem Chat wurde noch keine Datei geschrieben.'
MESSAGES['Background tasks'] = 'Hintergrundaufgaben'
MESSAGES['Not measured.'] = 'Nicht gemessen.'
MESSAGES['%(percent)s%% used'] = '%(percent)s %% belegt'
MESSAGES['%(tokens)s tokens'] = '%(tokens)s Tokens'
MESSAGES['window unknown'] = 'Fenster unbekannt'
MESSAGES['%(percent)s%% of the context window used'] = '%(percent)s %% des Kontextfensters belegt'
MESSAGES['Counted by the model at the last reply; the categories are estimated.'] = 'Vom Modell bei der letzten Antwort gezählt; die Kategorien sind geschätzt.'
MESSAGES['Estimated: no reply has been counted yet.'] = 'Geschätzt: Noch keine Antwort wurde gezählt.'
MESSAGES['%(n)s tools offered.'] = '%(n)s Werkzeuge angeboten.'
MESSAGES['Activity'] = 'Aktivität'
MESSAGES['Close the activity panel'] = 'Aktivitätsbereich schließen'
MESSAGES['Loading…'] = 'Wird geladen…'
MESSAGES['%(steps)s steps, %(tools)s tools'] = '%(steps)s Schritte, %(tools)s Werkzeuge'
MESSAGES['Open its conversation'] = 'Seine Unterhaltung öffnen'
MESSAGES["Nothing runs in the background. Subagents, commands and a team's members show here while they work."] = 'Im Hintergrund läuft nichts. Subagenten, Befehle und die Mitglieder eines Teams erscheinen hier, solange sie arbeiten.'
MESSAGES['Nothing running.'] = 'Nichts läuft.'
MESSAGES['Team: %(size)s of %(max)s agents'] = 'Team: %(size)s von %(max)s Agenten'
MESSAGES['Show %(n)s more'] = '%(n)s weitere anzeigen'
MESSAGES['%(n)s finished'] = '%(n)s beendet'
MESSAGES['team member'] = 'Teammitglied'
MESSAGES['Open the activity panel'] = 'Aktivitätsbereich öffnen'
MESSAGES['Context, outputs, background tasks and the plan'] = 'Kontext, Ausgaben, Hintergrundaufgaben und der Plan'
MESSAGES['That agent is not here.'] = 'Dieser Agent ist nicht hier.'
MESSAGES['That agent is not running.'] = 'Dieser Agent läuft nicht.'
MESSAGES['queued'] = 'in der Warteschlange'
MESSAGES['waiting'] = 'wartet'
MESSAGES['%(n)s queued'] = '%(n)s in der Warteschlange'
MESSAGES['%(n)s waiting'] = '%(n)s warten'
MESSAGES['Messages'] = 'Nachrichten'
MESSAGES['Memory and personal'] = 'Gedächtnis und Persönliches'
MESSAGES['Instruction files'] = 'Anweisungsdateien'
MESSAGES['MCP tools'] = 'MCP-Werkzeuge'
MESSAGES['Since the last reply'] = 'Seit der letzten Antwort'
MESSAGES['Compaction buffer'] = 'Puffer für die Verdichtung'
MESSAGES['Free space'] = 'Freier Platz'

# Effort levels and the thinking switch, requests at once (2.0.0-beta.5, harness spec 3.2.0).
MESSAGES['Can switch thinking off'] = 'Kann das Nachdenken abschalten'
MESSAGES['Tick this when the model\'s server turns thinking off for a request that sends <span class="mono">chat_template_kwargs.enable_thinking: false</span> — Qwen3 and Nemotron 3 models on llama.cpp, vLLM or SGLang. A chat then offers <em>off</em>, which sends exactly that and no effort. Without it there is no off: sending no effort leaves such a model thinking at its server\'s default.'] = 'Aktivieren Sie dies, wenn der Server des Modells das Nachdenken für eine Anfrage abschaltet, die <span class="mono">chat_template_kwargs.enable_thinking: false</span> sendet — Qwen3- und Nemotron-3-Modelle auf llama.cpp, vLLM oder SGLang. Ein Chat bietet dann <em>aus</em> an, das genau dies und keinen Denkaufwand sendet. Ohne dies gibt es kein Aus: Ohne gesendeten Denkaufwand denkt ein solches Modell mit der Voreinstellung seines Servers.'
MESSAGES['Not set — medium where offered, else the first effort'] = 'Nicht festgelegt — medium, wo angeboten, sonst der erste Aufwand'
MESSAGES['off — thinking switched off'] = 'aus — Nachdenken abgeschaltet'
MESSAGES["default — the model's own"] = 'Standard — die Voreinstellung des Modells'
MESSAGES['Sent two ways at once, because there is no one field that works: OpenAI and vLLM read <span class="mono">reasoning_effort</span>, while llama.cpp drops it silently and reads only <span class="mono">chat_template_kwargs</span> — which is the route by which it reaches gpt-oss. Both go out on every chat with a model marked <strong>Reasoning</strong>: not set here, a chat starts at medium where the model takes it, else at its first effort. <em>Off</em> sends only <span class="mono">enable_thinking: false</span>, and a model not marked <strong>Reasoning</strong> is sent no effort at all.'] = 'Wird auf zwei Wegen zugleich gesendet, weil es kein einzelnes Feld gibt, das überall funktioniert: OpenAI und vLLM lesen <span class="mono">reasoning_effort</span>, während llama.cpp es stillschweigend verwirft und nur <span class="mono">chat_template_kwargs</span> liest — der Weg, auf dem es gpt-oss erreicht. Beides geht bei jedem Chat mit einem als <strong>Reasoning</strong> markierten Modell hinaus: Ist hier nichts festgelegt, beginnt ein Chat bei medium, wo das Modell es annimmt, sonst bei seinem ersten Aufwand. <em>Aus</em> sendet nur <span class="mono">enable_thinking: false</span>, und ein Modell ohne die Markierung <strong>Reasoning</strong> bekommt gar keinen Aufwand.'
MESSAGES['Requests at once'] = 'Anfragen gleichzeitig'
MESSAGES["How many requests the model's server answers at the same time — llama.cpp's slots, a vLLM batch. Empty is no limit, right for a hosted API. At 1 the model cannot be its own subagent, because the subagent would take the only slot and push out the conversation the server has cached, so the waiting reply would have to read it all again. Other models can still be its subagents."] = 'Wie viele Anfragen der Server des Modells gleichzeitig beantwortet — die Slots von llama.cpp, ein vLLM-Batch. Leer heißt kein Limit, richtig für eine gehostete API. Bei 1 kann das Modell nicht sein eigener Subagent sein, weil der Subagent den einzigen Slot belegen und das Gespräch verdrängen würde, das der Server im Cache hat, sodass die wartende Antwort alles erneut einlesen müsste. Andere Modelle können trotzdem seine Subagenten sein.'
MESSAGES['Think before answering'] = 'Vor der Antwort nachdenken'
MESSAGES['thinking off'] = 'Nachdenken aus'
MESSAGES["This model offers no choice of effort. An administrator sets what it takes on the model's page."] = 'Dieses Modell bietet keine Wahl des Denkaufwands. Was es annimmt, legt ein Administrator auf der Seite des Modells fest.'
MESSAGES['Effort is %(level)s. /effort %(levels)s.'] = 'Denkaufwand ist %(level)s. /effort %(levels)s.'
MESSAGES['“%(level)s” is not a level this model offers. Try %(levels)s.'] = '„%(level)s“ ist keine Stufe, die dieses Modell anbietet. Versuchen Sie %(levels)s.'
MESSAGES['Thinking switched off.'] = 'Nachdenken abgeschaltet.'
MESSAGES['Models for scheduled work'] = 'Modelle für geplante Aufgaben'
MESSAGES["A run takes its schedule's own model when it is free, else the first free one of these, in this order. A model is free when it is hosted, when it has a slot nobody is using, or — on a server that holds one model at a time — when nothing is being answered there. When none is free, the run waits and tries again. People can untick any of these for their own runs."] = 'Ein Lauf nimmt das eigene Modell seines Zeitplans, wenn es frei ist, sonst das erste freie dieser Modelle, in dieser Reihenfolge. Ein Modell ist frei, wenn es gehostet ist, wenn es einen Slot hat, den niemand nutzt, oder — auf einem Server, der nur ein Modell zur Zeit hält — wenn dort gerade nichts beantwortet wird. Ist keines frei, wartet der Lauf und versucht es erneut. Jede Person kann davon beliebige für ihre eigenen Läufe abwählen.'
MESSAGES['one model at a time'] = 'ein Modell zur Zeit'
MESSAGES['%(n)s at once'] = '%(n)s gleichzeitig'
MESSAGES['no stated limit'] = 'kein angegebenes Limit'
MESSAGES['Order'] = 'Reihenfolge'
MESSAGES['Tick the models scheduled work may use and number them in the order they are tried. With none ticked, a run uses its own model only.'] = 'Haken Sie die Modelle an, die geplante Aufgaben nutzen dürfen, und nummerieren Sie sie in der Reihenfolge, in der sie versucht werden. Ist keines angehakt, nutzt ein Lauf nur sein eigenes Modell.'
MESSAGES['There are no chat models yet.'] = 'Es gibt noch keine Chat-Modelle.'
MESSAGES['History'] = 'Verlauf'
MESSAGES["Keep each run's record for"] = 'Den Eintrag jedes Laufs aufbewahren für'
MESSAGES["Days. Older runs are forgotten, and the chats of fresh runs with them; a schedule's current chat is kept."] = 'Tage. Ältere Läufe werden vergessen und mit ihnen die Chats eigenständiger Läufe; der aktuelle Chat eines Zeitplans bleibt.'
MESSAGES['Or by its place in the month'] = 'Oder nach seiner Stelle im Monat'
MESSAGES['Not used'] = 'Nicht verwendet'
MESSAGES['First'] = 'Erster'
MESSAGES['Second'] = 'Zweiter'
MESSAGES['Third'] = 'Dritter'
MESSAGES['Fourth'] = 'Vierter'
MESSAGES['Fifth'] = 'Fünfter'
MESSAGES['Last'] = 'Letzter'
MESSAGES['Day'] = 'Tag'
MESSAGES['day of the month'] = 'Tag des Monats'
MESSAGES['The second Monday, the last Friday, the last day of the month. When set, it replaces the days and dates above.'] = 'Der zweite Montag, der letzte Freitag, der letzte Tag des Monats. Wenn gesetzt, ersetzt es die Tage und Daten oben.'
MESSAGES['Show the next runs'] = 'Die nächsten Läufe zeigen'
MESSAGES['How it runs'] = 'Wie er läuft'
MESSAGES['The first free one of your scheduling models'] = 'Das erste freie Ihrer Modelle für geplante Aufgaben'
MESSAGES['Tried first. When it is busy answering someone, the next free model runs it instead.'] = 'Wird zuerst versucht. Ist es gerade mit einer Antwort für jemanden beschäftigt, übernimmt das nächste freie Modell.'
MESSAGES['Each run'] = 'Jeder Lauf'
MESSAGES['A fresh chat is told what the previous run found.'] = 'Ein neuer Chat erfährt, was der vorige Lauf gefunden hat.'
MESSAGES["The model's own"] = 'Die des Modells'
MESSAGES['Used when the model that runs it offers it.'] = 'Verwendet, wenn das ausführende Modell es anbietet.'
MESSAGES['When a run was missed'] = 'Wenn ein Lauf verpasst wurde'
MESSAGES['A run up to half its period late still runs; past that, this decides.'] = 'Ein Lauf, der höchstens eine halbe Periode zu spät ist, läuft noch; darüber hinaus entscheidet dies.'
MESSAGES['Still run when late by up to'] = 'Noch ausführen bei Verspätung bis'
MESSAGES['automatic'] = 'automatisch'
MESSAGES['Minutes. Empty: half the time between runs, from 2 minutes to 2 hours.'] = 'Minuten. Leer: die halbe Zeit zwischen den Läufen, von 2 Minuten bis 2 Stunden.'
MESSAGES['Tools a run may use'] = 'Werkzeuge, die ein Lauf nutzen darf'
MESSAGES['Nobody is there to answer a question during a run, and a run never sets up schedules of its own.'] = 'Während eines Laufs ist niemand da, der eine Frage beantwortet, und ein Lauf richtet nie eigene Zeitpläne ein.'
MESSAGES['Last run'] = 'Letzter Lauf'
MESSAGES['Paused'] = 'Pausiert'
MESSAGES['This does not describe a time anything could run at yet.'] = 'Das beschreibt noch keinen Zeitpunkt, zu dem etwas laufen könnte.'
MESSAGES['%a %-d %b %Y, %H:%M'] = '%A, %-d. %B %Y, %H:%M'
MESSAGES['Times are %(timezone)s.'] = 'Zeiten in %(timezone)s.'
MESSAGES['It has not run yet.'] = 'Er ist noch nicht gelaufen.'
MESSAGES['missed ×%(n)s'] = 'verpasst ×%(n)s'
MESSAGES['%(name)s was busy'] = '%(name)s war beschäftigt'
MESSAGES['tries again at %(when)s'] = 'neuer Versuch um %(when)s'
MESSAGES['Open the report'] = 'Bericht öffnen'
MESSAGES['Older runs'] = 'Ältere Läufe'
MESSAGES['%H:%M'] = '%H:%M'
MESSAGES['A run takes its own model when it is free, else the first free one of these, in this order. Untick the ones your runs should not use.'] = 'Ein Lauf nimmt sein eigenes Modell, wenn es frei ist, sonst das erste freie dieser Modelle, in dieser Reihenfolge. Wählen Sie die ab, die Ihre Läufe nicht nutzen sollen.'
MESSAGES['Every run'] = 'Bei jedem Lauf'
MESSAGES['A fresh chat each time'] = 'Jedes Mal ein neuer Chat'
MESSAGES['One chat that keeps growing'] = 'Ein Chat, der weiter wächst'
MESSAGES['Run once when it can'] = 'Einmal ausführen, sobald es geht'
MESSAGES['Skip it'] = 'Auslassen'
MESSAGES['Earlier results'] = 'Frühere Ergebnisse'
MESSAGES['Reading web pages'] = 'Webseiten lesen'
MESSAGES['Pictures'] = 'Bilder'
MESSAGES['Monday'] = 'Montag'
MESSAGES['Tuesday'] = 'Dienstag'
MESSAGES['Wednesday'] = 'Mittwoch'
MESSAGES['Thursday'] = 'Donnerstag'
MESSAGES['Friday'] = 'Freitag'
MESSAGES['Saturday'] = 'Samstag'
MESSAGES['Sunday'] = 'Sonntag'
MESSAGES['Nothing new'] = 'Nichts Neues'
MESSAGES['Skipped'] = 'Ausgelassen'
MESSAGES['Missed'] = 'Verpasst'
MESSAGES['on time'] = 'pünktlich'
MESSAGES['late'] = 'verspätet'
MESSAGES['caught up'] = 'nachgeholt'
MESSAGES['run by hand'] = 'von Hand gestartet'
MESSAGES['Say what you want to happen.'] = 'Sagen Sie, was geschehen soll.'
MESSAGES['That run is not available.'] = 'Dieser Lauf ist nicht verfügbar.'
MESSAGES['A scheduled task failed 3 times in a row. Pause it?'] = 'Eine geplante Aufgabe ist 3-mal hintereinander fehlgeschlagen. Pausieren?'
MESSAGES['A scheduled task failed.'] = 'Eine geplante Aufgabe ist fehlgeschlagen.'
MESSAGES['A scheduled task has finished.'] = 'Eine geplante Aufgabe ist fertig.'
MESSAGES['That model is not one you can use.'] = 'Dieses Modell können Sie nicht verwenden.'
MESSAGES['Planning'] = 'Planung'

# --- 2.0.0-beta.7: Subagents (harness spec 3.3.0), the activity panel's compaction and tasks ---
MESSAGES.update(
    {
        'This model serves one request at a time: subagents need another model, on another server, named in the chat.': 'Dieses Modell bedient eine Anfrage zur Zeit: Subagenten brauchen ein anderes Modell auf einem anderen Server, im Chat genannt.',
        '%(tokens)s until auto-compact': '%(tokens)s bis zur automatischen Zusammenfassung',
        'Automatic compaction is off.': 'Die automatische Zusammenfassung ist aus.',
        'Tasks: %(n)s': 'Aufgaben: %(n)s',
        'Finished': 'Beendet',
        '%(n)s running': '%(n)s laufen',
    }
)

# --- 2.0.0 Unreleased: the Subagents setting as one Min/Max range; 1 is the agent alone ---
MESSAGES.update(
    {
        'Min': 'Min',
        'Max': 'Max',
        "Counted with this chat's own agent: 1 is the agent alone, no subagents; Max 4 is the agent and up to 3 subagents, which do the work while it leads them.": 'Der eigene Agent des Chats zählt mit: 1 ist der Agent allein, ohne Subagenten; Max 4 ist der Agent und bis zu 3 Subagenten, die die Arbeit machen, während er sie leitet.',
        "How many agents a new chat may have, counted with its own: 1 is the agent alone, no subagents; Max 4 is the agent and up to 3 subagents, working beside it while it leads them. With Min 2 or more it leads from the start. A subagent runs on the chat's own model, or on another only when you name it in the chat. Existing chats keep their own.": 'Wie viele Agenten ein neuer Chat haben darf, sein eigener mitgezählt: 1 ist der Agent allein, ohne Subagenten; Max 4 ist der Agent und bis zu 3 Subagenten, die neben ihm arbeiten, während er sie leitet. Ab Min 2 leitet er von Anfang an. Ein Subagent läuft auf dem eigenen Modell des Chats, auf einem anderen nur, wenn Sie es im Chat nennen. Bestehende Chats behalten ihre eigene Einstellung.',
        "Max agents on a run, counted with its own: 1 is the agent alone, no subagents; 4 is the agent and up to 3 subagents. Only where its model can serve more than one request at once: a run's subagents run on its model, since nobody names another.": 'Max Agenten an einem Lauf, der eigene mitgezählt: 1 ist der Agent allein, ohne Subagenten; 4 ist der Agent und bis zu 3 Subagenten. Nur wo sein Modell mehr als eine Anfrage zugleich bedienen kann: Die Subagenten eines Laufs laufen auf seinem Modell, da niemand ein anderes nennt.',
        'Max agents in a chat': 'Max Agenten in einem Chat',
        "Each subagent is a whole generation of its own. People choose Min and Max for a chat, counted with its own agent (1 is the agent alone, no subagents), up to this. Offered where some model can serve as a subagent: the chat's own, serving two requests at once, or another off its one-model server, named by the person in the chat. Nobody sets a subagent's model.": 'Jeder Subagent ist eine ganze eigene Generierung. Personen wählen Min und Max für einen Chat, sein eigener Agent mitgezählt (1 ist der Agent allein, ohne Subagenten), bis zu diesem Wert. Angeboten, wo ein Modell als Subagent dienen kann: das eigene des Chats, wenn es zwei Anfragen zugleich bedient, oder ein anderes außerhalb seines Ein-Modell-Servers, das die Person im Chat nennt. Niemand stellt das Modell eines Subagenten ein.',
        "Min and Max are 1 to %(cap)s, counted with the chat's own agent, and Min cannot be more than Max.": 'Min und Max liegen zwischen 1 und %(cap)s, der eigene Agent des Chats mitgezählt, und Min kann nicht größer als Max sein.',
    }
)

# Progress (harness spec 3.4.0, progress.md): the shared texts every client
# draws a reply's phase with, keyed by the spec's English with its {{fields}}.
MESSAGES.update(
    {
        'Queued': 'In der Warteschlange',
        'Queued: {{ahead}} ahead': 'In der Warteschlange: {{ahead}} davor',
        'Waiting for {{model}}: {{reason}}': 'Warte auf {{model}}: {{reason}}',
        'Loading {{model}}': 'Lade {{model}}',
        'Reading the prompt: {{done}} / {{total}} tokens ({{percent}}%)': 'Lese den Prompt: {{done}} / {{total}} Tokens ({{percent}} %)',
        'Reading the prompt': 'Lese den Prompt',
        'Writing: {{rate}} tok/s': 'Schreibe: {{rate}} Tok/s',
        'Writing': 'Schreibe',
        'Running {{tool}}: {{what}}': 'Führe {{tool}} aus: {{what}}',
        'Running {{tool}}': 'Führe {{tool}} aus',
        'Waiting for you: {{what}}': 'Wartet auf dich: {{what}}',
        'Waiting for subagents: {{running}} running': 'Wartet auf Subagenten: {{running}} laufen',
        'Compacting the conversation': 'Verdichte das Gespräch',
        'Retrying in {{in}} s: {{reason}}': 'Neuer Versuch in {{in}} s: {{reason}}',
        'Stopped: {{reason}}': 'Gestoppt: {{reason}}',
        'Failed: {{reason}}': 'Fehlgeschlagen: {{reason}}',
        'it serves one request at a time and is answering another': 'es bedient eine Anfrage auf einmal und beantwortet gerade eine andere',
        'all its slots are in use': 'alle seine Plätze sind belegt',
        'its server holds another model now': 'sein Server hält gerade ein anderes Modell',
        'allow {{tool}}?': '{{tool}} erlauben?',
        'an approval': 'eine Freigabe',
        'a question': 'eine Frage',
        'the plan': 'den Plan',
        'by you': 'von dir',
        'step limit': 'Schrittlimit',
        'budget': 'Budget',
    }
)

# Subagents in agent chats (harness spec 3.4.0).
MESSAGES.update(
    {
        'Subagent %(name)s (%(id)s), %(position)s, asks:': 'Subagent %(name)s (%(id)s), %(position)s, fragt:',
        'The computer this chat runs on does not run subagents: update its LLeMbas CLI.': 'Der Computer, auf dem dieser Chat läuft, führt keine Subagenten aus: aktualisiere dort das LLeMbas CLI.',
    }
)

# The subagent tree in the activity panel (harness spec 3.4.0).
MESSAGES.update(
    {
        'the main agent': 'der Hauptagent',
        'Context used': 'Genutzter Kontext',
        'Stop %(name)s and the subagents under it?': '%(name)s und die Subagenten darunter stoppen?',
        'messages: %(n)s': 'Nachrichten: %(n)s',
        'findings: %(n)s': 'Erkenntnisse: %(n)s',
        'Findings': 'Erkenntnisse',
        '%(size)s of %(max)s': '%(size)s von %(max)s',
        'Open slots: %(n)s': 'Freie Plätze: %(n)s',
        'They run on the computer this chat runs on.': 'Sie laufen auf dem Computer, auf dem dieser Chat läuft.',
        'Ended': 'Beendet',
        'No findings yet.': 'Noch keine Erkenntnisse.',
        'No subagent with that id in this chat.': 'In diesem Chat gibt es keinen Subagenten mit dieser ID.',
        'That is kept on the computer this chat runs on, which is not linked right now.': 'Das liegt auf dem Computer, auf dem dieser Chat läuft, und der ist gerade nicht verbunden.',
        'Choose a directory, and the terminal opens there.': 'Wähle einen Ordner, und das Terminal öffnet sich dort.',
        'waiting for a slot': 'wartet auf einen Platz',
        'finished': 'fertig',
        'idle': 'untätig',
        'paused: context full': 'pausiert: Kontext voll',
        'out of context': 'Kontext erschöpft',
    }
)

# Scheduled-run notifications: the full and the lock screen's version.
MESSAGES.update(
    {
        'Scheduled task': 'Geplante Aufgabe',
        'Failed 3 times in a row. Pause it?': '3-mal nacheinander fehlgeschlagen. Pausieren?',
        'Run again': 'Erneut ausführen',
    }
)

# Monitor mode: a schedule that watches a web address.
MESSAGES.update(
    {
        'Only when this page changes': 'Nur wenn sich diese Seite ändert',
        'Optional. Each run first looks at this address; when nothing changed since the last look, the run stops there and you are not told.': 'Optional. Jeder Lauf sieht zuerst diese Adresse an; hat sich seit dem letzten Mal nichts geändert, endet der Lauf dort und Sie werden nicht benachrichtigt.',
        'Unchanged': 'Unverändert',
        'Say which address to watch.': 'Geben Sie an, welche Adresse beobachtet werden soll.',
        'That address is too long.': 'Diese Adresse ist zu lang.',
        'Only http and https addresses can be watched.': 'Nur http- und https-Adressen können beobachtet werden.',
    }
)

# Pinned chats.
MESSAGES.update(
    {
        'Pinned chats': 'Angeheftete Chats',
        'Pin chat': 'Chat anheften',
        'Unpin chat': 'Chat loslösen',
    }
)

# /api/v2 parity: folders, language and theme.
MESSAGES.update(
    {
        'A folder needs a name.': 'Ein Ordner braucht einen Namen.',
        'That language is not offered.': 'Diese Sprache wird nicht angeboten.',
        'That theme is not offered.': 'Dieses Design wird nicht angeboten.',
    }
)

# Scheduled work on a linked computer (harness spec 3.5.0).
MESSAGES.update(
    {
        'Only a web address or a command can be watched.': 'Nur eine Webadresse oder ein Befehl kann beobachtet werden.',
        'Say which command to watch.': 'Geben Sie an, welcher Befehl beobachtet werden soll.',
        'That command is too long.': 'Dieser Befehl ist zu lang.',
        'A command is watched on a linked computer: choose the computer it runs on.': 'Ein Befehl wird auf einem verbundenen Computer beobachtet: Wählen Sie den Computer, auf dem es läuft.',
        'Say which computer to run it on.': 'Geben Sie an, auf welchem Computer es laufen soll.',
        'Only a linked computer can say "this" computer.': 'Nur ein verbundener Computer kann „diesen“ Computer sagen.',
        'That computer is not one of your linked computers.': 'Dieser Computer gehört nicht zu Ihren verbundenen Computern.',
        'Say which folder on the computer, as a full path.': 'Geben Sie den Ordner auf dem Computer als vollständigen Pfad an.',
        'A scheduled run cannot use plan mode: nobody is there to approve a plan.': 'Ein geplanter Lauf kann den Planmodus nicht nutzen: Niemand ist da, um einen Plan zu genehmigen.',
        'Choose Manual, Edit or Auto.': 'Wählen Sie Manual, Edit oder Auto.',
        "That computer's LLeMbas CLI is too old to run scheduled work. Update it.": 'Das LLeMbas CLI dieses Computers ist zu alt für geplante Arbeit. Aktualisieren Sie es.',
        'Where it runs': 'Wo es läuft',
        'Computer': 'Computer',
        'not linked now': 'gerade nicht verbunden',
        'does not accept scheduled work': 'nimmt keine geplante Arbeit an',
        'Folder on the computer': 'Ordner auf dem Computer',
        'A full path, inside a folder the computer allows remote work in.': 'Ein vollständiger Pfad in einem Ordner, in dem der Computer Fernarbeit erlaubt.',
        'Edit unless you choose otherwise; Auto runs commands without asking. The computer may lower it.': 'Edit, sofern Sie nichts anderes wählen; Auto führt Befehle ohne Nachfrage aus. Der Computer kann ihn herabsetzen.',
        'An approval waits (minutes)': 'Eine Genehmigung wartet (Minuten)',
        'A command the run needs approved comes to your phone as a card. Unanswered by then, it counts as denied.': 'Ein Befehl, den der Lauf genehmigt braucht, kommt als Karte auf Ihr Telefon. Bis dahin unbeantwortet, gilt er als abgelehnt.',
        "Only when this command's output changes": 'Nur wenn sich die Ausgabe dieses Befehls ändert',
        'Optional, on the computer. Run in the folder before each run; when its output is the same as last time, the run stops there. A command the mode would ask about is refused.': 'Optional, auf dem Computer. Läuft vor jedem Lauf im Ordner; ist seine Ausgabe dieselbe wie beim letzten Mal, endet der Lauf dort. Ein Befehl, bei dem der Modus nachfragen würde, wird abgelehnt.',
        'The command may take (seconds)': 'Der Befehl darf dauern (Sekunden)',
        'Stopped after this; the run then fails.': 'Danach wird er gestoppt, und der Lauf schlägt fehl.',
        'a computer no longer signed in': 'ein Computer, der nicht mehr angemeldet ist',
        'Earlier messages': 'Frühere Nachrichten',
        'Name one thing to attach: an attachment, a path or a URL.': 'Nennen Sie genau eine Sache zum Anhängen: einen Anhang, einen Pfad oder eine URL.',
        'That is not available to attach here.': 'Das kann hier nicht angehängt werden.',
        'That file is too large to attach.': 'Diese Datei ist zum Anhängen zu groß.',
        'End this shell': 'Diese Shell beenden',
        'Your open shells': 'Ihre offenen Shells',
        'Reconnect': 'Neu verbinden',
        'This shell was ended.': 'Diese Shell wurde beendet.',
        "%(device)s does not open terminals. Switch it on there: in LLeMbas Desktop's settings, or with the LLeMbas CLI's remote.terminal setting.": '%(device)s öffnet keine Terminals. Schalten Sie es dort ein: in den Einstellungen von LLeMbas Desktop oder mit der Einstellung remote.terminal der LLeMbas CLI.',
        'That shell is no longer open.': 'Diese Shell ist nicht mehr offen.',
        'You already have as many terminals open as you may (%(n)s). End one first.': 'Sie haben bereits so viele Terminals offen, wie Sie dürfen (%(n)s). Beenden Sie zuerst eines.',
        'Disconnected.': 'Getrennt.',
        'Reconnected. This is a new shell: the one before ended with the connection.': 'Wieder verbunden. Dies ist eine neue Shell: die vorige endete mit der Verbindung.',
        '%(why)s Press Reconnect to try again.': '%(why)s Drücken Sie Neu verbinden, um es noch einmal zu versuchen.',
        '%(why)s Trying again in %(n)s s…': '%(why)s Neuer Versuch in %(n)s s…',
        'just now': 'gerade eben',
        '%(n)s min': '%(n)s Min.',
        '%(n)s h': '%(n)s Std.',
        'End': 'Beenden',
        'End the shell on %(device)s in %(dir)s': 'Die Shell auf %(device)s in %(dir)s beenden',
        'End this shell?': 'Diese Shell beenden?',
        'Whatever is running in it stops, and what it showed is gone.': 'Was darin läuft, wird gestoppt, und was sie angezeigt hat, ist weg.',
        'End shell': 'Shell beenden',
        'The browser did not allow reading the clipboard. Use its own paste (the menu, or Shift+Insert).': 'Der Browser hat das Lesen der Zwischenablage nicht erlaubt. Verwenden Sie sein eigenes Einfügen (das Menü oder Umschalt+Einfg).',
        'running': 'läuft',
        'not run': 'nicht ausgeführt',
        'Still running.': 'Läuft noch.',
        'Where this chat runs': 'Wo dieser Chat läuft',
        'Folder': 'Ordner',
        'Allow once': 'Einmal erlauben',
        'Always allow': 'Immer erlauben',
        'Deny, and say why': 'Ablehnen und begründen',
        'Details': 'Details',
        'Back to the report': 'Zurück zum Bericht',
        'Reports': 'Berichte',
        'All schedules': 'Alle Zeitpläne',
        'Unread only': 'Nur ungelesene',
        'No reports match.': 'Kein Bericht passt.',
        'No reports yet. A scheduled task files one when it has something to tell you.': 'Noch keine Berichte. Eine geplante Aufgabe legt einen an, wenn sie Ihnen etwas mitzuteilen hat.',
        'Unread': 'Ungelesen',
        'Important': 'Wichtig',
        'Continue this report as a conversation. It stays under Reports.': 'Diesen Bericht als Unterhaltung fortsetzen. Sie bleibt unter Berichte.',
        'Reply': 'Antworten',
        'Delete this report? The conversation that continued it goes with it.': 'Diesen Bericht löschen? Die Unterhaltung, die ihn fortgesetzt hat, wird mitgelöscht.',
        'Open the conversation': 'Unterhaltung öffnen',
        'Also save each report as a file': 'Jeden Bericht auch als Datei speichern',
        'In your reports folder, as Markdown, beside the report under Reports.': 'In Ihrem Berichtsordner, als Markdown, zusätzlich zum Bericht unter Berichte.',
        'Notify when': 'Benachrichtigen, wenn',
        'A report every time, also when nothing was found.': 'Jedes Mal ein Bericht, auch wenn nichts gefunden wurde.',
        'When…': 'Wenn …',
        'A report only when this holds; otherwise the run leaves one line in the history.': 'Ein Bericht nur, wenn dies zutrifft; sonst hinterlässt der Lauf eine Zeile im Verlauf.',
        'When to write a report': 'Wann ein Bericht geschrieben wird',
        'No report and no notification: each run is only a line in the history.': 'Kein Bericht und keine Benachrichtigung: Jeder Lauf ist nur eine Zeile im Verlauf.',
        'Also saved as a file in your reports folder': 'Werden auch als Datei in Ihrem Berichtsordner gespeichert',
        'Open the run': 'Lauf öffnen',
        'Open the file': 'Datei öffnen',
        'That report no longer exists.': 'Diesen Bericht gibt es nicht mehr.',
        'New report': 'Neuer Bericht',
        'Stops it running and removes the chats its runs worked in. Its reports stay under Reports.': 'Beendet ihn und entfernt die Chats, in denen seine Läufe gearbeitet haben. Seine Berichte bleiben unter Berichte.',
        'Remove this schedule? It will stop running, and the chats of its runs are removed. Its reports stay.': 'Diesen Zeitplan entfernen? Er läuft nicht mehr, und die Chats seiner Läufe werden entfernt. Seine Berichte bleiben.',
        'HTML pages in answers (experimental)': 'HTML-Seiten in Antworten (experimentell)',
        'Let models add pages to their answers': 'Modelle dürfen ihren Antworten Seiten hinzufügen',
        'Can write HTML pages': 'Kann HTML-Seiten schreiben',
        'Pages in answers': 'Seiten in Antworten',
        'Experimental. The model may add a small page — a chart, a diagram — to an answer, shown here in a sealed frame without scripts or network access.': 'Experimentell. Das Modell kann einer Antwort eine kleine Seite hinzufügen — ein Diagramm, ein Schaubild —, die hier in einem abgeschotteten Rahmen ohne Skripte und Netzwerkzugriff gezeigt wird.',
        'Pages in answers are switched off here.': 'Seiten in Antworten sind hier ausgeschaltet.',
        'Writing the page…': 'Die Seite wird geschrieben …',
        'The page “%(title)s”, sealed off from this application': 'Die Seite „%(title)s“, von dieser Anwendung abgeschottet',
        'This answer has a page: %(title)s': 'Diese Antwort hat eine Seite: %(title)s',
        'Open larger': 'Größer öffnen',
        'View source': 'Quelltext anzeigen',
        'Pages in answers are not offered in this chat.': 'Seiten in Antworten werden in diesem Chat nicht angeboten.',
        'New report: %(title)s': 'Neuer Bericht: %(title)s',
        "A report's conversation stays with its report: it cannot be archived, pinned or moved.": 'Die Unterhaltung zu einem Bericht bleibt bei ihrem Bericht: Sie kann nicht archiviert, angeheftet oder verschoben werden.',
        "A model may then put a small page — a chart, a diagram, a table laid out with colour — into its answer, shown in a sealed frame at that place. This switch is all it takes: pages are then on in every chat, and a person can switch them off in a chat's settings. The page is shown without scripts and without any network access: HTML, CSS and drawings only. On a phone and in a terminal there is a one-line note in its place. Experimental: off by default, and it may change.": 'Ein Modell kann dann eine kleine Seite — ein Diagramm, ein Schaubild, eine farbig gestaltete Tabelle — in seine Antwort setzen, die an dieser Stelle in einem abgeschotteten Rahmen gezeigt wird. Dieser Schalter genügt: Seiten sind dann in jedem Chat eingeschaltet, und eine Person kann sie in den Einstellungen eines Chats ausschalten. Die Seite wird ohne Skripte und ohne Netzwerkzugriff gezeigt: nur HTML, CSS und Zeichnungen. Auf dem Telefon und im Terminal steht an ihrer Stelle ein einzeiliger Hinweis. Experimentell: standardmäßig aus, und es kann sich ändern.',
        'Tick this for a model that writes a working, self-contained page when asked — the larger models do, the small ones mostly do not. It matters where pages in answers are offered on chosen models only (<a href="/admin/general">General</a>): there an unticked model is simply not asked to. Where they are offered on all models, every model has them.': 'Ankreuzen für ein Modell, das auf Wunsch eine funktionierende, eigenständige Seite schreibt — die größeren Modelle können das, die kleinen meist nicht. Es zählt dort, wo Seiten in Antworten nur ausgewählten Modellen angeboten werden (<a href="/admin/general">Allgemein</a>): Dort wird ein Modell ohne Häkchen einfach nicht darum gebeten. Wo sie allen Modellen angeboten werden, hat sie jedes Modell.',
        'Which models may write pages': 'Welche Modelle Seiten schreiben dürfen',
        'Only the models marked “Can write HTML pages”': 'Nur die Modelle mit der Markierung „Kann HTML-Seiten schreiben“',
        'Pages are off: no model is offered them.': 'Seiten sind aus: Sie werden keinem Modell angeboten.',
        '%(able)s of %(all)s chat models are offered pages now.': 'Seiten werden jetzt %(able)s von %(all)s Chat-Modellen angeboten.',
        'A model is marked on its own page, under <a href="/admin/models">Models</a>.': 'Ein Modell wird auf seiner eigenen Seite markiert, unter <a href="/admin/models">Modelle</a>.',
        'Not available with this model: pages are offered only on models an administrator chose (Admin › General).': 'Mit diesem Modell nicht verfügbar: Seiten werden nur Modellen angeboten, die eine Administratorin oder ein Administrator ausgewählt hat (Verwaltung › Allgemein).',
        'Not available in an agent chat.': 'In einem Agenten-Chat nicht verfügbar.',
        'Not available in this chat.': 'In diesem Chat nicht verfügbar.',
        'Open in viewer': 'Im Betrachter öffnen',
        'Run page': 'Seite ausführen',
        'This page has scripts or other parts that only work when it runs, so it is not shown until you run it. It runs sealed off from LLeMbas.': 'Diese Seite enthält Skripte oder andere Teile, die nur beim Ausführen funktionieren; sie wird deshalb erst gezeigt, wenn Sie sie ausführen. Sie läuft von LLeMbas abgeschottet.',
    }
)

MESSAGES.update(
    {
        "The provider has no balance to read with a model key and does not say what a reply cost: a cost here is this instance's estimate from the model's prices.": 'Der Anbieter hat kein Guthaben, das sich mit einem Modellschlüssel lesen lässt, und sagt nicht, was eine Antwort gekostet hat: Kosten hier sind die Schätzung dieser Instanz aus den Preisen des Modells.',
        'Region': 'Region',
        'As typed above': 'Wie oben eingegeben',
        "Fills the base URL, which stays editable. A key works only with the address of its own product and region; for a workspace's own host, change the host and keep the path.": 'Füllt die Basis-URL aus, die bearbeitbar bleibt. Ein Schlüssel funktioniert nur mit der Adresse seines eigenen Produkts und seiner Region; für den eigenen Host eines Workspace ändern Sie den Host und behalten den Pfad.',
        "This key does not look like one for this type: a plan's key starts with sk-sp-, a pay-as-you-go key does not. A key works only with its own product's address.": 'Dieser Schlüssel sieht nicht wie einer für diesen Typ aus: Der Schlüssel eines Plans beginnt mit sk-sp-, ein nutzungsbasierter nicht. Ein Schlüssel funktioniert nur mit der Adresse seines eigenen Produkts.',
        "This model's connection is a subscription. Its replies are counted — tokens, cached tokens, time — and never priced: a figure per token would be made up. What the plan has left is in the provider's console.": 'Die Verbindung dieses Modells ist ein Abonnement. Seine Antworten werden gezählt — Tokens, Tokens aus dem Cache, Zeit — und nie bepreist: Ein Betrag pro Token wäre erfunden. Was der Plan noch übrig hat, steht in der Konsole des Anbieters.',
        "Replace {WorkspaceId} in the base URL with your workspace's id: the provider's console shows the whole host.": 'Ersetzen Sie {WorkspaceId} in der Basis-URL durch die ID Ihres Workspace: Die Konsole des Anbieters zeigt den ganzen Host.',
        "%(model)s is on a connection for interactive use only: its provider's terms do not allow scheduled or background work. Choose a model on a pay-as-you-go connection.": '%(model)s liegt auf einer Verbindung nur für interaktive Nutzung: Die Bedingungen ihres Anbieters erlauben keine geplante Arbeit und keine Hintergrundarbeit. Wählen Sie ein Modell auf einer nutzungsbasiert abgerechneten Verbindung.',
        "This chat's model is on a connection for interactive use only, and compacting is background work its provider's terms do not allow. It needs the same model on a pay-as-you-go connection, or a default model that is not interactive-only.": 'Das Modell dieses Chats liegt auf einer Verbindung nur für interaktive Nutzung, und das Zusammenfassen ist Hintergrundarbeit, die die Bedingungen ihres Anbieters nicht erlauben. Nötig ist dasselbe Modell auf einer nutzungsbasiert abgerechneten Verbindung oder ein Standardmodell, das nicht nur interaktiv ist.',
        'Singapore (international)': 'Singapur (international)',
        'China (Beijing)': 'China (Peking)',
        'US (Virginia)': 'USA (Virginia)',
        'China (Hong Kong)': 'China (Hongkong)',
        "Germany (Frankfurt), your workspace's host": 'Deutschland (Frankfurt), der Host Ihres Workspace',
        "Japan (Tokyo), your workspace's host": 'Japan (Tokio), der Host Ihres Workspace',
        'Token Plan, Singapore': 'Token Plan, Singapur',
        'Token Plan, China (Beijing)': 'Token Plan, China (Peking)',
        'Coding Plan (international)': 'Coding Plan (international)',
        'Alibaba Cloud Model Studio (pay-as-you-go)': 'Alibaba Cloud Model Studio (nutzungsbasiert)',
        'Alibaba Cloud Model Studio (Token Plan, Coding Plan)': 'Alibaba Cloud Model Studio (Token Plan, Coding Plan)',
        "The plan's quota is used up. The provider does not fall back to pay-as-you-go: wait for the plan's next month, add to the plan, or choose a model on another connection.": 'Das Kontingent des Plans ist aufgebraucht. Der Anbieter wechselt nicht auf nutzungsbasierte Abrechnung: Warten Sie auf den nächsten Monat des Plans, erweitern Sie den Plan oder wählen Sie ein Modell auf einer anderen Verbindung.',
        "That model is on a connection for interactive use only: its provider's terms do not allow scheduled work. Choose a model on a pay-as-you-go connection.": 'Dieses Modell liegt auf einer Verbindung nur für interaktive Nutzung: Die Bedingungen ihres Anbieters erlauben keine geplante Arbeit. Wählen Sie ein Modell auf einer nutzungsbasiert abgerechneten Verbindung.',
    }
)

MESSAGES.update(
    {
        'Last used here: %(model)s. A new chat in this directory starts on it unless you choose another.': 'Hier zuletzt verwendet: %(model)s. Ein neuer Chat in diesem Verzeichnis beginnt damit, sofern Sie kein anderes wählen.',
        "Runs on this server, like a chat: with the server's tools (search, your files here, memory) and on a scheduling model.": 'Läuft auf diesem Server wie ein Chat: mit den Werkzeugen des Servers (Suche, Ihre Dateien hier, Gedächtnis) und auf einem Planungsmodell.',
        'Agentic': 'Agentisch',
        "Runs on one of your computers, in one folder there, as an agent: with the agent's tools on that computer — its files and its shell — under the computer's own limits. It belongs to that computer and folder.": 'Läuft auf einem Ihrer Computer, in einem Ordner dort, als Agent: mit den Werkzeugen des Agenten auf diesem Computer — seinen Dateien und seiner Shell — innerhalb der eigenen Grenzen des Computers. Er gehört zu diesem Computer und Ordner.',
        'None of your computers accepts scheduled work now. Link one and switch on “Accept scheduled work” in its LLeMbas CLI (lembas service scheduled on).': 'Keiner Ihrer Computer nimmt jetzt geplante Arbeit an. Verbinden Sie einen und schalten Sie „Accept scheduled work“ in seinem LLeMbas CLI ein (lembas service scheduled on).',
        "The computer must allow it: “Accept scheduled work” in its LLeMbas CLI (lembas service scheduled on). When it is not linked at the time of a run, the run waits for it; if it has not come back before the run's time has passed, the run is recorded as missed and you are told.": 'Der Computer muss es erlauben: „Accept scheduled work“ in seinem LLeMbas CLI (lembas service scheduled on). Ist er zur Zeit eines Laufs nicht verbunden, wartet der Lauf auf ihn; ist er nicht zurück, bevor die Zeit des Laufs vorbei ist, wird der Lauf als verpasst vermerkt und Sie erfahren es.',
        'Browse': 'Durchsuchen',
        "The folder's usual model": 'Das übliche Modell des Ordners',
        "The folder's usual model is the one you last used in that folder on that computer — the computer remembers it, and each run asks it — or your default model where there is none. A model chosen here is the run's, whatever the folder's is. A run never changes the folder's model.": 'Das übliche Modell des Ordners ist das, das Sie in diesem Ordner auf diesem Computer zuletzt verwendet haben — der Computer merkt es sich, und jeder Lauf fragt ihn danach — oder Ihr Standardmodell, wo es keines gibt. Ein hier gewähltes Modell ist das des Laufs, was auch immer das des Ordners ist. Ein Lauf ändert das Modell des Ordners nie.',
        "the folder's usual model": 'das übliche Modell des Ordners',
    }
)

MESSAGES.update(
    {
        "Interactive use only: the provider's terms forbid automated services, batch scripts and scheduled background jobs on a plan, and a key used for them can be suspended. So this server sends no scheduled or background work of its own to this connection — it goes to a pay-as-you-go one, or is not done — and refuses its models to personal API keys. A linked LLeMbas CLI may use them and is trusted to keep its own scheduled and background calls off them. A plan is sold for its subscriber's own use in coding and agent tools; whether this instance's use fits is yours to judge.": 'Nur für interaktive Nutzung: Die Bedingungen des Anbieters verbieten bei einem Plan automatisierte Dienste, Batch-Skripte und geplante Hintergrundaufgaben, und ein dafür benutzter Schlüssel kann gesperrt werden. Dieser Server schickt deshalb keine eigene geplante Arbeit und keine Hintergrundarbeit an diese Verbindung — sie geht an eine nutzungsbasiert abgerechnete, oder sie unterbleibt — und verweigert ihre Modelle persönlichen API-Schlüsseln. Ein verbundenes LLeMbas CLI darf sie nutzen; ihm wird vertraut, dass es seine eigenen geplanten und Hintergrundaufrufe von ihnen fernhält. Ein Plan wird für die eigene Nutzung des Abonnenten in Programmier- und Agentenwerkzeugen verkauft; ob die Nutzung in dieser Instanz dazu passt, müssen Sie beurteilen.',
        'The earlier turns of this chat were not summarised: its model is on a connection for interactive use only, and there is no other model to ask for the summary. Switch model or start a new chat before the conversation outgrows this one.': 'Die früheren Teile dieses Chats wurden nicht zusammengefasst: Sein Modell liegt auf einer Verbindung nur für interaktive Nutzung, und es gibt kein anderes Modell, das um die Zusammenfassung gebeten werden könnte. Wechseln Sie das Modell oder beginnen Sie einen neuen Chat, bevor das Gespräch dieses Modell übersteigt.',
    }
)

# Media from connections: speech and pictures a connection carries itself.
MESSAGES.update(
    {
        "Use this connection's own speech models for its chats": 'Für Chats auf dieser Verbindung ihre eigenen Sprachmodelle verwenden',
        "Use this connection's own image model for its chats": 'Für Chats auf dieser Verbindung ihr eigenes Bildmodell verwenden',
        'On': 'An',
        'Speech to text': 'Sprache zu Text',
        'Text to speech': 'Text zu Sprache',
        'model id': 'Modell-ID',
        'voice': 'Stimme',
        'per minute': 'pro Minute',
        'per 1M characters': 'pro 1 Mio. Zeichen',
        'per picture': 'pro Bild',
        "On a plan this is for a person's own turns only — dictation, a reply they asked to hear, a picture inside their own reply — never for scheduled work or a personal API key, and it is counted, not priced.": 'Bei einem Plan ist dies nur für die eigenen Züge einer Person — Diktat, eine Antwort, die sie sich vorlesen lässt, ein Bild in ihrer eigenen Antwort — nie für geplante Arbeit oder einen persönlichen API-Schlüssel, und es wird gezählt, nicht bepreist.',
        'QwenCloud, Singapore': 'QwenCloud, Singapur',
        'QwenCloud, US (Virginia)': 'QwenCloud, USA (Virginia)',
        'QwenCloud, Germany (Frankfurt)': 'QwenCloud, Deutschland (Frankfurt)',
        'QwenCloud, Japan (Tokyo)': 'QwenCloud, Japan (Tokio)',
        'QwenCloud, China (Hong Kong)': 'QwenCloud, China (Hongkong)',
        'QwenCloud, China (Beijing)': 'QwenCloud, China (Peking)',
        'Token Plan, QwenCloud': 'Token Plan, QwenCloud',
        "This connection's base URL is not an address.": 'Die Basis-URL dieser Verbindung ist keine Adresse.',
        '%(name)s answered %(status)s: %(said)s': '%(name)s antwortete %(status)s: %(said)s',
        "The provider's answer named no result.": 'Die Antwort des Anbieters nannte kein Ergebnis.',
        "The provider's result could not be fetched: %(why)s": 'Das Ergebnis des Anbieters konnte nicht abgerufen werden: %(why)s',
        "The provider's result is larger than this server keeps.": 'Das Ergebnis des Anbieters ist größer, als dieser Server aufbewahrt.',
        'Nothing is configured to do that on this instance.': 'Auf dieser Instanz ist dafür nichts eingerichtet.',
        'The image endpoint returned a picture that could not be read.': 'Der Bild-Endpunkt lieferte ein Bild, das nicht gelesen werden konnte.',
        'The image endpoint returned no picture.': 'Der Bild-Endpunkt lieferte kein Bild.',
        'That recording is longer than this provider transcribes at once.': 'Diese Aufnahme ist länger, als dieser Anbieter auf einmal transkribiert.',
        'The speech endpoint returned audio that could not be read.': 'Der Sprach-Endpunkt lieferte Audio, das nicht gelesen werden konnte.',
        'The speech endpoint returned no audio.': 'Der Sprach-Endpunkt lieferte kein Audio.',
        "Transcribed by %(source)s: this chat's own speech model did not answer.": 'Transkribiert von %(source)s: Das eigene Sprachmodell dieses Chats hat nicht geantwortet.',
        "Read by %(source)s: this chat's own speech model did not answer.": 'Vorgelesen von %(source)s: Das eigene Sprachmodell dieses Chats hat nicht geantwortet.',
    }
)
MESSAGES["The endpoint's answer is larger than this server reads."] = 'Die Antwort des Endpunkts ist größer, als dieser Server liest.'
MESSAGES['The image endpoint returned something that is not a picture.'] = 'Der Bild-Endpunkt lieferte etwas, das kein Bild ist.'
MESSAGES['The picture is larger than this instance stores.'] = 'Das Bild ist größer, als diese Instanz speichert.'

# Files: copy, the folder picker, selecting several, the trash for good, limits.
MESSAGES['The limits of the file tree itself, apart from what a chat may be handed above.'] = 'Die Grenzen des Dateibaums selbst, unabhängig davon, was oben einem Chat übergeben werden darf.'
MESSAGES['Largest file uploaded to Files'] = 'Größte Datei, die in Dateien hochgeladen wird'
MESSAGES['Megabytes. A larger file is refused, with a message that says so.'] = 'Megabyte. Eine größere Datei wird abgelehnt, mit einer Meldung, die das sagt.'
MESSAGES['Largest download as a zip'] = 'Größter Download als Zip'
MESSAGES['Megabytes of files, before they are packed. A larger selection is refused before anything is sent.'] = 'Megabyte an Dateien, vor dem Packen. Eine größere Auswahl wird abgelehnt, bevor etwas gesendet wird.'
MESSAGES['Selected'] = 'Ausgewählt'
MESSAGES['Copy to…'] = 'Kopieren nach…'
MESSAGES['Download as a zip'] = 'Als Zip herunterladen'
MESSAGES['Clear the selection'] = 'Auswahl aufheben'
MESSAGES['Select everything here'] = 'Alles hier auswählen'
MESSAGES['Remove everything in the trash for good? This cannot be undone.'] = 'Alles im Papierkorb endgültig entfernen? Das lässt sich nicht rückgängig machen.'
MESSAGES['Empty the trash'] = 'Papierkorb leeren'
MESSAGES['Remove “%(name)s” for good? This cannot be undone.'] = '„%(name)s“ endgültig entfernen? Das lässt sich nicht rückgängig machen.'
MESSAGES['Remove for good'] = 'Endgültig entfernen'
MESSAGES['Remove %(name)s for good'] = '%(name)s endgültig entfernen'
MESSAGES['%(name)s is larger than %(n)s MB, the most that can be uploaded here.'] = '%(name)s ist größer als %(n)s MB, das Höchste, was hier hochgeladen werden kann.'
MESSAGES['Copied as %(name)s.'] = 'Kopiert als %(name)s.'
MESSAGES['Choose at least one file or folder.'] = 'Wählen Sie mindestens eine Datei oder einen Ordner.'
MESSAGES['Done: %(done)s. Not done: %(failed)s. %(reason)s'] = 'Erledigt: %(done)s. Nicht erledigt: %(failed)s. %(reason)s'
MESSAGES['Moved: %(n)s.'] = 'Verschoben: %(n)s.'
MESSAGES['Copied: %(n)s.'] = 'Kopiert: %(n)s.'
MESSAGES['Moved to the trash: %(n)s.'] = 'In den Papierkorb verschoben: %(n)s.'
MESSAGES['That is more than %(n)s MB, the most a zip may hold. Choose less.'] = 'Das sind mehr als %(n)s MB, das Höchste, was ein Zip enthalten darf. Wählen Sie weniger.'
MESSAGES['Removed for good.'] = 'Endgültig entfernt.'
MESSAGES['There is no text to save.'] = 'Es gibt keinen Text zum Speichern.'
MESSAGES['At most %(n)s files or folders at once.'] = 'Höchstens %(n)s Dateien oder Ordner auf einmal.'
MESSAGES['That is not something to do.'] = 'Das lässt sich nicht tun.'
MESSAGES['Choose a folder.'] = 'Wählen Sie einen Ordner.'
MESSAGES['No folder chosen yet.'] = 'Noch kein Ordner gewählt.'
MESSAGES['Move to which folder?'] = 'In welchen Ordner verschieben?'
MESSAGES['Move here'] = 'Hierher verschieben'
MESSAGES['Copy to which folder?'] = 'In welchen Ordner kopieren?'
MESSAGES['Copy here'] = 'Hierher kopieren'
MESSAGES['%(n)s selected'] = '%(n)s ausgewählt'
MESSAGES['Move the %(n)s selected to the trash? They can be restored from there.'] = 'Die %(n)s ausgewählten in den Papierkorb verschieben? Von dort lassen sie sich wiederherstellen.'
MESSAGES['shared sign-in'] = 'gemeinsame Anmeldung'
MESSAGES['each person signs in'] = 'jede Person meldet sich selbst an'
MESSAGES['%(count)s signed in'] = '%(count)s angemeldet'
MESSAGES['How it is signed in to'] = 'Wie man sich dort anmeldet'
MESSAGES['Nothing — the server is open'] = 'Gar nicht — der Server ist offen'
MESSAGES['A secret this instance sends'] = 'Ein Geheimnis, das diese Instanz sendet'
MESSAGES["OAuth — people sign in at the server's provider"] = 'OAuth — man meldet sich beim Anbieter des Servers an'
MESSAGES['Where to sign in is read from the server itself. Most servers also register this instance by themselves, and the three fields below stay empty.'] = 'Wo man sich anmeldet, wird vom Server selbst gelesen. Die meisten Server registrieren diese Instanz auch von selbst, und die drei Felder unten bleiben leer.'
MESSAGES['Client ID'] = 'Client-ID'
MESSAGES['Only for a provider where applications are registered by hand. The redirect address to register there is <code>%(address)s</code>.'] = 'Nur für einen Anbieter, bei dem Anwendungen von Hand registriert werden. Die dort zu registrierende Weiterleitungsadresse ist <code>%(address)s</code>.'
MESSAGES['Client secret'] = 'Client-Geheimnis'
MESSAGES['Separated by spaces. Empty asks for what the server itself names.'] = 'Durch Leerzeichen getrennt. Leer fragt nach dem, was der Server selbst nennt.'
MESSAGES['One shared sign-in for everyone allowed to use this server'] = 'Eine gemeinsame Anmeldung für alle, die diesen Server nutzen dürfen'
MESSAGES["Off, each person signs in with an account of their own, and a person who has not is not offered this server's tools. On, an administrator signs in once and everyone acts as that account: whatever it may read or change there, every one of them may, through a model."] = 'Aus: Jede Person meldet sich mit einem eigenen Konto an, und wer das nicht getan hat, bekommt die Werkzeuge dieses Servers nicht angeboten. An: Ein Administrator meldet sich einmal an, und alle handeln als dieses Konto: Was es dort lesen oder ändern darf, darf jede dieser Personen über ein Modell.'
MESSAGES['Signed in now: %(count)s. Changing the address or the client signs everyone out.'] = 'Jetzt angemeldet: %(count)s. Eine Änderung der Adresse oder des Clients meldet alle ab.'
MESSAGES['Sign in to an MCP server'] = 'Bei einem MCP-Server anmelden'
MESSAGES["Signed in. The server's tools are offered in your chats from now on. You can close this tab."] = 'Angemeldet. Die Werkzeuge des Servers werden ab jetzt in Ihren Chats angeboten. Sie können diesen Tab schließen.'
MESSAGES['Server'] = 'Server'
MESSAGES['Address'] = 'Adresse'
MESSAGES['This server has one shared sign-in. Everyone allowed to use it acts as the account you sign in with.'] = 'Dieser Server hat eine gemeinsame Anmeldung. Alle, die ihn nutzen dürfen, handeln als das Konto, mit dem Sie sich anmelden.'
MESSAGES['You reached this instance over plain HTTP. Most providers only send a sign-in back to an https address, and may refuse this one.'] = 'Sie haben diese Instanz über unverschlüsseltes HTTP erreicht. Die meisten Anbieter schicken eine Anmeldung nur an eine https-Adresse zurück und lehnen diese möglicherweise ab.'
MESSAGES["The provider's page is ready."] = 'Die Seite des Anbieters ist bereit.'
MESSAGES['Continue to the provider'] = 'Weiter zum Anbieter'
MESSAGES["You are sent to the server's own sign-in page and come back here. This instance keeps the sign-in encrypted and never shows it."] = 'Sie werden zur eigenen Anmeldeseite des Servers geschickt und kommen hierher zurück. Diese Instanz bewahrt die Anmeldung verschlüsselt auf und zeigt sie nie an.'
MESSAGES['This server has one shared sign-in, which an administrator makes.'] = 'Dieser Server hat eine gemeinsame Anmeldung, die ein Administrator vornimmt.'
MESSAGES['Back to settings'] = 'Zurück zu den Einstellungen'
MESSAGES['signed in'] = 'angemeldet'
MESSAGES['as %(account)s'] = 'als %(account)s'
MESSAGES['by %(who)s'] = 'durch %(who)s'
MESSAGES['renewed by itself'] = 'erneuert sich selbst'
MESSAGES['until %(when)s'] = 'bis %(when)s'
MESSAGES['needs sign-in'] = 'Anmeldung nötig'
MESSAGES['not signed in'] = 'nicht angemeldet'
MESSAGES['%Y-%m-%d %H:%M'] = '%Y-%m-%d %H:%M'
MESSAGES['Sign-ins to MCP servers'] = 'Anmeldungen bei MCP-Servern'
MESSAGES["These servers are used with an account of your own there. A server's tools are offered in your chats while you are signed in to it."] = 'Diese Server werden mit Ihrem eigenen Konto dort genutzt. Die Werkzeuge eines Servers werden in Ihren Chats angeboten, solange Sie dort angemeldet sind.'
MESSAGES['Added %(name)s. Sign in to it to discover its tools.'] = '%(name)s hinzugefügt. Melden Sie sich dort an, um seine Werkzeuge zu ermitteln.'
MESSAGES['Signed out of %(name)s, and its provider withdrew the sign-in.'] = 'Von %(name)s abgemeldet, und der Anbieter hat die Anmeldung zurückgezogen.'
MESSAGES['Signed out of %(name)s.'] = 'Von %(name)s abgemeldet.'
MESSAGES['This cell is longer than is shown here.'] = 'Diese Zelle ist länger, als hier gezeigt wird.'
MESSAGES['A picture the notebook made'] = 'Ein Bild, das das Notebook erzeugt hat'
MESSAGES['An output of the type %(type)s is not shown here.'] = 'Eine Ausgabe vom Typ %(type)s wird hier nicht gezeigt.'
MESSAGES['This notebook has no cells.'] = 'Dieses Notebook hat keine Zellen.'
MESSAGES['%(n)s more cells are not shown.'] = '%(n)s weitere Zellen werden nicht gezeigt.'
MESSAGES['Show as text'] = 'Als Text zeigen'
MESSAGES['%(name)s, drawn sealed off from this application'] = '%(name)s, abgeschottet von dieser Anwendung gezeichnet'
MESSAGES['What is inside this kind of archive cannot be listed here.'] = 'Der Inhalt dieser Art von Archiv kann hier nicht aufgelistet werden.'
MESSAGES['This archive could not be read.'] = 'Dieses Archiv konnte nicht gelesen werden.'
MESSAGES['Only the first %(n)s entries are listed.'] = 'Nur die ersten %(n)s Einträge sind aufgelistet.'
MESSAGES['%(n)s entries. Nothing is unpacked here.'] = '%(n)s Einträge. Hier wird nichts entpackt.'
MESSAGES['There is no viewer for this kind of file.'] = 'Für diese Art von Datei gibt es keinen Betrachter.'
MESSAGES['Only the first rows and columns are laid out. The text has all of it.'] = 'Nur die ersten Zeilen und Spalten sind als Tabelle gesetzt. Der Text enthält alles.'
MESSAGES['Show as a table'] = 'Als Tabelle zeigen'
MESSAGES['This file could not be shown here. It can still be downloaded.'] = 'Diese Datei konnte hier nicht gezeigt werden. Sie lässt sich weiterhin herunterladen.'
MESSAGES['More pages'] = 'Weitere Seiten'
MESSAGES['This model is stored in a way this viewer cannot read. It can still be downloaded.'] = 'Dieses Modell ist auf eine Weise gespeichert, die dieser Betrachter nicht lesen kann. Es lässt sich weiterhin herunterladen.'
MESSAGES['This model keeps its data in other files, which cannot be read here. It can still be downloaded.'] = 'Dieses Modell hält seine Daten in anderen Dateien, die hier nicht gelesen werden können. Es lässt sich weiterhin herunterladen.'
MESSAGES['This browser cannot draw 3D here.'] = 'Dieser Browser kann hier kein 3D zeichnen.'
MESSAGES['Drag to turn it, scroll to zoom.'] = 'Ziehen dreht es, Scrollen zoomt.'
MESSAGES['Only part of this model is drawn.'] = 'Nur ein Teil dieses Modells ist gezeichnet.'

# --- The unified Connections, Models and Media pages (2.x): 171 strings ---
MESSAGES['Test'] = 'Testen'
MESSAGES['Media'] = 'Medien'
MESSAGES['%(name)s is on. Switch it off — applies at once.'] = '%(name)s ist an. Ausschalten — gilt sofort.'
MESSAGES['%(name)s is off. Switch it on — applies at once.'] = '%(name)s ist aus. Einschalten — gilt sofort.'
MESSAGES['Applies at once'] = 'Gilt sofort'
MESSAGES['Off'] = 'Aus'
MESSAGES['Sections'] = 'Bereiche'
MESSAGES['Where it is'] = 'Wo sie ist'
MESSAGES['Enabled — its models are offered'] = 'Aktiviert — ihre Modelle werden angeboten'
MESSAGES['Options'] = 'Optionen'
MESSAGES['Longest generation, in seconds'] = 'Längste Generierung, in Sekunden'
MESSAGES['A generation is tens of seconds, and a queue in front of it can be minutes. After this long the reply stops waiting.'] = 'Eine Generierung dauert Dutzende Sekunden, und eine Warteschlange davor kann Minuten dauern. Nach dieser Zeit wartet die Antwort nicht länger.'
MESSAGES["Preserve VRAM: unload the chat's model while this ComfyUI draws"] = 'VRAM schonen: das Modell des Chats entladen, während dieses ComfyUI zeichnet'
MESSAGES["For a machine that cannot hold a chat model and a checkpoint at once. The chat's connection is asked to unload (its Unload URL), and the ComfyUI is freed afterwards. It makes every picture slower; leave it off where there is memory for both."] = 'Für einen Rechner, der ein Chat-Modell und einen Checkpoint nicht zugleich halten kann. Die Verbindung des Chats wird aufgefordert zu entladen (ihre Entlade-URL), und das ComfyUI wird danach freigegeben. Das macht jedes Bild langsamer; lassen Sie es aus, wo der Speicher für beides reicht.'
MESSAGES['What it was found to have'] = 'Was dort gefunden wurde'
MESSAGES['%(samplers)s samplers and %(schedulers)s schedulers. Its checkpoints are under Models. Test asks again.'] = '%(samplers)s Sampler und %(schedulers)s Scheduler. Seine Checkpoints stehen unter Modelle. Testen fragt erneut nach.'
MESSAGES["Everything this connection offers: the chat models it lists, and the speech and picture models added to it. A line opens the model's own page."] = 'Alles, was diese Verbindung bietet: die Chat-Modelle, die sie auflistet, und die Sprach- und Bildmodelle, die ihr hinzugefügt wurden. Eine Zeile öffnet die eigene Seite des Modells.'
MESSAGES["A ComfyUI's models are its checkpoints. Refresh asks it which it has; a new one arrives switched off, so a list you narrowed stays narrow."] = 'Die Modelle eines ComfyUI sind seine Checkpoints. Aktualisieren fragt es, welche es hat; ein neuer kommt ausgeschaltet an, sodass eine Liste, die Sie eingegrenzt haben, eingegrenzt bleibt.'
MESSAGES["A speech server's models are the ids it is asked with: one that transcribes, one that reads aloud. They are typed, not found."] = 'Die Modelle eines Sprachservers sind die IDs, mit denen er angefragt wird: eine, die transkribiert, eine, die vorliest. Sie werden eingetippt, nicht gefunden.'
MESSAGES['Refresh from the provider'] = 'Vom Anbieter aktualisieren'
MESSAGES['No models here yet.'] = 'Hier gibt es noch keine Modelle.'
MESSAGES['Add a model'] = 'Ein Modell hinzufügen'
MESSAGES['Capability'] = 'Fähigkeit'
MESSAGES['Model id'] = 'Modell-ID'
MESSAGES['As the provider spells it: the name sent with every request.'] = 'So, wie der Anbieter sie schreibt: der Name, der mit jeder Anfrage gesendet wird.'
MESSAGES['This provider documents: %(models)s.'] = 'Dieser Anbieter dokumentiert: %(models)s.'
MESSAGES['Add model'] = 'Modell hinzufügen'
MESSAGES['Speech and pictures for its own chats'] = 'Sprache und Bilder für ihre eigenen Chats'
MESSAGES["A chat whose model is on this connection uses these when the switch is on and a model of that kind is ticked — the first ticked one. Otherwise, and once when this connection's own does not answer, the instance's default under Media is used; the result says which answered. A chat on another connection is never sent here unless you make one of these the default there."] = 'Ein Chat, dessen Modell an dieser Verbindung hängt, verwendet diese, wenn der Schalter an ist und ein Modell dieser Art angehakt ist — das erste angehakte. Andernfalls, und einmal dann, wenn das eigene dieser Verbindung nicht antwortet, wird der Standard der Instanz unter Medien verwendet; das Ergebnis sagt, welches geantwortet hat. Ein Chat an einer anderen Verbindung wird nie hierher geschickt, es sei denn, Sie machen eines davon dort zum Standard.'
MESSAGES['Its media models'] = 'Ihre Medienmodelle'
MESSAGES["Ticked is on. A model's voice, options and price are on its own page; new ones are added under Models."] = 'Angehakt heißt an. Stimme, Optionen und Preis eines Modells stehen auf seiner eigenen Seite; neue werden unter Modelle hinzugefügt.'
MESSAGES['None yet. Add a speech or picture model under Models.'] = 'Noch keine. Fügen Sie unter Modelle ein Sprach- oder Bildmodell hinzu.'
MESSAGES['A workflow is a ComfyUI graph with holes where the prompt, the checkpoint and the numbers go. A model picks one by the name and description you give it.'] = 'Ein Workflow ist ein ComfyUI-Graph mit Lücken, wo der Prompt, der Checkpoint und die Zahlen hingehören. Ein Modell wählt einen anhand des Namens und der Beschreibung, die Sie ihm geben.'
MESSAGES['every ComfyUI'] = 'jedes ComfyUI'
MESSAGES['The first in the list'] = 'Der erste in der Liste'
MESSAGES['Used when neither the model nor the chat names one.'] = 'Wird verwendet, wenn weder das Modell noch der Chat einen nennt.'
MESSAGES['No workflow yet. Without one this ComfyUI has nothing to draw with.'] = 'Noch kein Workflow. Ohne einen hat dieses ComfyUI nichts, womit es zeichnen könnte.'
MESSAGES['Remove this connection'] = 'Diese Verbindung entfernen'
MESSAGES['Its models go with it: %(chat)s listed and %(media)s added. Existing chats keep their history, and a chat on one of its models has to be moved to another model to go on.'] = 'Ihre Modelle gehen mit ihr: %(chat)s aufgelistet und %(media)s hinzugefügt. Bestehende Chats behalten ihren Verlauf, und ein Chat mit einem ihrer Modelle muss auf ein anderes Modell umgestellt werden, um weiterzugehen.'
MESSAGES['Its workflows stay, for every ComfyUI.'] = 'Ihre Workflows bleiben, für jedes ComfyUI.'
MESSAGES['One of its models is a default under Media. Removing it leaves that default pointing at nothing until you choose another there: nothing is quietly served in its place.'] = 'Eines ihrer Modelle ist ein Standard unter Medien. Wird sie entfernt, zeigt dieser Standard auf nichts, bis Sie dort einen anderen wählen: Nichts wird stillschweigend an seiner Stelle verwendet.'
MESSAGES['Add connection'] = 'Verbindung hinzufügen'
MESSAGES['What kind of connection'] = 'Welche Art von Verbindung'
MESSAGES['What is at the address? The kind decides which questions the form asks, and what the connection can offer.'] = 'Was befindet sich unter der Adresse? Die Art entscheidet, welche Fragen das Formular stellt und was die Verbindung bieten kann.'
MESSAGES['http://workstation:8188'] = 'http://workstation:8188'
MESSAGES['http://workstation:8000/v1'] = 'http://workstation:8000/v1'
MESSAGES['Where the ComfyUI answers, without a path. Its checkpoints, samplers and schedulers are asked for as soon as it is added.'] = 'Wo das ComfyUI antwortet, ohne Pfad. Seine Checkpoints, Sampler und Scheduler werden abgefragt, sobald es hinzugefügt ist.'
MESSAGES['The address whose <code>/audio/transcriptions</code> and <code>/audio/speech</code> are asked, usually ending in <code>/v1</code>. One server may do both, or only one.'] = 'Die Adresse, deren <code>/audio/transcriptions</code> und <code>/audio/speech</code> angefragt werden, meist auf <code>/v1</code> endend. Ein Server kann beides oder nur eines davon.'
MESSAGES['Speech'] = 'Sprache'
MESSAGES["A connection is anything this instance reaches at an address: a provider of chat models, a ComfyUI that draws, a server that transcribes and reads aloud. What each one offers are models, listed under Models; a line here opens the connection's own page."] = 'Eine Verbindung ist alles, was diese Instanz unter einer Adresse erreicht: ein Anbieter von Chat-Modellen, ein ComfyUI, das zeichnet, ein Server, der transkribiert und vorliest. Was jede bietet, sind Modelle, aufgelistet unter Modelle; eine Zeile hier öffnet die eigene Seite der Verbindung.'
MESSAGES['No roads lead anywhere yet. Add a connection and its models appear under Models.'] = 'Noch führen keine Wege irgendwohin. Fügen Sie eine Verbindung hinzu, dann erscheinen ihre Modelle unter Modelle.'
MESSAGES['no models'] = 'keine Modelle'
MESSAGES['Speech and pictures, for the whole instance: whether each is on, which model is the default, and what is not any one model\'s. Where it comes from is a <a href="/admin/connections">connection</a>, and how a model is asked is on the <a href="/admin/models">model\'s</a> own page.'] = 'Sprache und Bilder für die ganze Instanz: ob jedes an ist, welches Modell der Standard ist und was zu keinem einzelnen Modell gehört. Woher es kommt, ist eine <a href="/admin/connections">Verbindung</a>, und wie ein Modell angefragt wird, steht auf der eigenen Seite des <a href="/admin/models">Modells</a>.'
MESSAGES['Dictation is on'] = 'Diktieren ist an'
MESSAGES['Read-aloud is on'] = 'Vorlesen ist an'
MESSAGES['Image generation is on'] = 'Bildgenerierung ist an'
MESSAGES["Off is off for every source, a connection's own included."] = 'Aus heißt aus für jede Quelle, auch für die eigene einer Verbindung.'
MESSAGES['An address from an older release (it becomes a connection at the next start)'] = 'Eine Adresse aus einer älteren Version (sie wird beim nächsten Start zu einer Verbindung)'
MESSAGES['Not chosen yet'] = 'Noch nicht gewählt'
MESSAGES['(no longer available)'] = '(nicht mehr verfügbar)'
MESSAGES['Chosen from the models that are added and switched on. It serves every chat whose own connection has none of its own.'] = 'Gewählt aus den Modellen, die hinzugefügt und eingeschaltet sind. Es bedient jeden Chat, dessen eigene Verbindung kein eigenes hat.'
MESSAGES["Open the default model's page"] = 'Seite des Standardmodells öffnen'
MESSAGES['The model chosen as the default can no longer serve: it was removed, switched off, or its connection was. Until you choose again nothing is served by default.'] = 'Das als Standard gewählte Modell kann nicht mehr dienen: Es wurde entfernt oder ausgeschaltet, oder seine Verbindung wurde es. Bis Sie neu wählen, wird standardmäßig nichts verwendet.'
MESSAGES['No model of this kind is added yet. Add one on a <a href="/admin/connections">connection</a>\'s Models tab.'] = 'Es ist noch kein Modell dieser Art hinzugefügt. Fügen Sie eines im Tab Modelle einer <a href="/admin/connections">Verbindung</a> hinzu.'
MESSAGES['The order sources are tried in'] = 'Die Reihenfolge, in der Quellen versucht werden'
MESSAGES["The chat's own connection, where that connection is set to use its own:"] = 'Die eigene Verbindung des Chats, wenn diese Verbindung ihre eigenen verwenden soll:'
MESSAGES['none is, at present.'] = 'derzeit keine.'
MESSAGES['The default above:'] = 'Der Standard oben:'
MESSAGES['nothing.'] = 'nichts.'
MESSAGES["One step, in this direction only, and the answer says which source gave it. A chat on a local model is never sent to a provider unless that provider's model is the default here."] = 'Ein Schritt, nur in dieser Richtung, und die Antwort sagt, welche Quelle sie geliefert hat. Ein Chat mit einem lokalen Modell wird nie an einen Anbieter geschickt, es sei denn, das Modell dieses Anbieters ist hier der Standard.'
MESSAGES['Read replies aloud by default'] = 'Antworten standardmäßig vorlesen'
MESSAGES["Where each person's own setting starts. It forces nothing on anyone."] = 'Womit die eigene Einstellung jeder Person beginnt. Es zwingt niemandem etwas auf.'
MESSAGES['Review of generated pictures'] = 'Prüfung generierter Bilder'
MESSAGES["A vision model looks at what a ComfyUI drew and says whether to keep it. A provider's model is never reviewed: every retry would be a picture somebody pays for."] = 'Ein Modell mit Bildverständnis sieht sich an, was ein ComfyUI gezeichnet hat, und sagt, ob es behalten wird. Das Modell eines Anbieters wird nie geprüft: Jeder neue Versuch wäre ein Bild, für das jemand bezahlt.'
MESSAGES['Review each picture before it is kept'] = 'Jedes Bild prüfen, bevor es behalten wird'
MESSAGES["The chat's own, when it has vision"] = 'Das eigene des Chats, wenn es Bilder sehen kann'
MESSAGES['Only models marked as having vision can review.'] = 'Nur Modelle, die als bildfähig markiert sind, können prüfen.'
MESSAGES['Tries'] = 'Versuche'
MESSAGES['How many pictures may be drawn for one request before the last is kept.'] = 'Wie viele Bilder für eine Anfrage gezeichnet werden dürfen, bevor das letzte behalten wird.'
MESSAGES['Limits and price'] = 'Limits und Preis'
MESSAGES['Pictures per API request'] = 'Bilder pro API-Anfrage'
MESSAGES['The most one <code>/v1/images/generations</code> may ask for.'] = 'Das Höchste, was ein <code>/v1/images/generations</code> anfordern darf.'
MESSAGES['Price per picture'] = 'Preis pro Bild'
MESSAGES['For a picture a ComfyUI draws, unless its checkpoint has a price of its own.'] = 'Für ein Bild, das ein ComfyUI zeichnet, sofern sein Checkpoint keinen eigenen Preis hat.'
MESSAGES['Instructions to the model'] = 'Anweisungen an das Modell'
MESSAGES["Given to every model that can draw, beside the tool's own guidance. Where “always add these words to the negative prompt” lives."] = 'Erhält jedes Modell, das zeichnen kann, neben den eigenen Hinweisen des Werkzeugs. Hierhin gehört „füge dem negativen Prompt immer diese Wörter hinzu“.'
MESSAGES['Drawing defaults for a ComfyUI'] = 'Zeichen-Standards für ein ComfyUI'
MESSAGES["What a generation uses when neither the request nor the checkpoint's own page says. Empty is no opinion: the built-in floor applies."] = 'Was eine Generierung verwendet, wenn weder die Anfrage noch die eigene Seite des Checkpoints etwas sagt. Leer heißt keine Vorgabe: Es gilt die eingebaute Untergrenze.'
MESSAGES['CFG'] = 'CFG'
MESSAGES['Pictures per run'] = 'Bilder pro Lauf'
MESSAGES['What to keep out of every picture, unless the request says its own.'] = 'Was aus jedem Bild herausgehalten werden soll, sofern die Anfrage nichts Eigenes nennt.'
MESSAGES['Kept and listed. Nothing on this instance asks a model of this capability for anything yet.'] = 'Behalten und aufgelistet. Noch fragt nichts in dieser Instanz ein Modell mit dieser Fähigkeit nach irgendetwas.'
MESSAGES["There is no Test for a provider's picture model: the only test would be a picture somebody pays for."] = 'Für das Bildmodell eines Anbieters gibt es kein Testen: Der einzige Test wäre ein Bild, für das jemand bezahlt.'
MESSAGES['For whoever administers this instance next: what it is, and why it is here.'] = 'Für alle, die diese Instanz als Nächste verwalten: was es ist und warum es hier ist.'
MESSAGES['On — may be used'] = 'An — darf verwendet werden'
MESSAGES['Off, it cannot be the default and is not offered to a chat.'] = 'Ausgeschaltet kann es nicht der Standard sein und wird keinem Chat angeboten.'
MESSAGES["Off, it serves neither its connection's own chats nor as the default."] = 'Ausgeschaltet bedient es weder die eigenen Chats seiner Verbindung noch dient es als Standard.'
MESSAGES["This model's connection is a subscription. What it does is counted and never priced: a figure would be made up."] = 'Die Verbindung dieses Modells ist ein Abonnement. Was es tut, wird gezählt und nie bepreist: Ein Betrag wäre erfunden.'
MESSAGES["What one picture from this checkpoint costs. Empty takes the instance's price per picture, under Media."] = 'Was ein Bild von diesem Checkpoint kostet. Leer übernimmt den Preis pro Bild der Instanz, unter Medien.'
MESSAGES["In the connection's currency, in the unit the provider bills in. Empty is free, which is what a server of your own is."] = 'In der Währung der Verbindung, in der Einheit, in der der Anbieter abrechnet. Leer heißt kostenlos, und das ist ein eigener Server.'
MESSAGES['How this model is asked'] = 'Wie dieses Modell angefragt wird'
MESSAGES["Each of these is this model's own. An empty box is no opinion: the instance's setting, or the provider's own default, applies."] = 'Jede dieser Angaben gehört nur diesem Modell. Ein leeres Feld heißt keine Vorgabe: Es gilt die Einstellung der Instanz oder der eigene Standard des Anbieters.'
MESSAGES["The server's own"] = 'Die eigene des Servers'
MESSAGES['Read in when a person has chosen none of their own.'] = 'Damit wird vorgelesen, wenn eine Person keine eigene gewählt hat.'
MESSAGES["0.25 to 4. A person's own speed wins."] = '0,25 bis 4. Die eigene Geschwindigkeit einer Person hat Vorrang.'
MESSAGES['Audio format'] = 'Audioformat'
MESSAGES['What the model is asked to answer in.'] = 'Worin das Modell antworten soll.'
MESSAGES['Language hint'] = 'Sprachhinweis'
MESSAGES['A code such as en or sk. Empty lets the model detect it, which it usually does best.'] = 'Ein Code wie en oder sk. Leer lässt das Modell die Sprache erkennen, was es meist am besten kann.'
MESSAGES['More is slower, not always better.'] = 'Mehr ist langsamer, nicht immer besser.'
MESSAGES['How closely to follow the prompt.'] = 'Wie genau dem Prompt gefolgt wird.'
MESSAGES['Voices'] = 'Stimmen'
MESSAGES['one voice per line'] = 'eine Stimme pro Zeile'
MESSAGES['The voices this model has, one per line: what a person chooses from where this model is the default. Empty asks the server each time.'] = 'Die Stimmen, die dieses Modell hat, eine pro Zeile: woraus eine Person wählt, wo dieses Modell der Standard ist. Leer fragt jedes Mal den Server.'
MESSAGES['Ask the server for its voices'] = 'Den Server nach seinen Stimmen fragen'
MESSAGES['Sizes'] = 'Größen'
MESSAGES['Width x height, one per line; the first is what a picture is drawn at when nothing asks for a size.'] = 'Breite x Höhe, eine pro Zeile; in der ersten wird ein Bild gezeichnet, wenn nichts eine Größe verlangt.'
MESSAGES['Remove this model'] = 'Dieses Modell entfernen'
MESSAGES['It is taken off its connection, with its options and its price. Nothing is deleted at the provider.'] = 'Es wird von seiner Verbindung genommen, samt seinen Optionen und seinem Preis. Beim Anbieter wird nichts gelöscht.'
MESSAGES['A checkpoint the ComfyUI still has comes back, switched off, at the next refresh.'] = 'Ein Checkpoint, den das ComfyUI noch hat, kommt bei der nächsten Aktualisierung ausgeschaltet zurück.'
MESSAGES['This is a default under Media. Removing it leaves that default pointing at nothing until you choose another there: nothing is quietly served in its place.'] = 'Dies ist ein Standard unter Medien. Wird es entfernt, zeigt dieser Standard auf nichts, bis Sie dort einen anderen wählen: Nichts wird stillschweigend an seiner Stelle verwendet.'
MESSAGES['Remove the model “%(name)s” from %(connection)s?'] = 'Das Modell „%(name)s“ von %(connection)s entfernen?'
MESSAGES['Icon'] = 'Symbol'
MESSAGES['Limits'] = 'Limits'
MESSAGES['Longest reply'] = 'Längste Antwort'
MESSAGES['The endpoint says this model writes at most %(n)s tokens in one reply. A ceiling only: no request asks for all of it by default.'] = 'Der Endpunkt gibt an, dass dieses Modell höchstens %(n)s Tokens in einer Antwort schreibt. Nur eine Obergrenze: Standardmäßig fordert keine Anfrage alles davon an.'
MESSAGES['The endpoint does not say how much this model can write in one reply.'] = 'Der Endpunkt gibt nicht an, wie viel dieses Modell in einer Antwort schreiben kann.'
MESSAGES['Pages and plans'] = 'Seiten und Pläne'
MESSAGES['Interactive use only'] = 'Nur interaktive Nutzung'
MESSAGES['Yes — from its connection, <a href="/admin/connections/%(id)s">%(name)s</a>, a provider\'s plan: a person\'s own turns only, never scheduled or background work.'] = 'Ja — durch seine Verbindung, <a href="/admin/connections/%(id)s">%(name)s</a>, den Plan eines Anbieters: nur die eigenen Züge einer Person, nie geplante Arbeit oder Hintergrundarbeit.'
MESSAGES['No. Its connection allows scheduled and background work.'] = 'Nein. Seine Verbindung erlaubt geplante Arbeit und Hintergrundarbeit.'
MESSAGES["Every model of every connection, whatever it is for: chat, embedding, speech, pictures. A line opens the model's own page; the switch at its end turns it on or off at once."] = 'Jedes Modell jeder Verbindung, wofür es auch ist: Chat, Embeddings, Sprache, Bilder. Eine Zeile öffnet die eigene Seite des Modells; der Schalter an ihrem Ende schaltet es sofort an oder aus.'
MESSAGES['The order of the chat models here is the order people see in the picker, and the <strong>default</strong> is what a new chat starts with.'] = 'Die Reihenfolge der Chat-Modelle hier ist die Reihenfolge, die man in der Auswahl sieht, und der <strong>Standard</strong> ist das, womit ein neuer Chat beginnt.'
MESSAGES['Every capability'] = 'Jede Fähigkeit'
MESSAGES['On or off'] = 'An oder aus'
MESSAGES['On and off'] = 'An und aus'
MESSAGES['Select every chat model on this page'] = 'Jedes Chat-Modell auf dieser Seite auswählen'
MESSAGES["Drag a chat model's handle to put the picker in order."] = 'Ziehen Sie am Griff eines Chat-Modells, um die Auswahl zu ordnen.'
MESSAGES['Every ComfyUI'] = 'Jedes ComfyUI'
MESSAGES["The ComfyUI this graph was written for: it needs that one's nodes and models. Offered only where that ComfyUI draws."] = 'Das ComfyUI, für das dieser Graph geschrieben wurde: Er braucht dessen Nodes und Modelle. Wird nur angeboten, wo dieses ComfyUI zeichnet.'
MESSAGES['%(name)s was a default under Media. Choose another there: until then nothing is served by default.'] = '%(name)s war ein Standard unter Medien. Wählen Sie dort einen anderen: Bis dahin wird standardmäßig nichts verwendet.'
MESSAGES['Overview'] = 'Übersicht'
MESSAGES['That page does not exist.'] = 'Diese Seite gibt es nicht.'
MESSAGES["A server of your own, or any cloud that speaks OpenAI's API. Chat and embedding models; speech and pictures too where it has them."] = 'Ein eigener Server oder jede Cloud, die die API von OpenAI spricht. Chat- und Embedding-Modelle; auch Sprache und Bilder, wo sie vorhanden sind.'
MESSAGES['Chat models, and its speech and picture models.'] = 'Chat-Modelle sowie seine Sprach- und Bildmodelle.'
MESSAGES['Chat models.'] = 'Chat-Modelle.'
MESSAGES['Chat models, with a balance to read.'] = 'Chat-Modelle, mit einem abfragbaren Guthaben.'
MESSAGES["Many providers' chat models behind one key."] = 'Chat-Modelle vieler Anbieter hinter einem Schlüssel.'
MESSAGES['Qwen and more, paid as used: chat, speech and picture models.'] = 'Qwen und mehr, nach Verbrauch bezahlt: Chat-, Sprach- und Bildmodelle.'
MESSAGES["A subscription, for a person's own turns only."] = 'Ein Abonnement, nur für die eigenen Züge einer Person.'
MESSAGES['A server that transcribes and reads aloud, such as whisper.cpp or Kokoro. No chat models.'] = 'Ein Server, der transkribiert und vorliest, etwa whisper.cpp oder Kokoro. Keine Chat-Modelle.'
MESSAGES['Pictures drawn with your own workflows and checkpoints. No chat models.'] = 'Bilder, gezeichnet mit Ihren eigenen Workflows und Checkpoints. Keine Chat-Modelle.'
MESSAGES['This connection cannot have a model of that kind.'] = 'Diese Verbindung kann kein Modell dieser Art haben.'
MESSAGES["Type the model's id."] = 'Geben Sie die ID des Modells ein.'
MESSAGES['That model is already here.'] = 'Dieses Modell ist schon hier.'
MESSAGES['This connection has as many models as it can keep.'] = 'Diese Verbindung hat so viele Modelle, wie sie halten kann.'
MESSAGES["%(name)s · the chat's checkpoint, or its first"] = '%(name)s · der Checkpoint des Chats, sonst sein erster'
MESSAGES['The server listed no voices. Type them below.'] = 'Der Server hat keine Stimmen aufgelistet. Geben Sie sie unten ein.'
MESSAGES['Found %(count)s voices.'] = '%(count)s Stimmen gefunden.'
MESSAGES['Only a model that is switched on, on a connection that is on, can be the default.'] = 'Nur ein eingeschaltetes Modell an einer eingeschalteten Verbindung kann der Standard sein.'
MESSAGES['Removed.'] = 'Entfernt.'
MESSAGES['Limits and reasoning'] = 'Limits und Denken'
MESSAGES['Speech server (OpenAI-compatible transcriptions and speech)'] = 'Sprachserver (OpenAI-kompatible Transkription und Sprachausgabe)'
MESSAGES['Picture editing'] = 'Bildbearbeitung'
MESSAGES['Video'] = 'Video'
MESSAGES['Music'] = 'Musik'
MESSAGES['3D models'] = '3D-Modelle'
MESSAGES['The ComfyUI has this checkpoint.'] = 'Das ComfyUI hat diesen Checkpoint.'
MESSAGES['The ComfyUI answered, and does not list this checkpoint.'] = 'Das ComfyUI hat geantwortet und führt diesen Checkpoint nicht auf.'
MESSAGES['There is nothing to try for this model without generating something.'] = 'Für dieses Modell gibt es nichts zu testen, ohne etwas zu generieren.'
MESSAGES['%(name)s has no model switched on yet. Add one under Models, then test again.'] = '%(name)s hat noch kein eingeschaltetes Modell. Fügen Sie eines unter Modelle hinzu und testen Sie dann erneut.'
MESSAGES['Import a workflow'] = 'Einen Workflow importieren'
MESSAGES['Starters for the other kinds'] = 'Startvorlagen für die anderen Arten'
MESSAGES["One way to make each kind of thing, built on ComfyUI's own nodes. Each names model files you must have, and none has been run against your ComfyUI: add one, press Test on its page, and switch it on once Test passes."] = 'Je ein Weg, jede Art von Ergebnis zu erzeugen, aufgebaut auf den eigenen Knoten von ComfyUI. Jede nennt Modelldateien, die Sie haben müssen, und keine wurde gegen Ihr ComfyUI ausgeführt: Fügen Sie eine hinzu, drücken Sie auf ihrer Seite Testen und schalten Sie sie ein, sobald Testen bestanden ist.'
MESSAGES['For pictures. Which workflow makes an edit, a video, music or a 3D model is chosen under Media.'] = 'Für Bilder. Welcher Workflow eine Bearbeitung, ein Video, Musik oder ein 3D-Modell erzeugt, wird unter Medien gewählt.'
MESSAGES['This is on'] = 'Ist eingeschaltet'
MESSAGES['On a ComfyUI, add a workflow of this kind on its Workflows tab: a starter is offered there.'] = 'Fügen Sie bei einem ComfyUI auf dessen Reiter Workflows einen Workflow dieser Art hinzu: Dort wird eine Startvorlage angeboten.'
MESSAGES['This takes minutes, so it runs as a job: the chat is answered at once and told when it is ready.'] = 'Das dauert Minuten und läuft deshalb als Auftrag: Der Chat erhält sofort eine Antwort und erfährt, wenn es fertig ist.'
MESSAGES["A ComfyUI makes these with a workflow of this kind; a provider with a model of this kind. A provider's model has its own price on its page."] = 'Ein ComfyUI erzeugt sie mit einem Workflow dieser Art, ein Anbieter mit einem Modell dieser Art. Das Modell eines Anbieters hat seinen eigenen Preis auf seiner Seite.'
MESSAGES['Price on a ComfyUI'] = 'Preis bei einem ComfyUI'
MESSAGES['Longest, in seconds'] = 'Längste Dauer, in Sekunden'
MESSAGES['Largest file kept, in MB'] = 'Größte aufbewahrte Datei, in MB'
MESSAGES['Jobs one person may have at once'] = 'Aufträge, die eine Person gleichzeitig haben darf'
MESSAGES['For video, music and 3D models together.'] = 'Für Video, Musik und 3D-Modelle zusammen.'
MESSAGES['Give up after, in minutes'] = 'Aufgeben nach, in Minuten'
MESSAGES["A starter: it was written from ComfyUI's stock nodes and has not been run against your ComfyUI. It is added switched off."] = 'Eine Startvorlage: Sie wurde aus den Standardknoten von ComfyUI geschrieben und nicht gegen Ihr ComfyUI ausgeführt. Sie wird ausgeschaltet hinzugefügt.'
MESSAGES['Imported and not saved yet: look the placeholders over, give it a name, and add it. It is added switched off.'] = 'Importiert und noch nicht gespeichert: Sehen Sie die Platzhalter durch, geben Sie ihm einen Namen und fügen Sie ihn hinzu. Er wird ausgeschaltet hinzugefügt.'
MESSAGES['After adding it, press Test on its page; switch it on once Test passes.'] = 'Drücken Sie nach dem Hinzufügen auf seiner Seite Testen; schalten Sie ihn ein, sobald Testen bestanden ist.'
MESSAGES['What it makes'] = 'Was er erzeugt'
MESSAGES['A picture, an edited picture, a video, music or a 3D model. It decides which tool uses it and which file is taken from what the run saves.'] = 'Ein Bild, ein bearbeitetes Bild, ein Video, Musik oder ein 3D-Modell. Das entscheidet, welches Werkzeug ihn verwendet und welche Datei aus dem genommen wird, was der Lauf speichert.'
MESSAGES['What this workflow uses'] = 'Was dieser Workflow verwendet'
MESSAGES['Placeholders it uses'] = 'Platzhalter, die er verwendet'
MESSAGES['Its kind requires'] = 'Seine Art erfordert'
MESSAGES['(missing)'] = '(fehlt)'
MESSAGES['Model files it expects'] = 'Modelldateien, die er erwartet'
MESSAGES['None named outright.'] = 'Keine ausdrücklich genannt.'
MESSAGES["The files must be in the ComfyUI's model folders under exactly these names. Nothing here checks that: Test does."] = 'Die Dateien müssen unter genau diesen Namen in den Modellordnern des ComfyUI liegen. Hier prüft das nichts: Das tut Testen.'
MESSAGES['Last test failed: %(why)s'] = 'Letzter Test fehlgeschlagen: %(why)s'
MESSAGES['Last test passed.'] = 'Letzter Test bestanden.'
MESSAGES['Not tested against a ComfyUI yet.'] = 'Noch nicht gegen ein ComfyUI getestet.'
MESSAGES['Runs the saved workflow once against its ComfyUI. It can take minutes.'] = 'Führt den gespeicherten Workflow einmal gegen sein ComfyUI aus. Das kann Minuten dauern.'
MESSAGES['Test this workflow'] = 'Diesen Workflow testen'
MESSAGES['Paste or choose a workflow exported from ComfyUI with <strong>Export (API)</strong>. You are shown where placeholders would go before anything is changed, and nothing is saved until you add it.'] = 'Fügen Sie einen Workflow ein, der aus ComfyUI mit <strong>Export (API)</strong> exportiert wurde, oder wählen Sie ihn aus. Ihnen wird gezeigt, wo Platzhalter hinkämen, bevor etwas geändert wird, und nichts wird gespeichert, bis Sie ihn hinzufügen.'
MESSAGES['A file'] = 'Eine Datei'
MESSAGES['Or pasted'] = 'Oder eingefügt'
MESSAGES['Read it'] = 'Einlesen'
MESSAGES['%(n)s nodes were read. Below is each place a placeholder is proposed, from what kind of node it is. Untick what you do not want; nothing else in the workflow is touched.'] = '%(n)s Knoten wurden eingelesen. Unten steht jede Stelle, an der ein Platzhalter vorgeschlagen wird, je nach Art des Knotens. Entfernen Sie den Haken bei dem, was Sie nicht möchten; sonst wird nichts im Workflow angerührt.'
MESSAGES['Read from its save nodes. Change it if that is wrong.'] = 'Aus seinen Speicherknoten gelesen. Ändern Sie es, falls es nicht stimmt.'
MESSAGES['in node %(node)s (%(cls)s), input %(input)s'] = 'in Knoten %(node)s (%(cls)s), Eingang %(input)s'
MESSAGES['now: %(value)s'] = 'jetzt: %(value)s'
MESSAGES['Nothing was proposed.'] = 'Es wurde nichts vorgeschlagen.'
MESSAGES['Use these and continue'] = 'Diese verwenden und fortfahren'
MESSAGES['Waiting to start'] = 'Wartet auf den Start'
MESSAGES['Being made'] = 'Wird erzeugt'
MESSAGES['Cancelled'] = 'Abgebrochen'
MESSAGES['Given up on'] = 'Aufgegeben'
MESSAGES['A workflow of this kind needs %(placeholder)s, and this one never uses it.'] = 'Ein Workflow dieser Art braucht %(placeholder)s, und dieser verwendet ihn nirgends.'
MESSAGES['There is no ComfyUI connection to test it on.'] = 'Es gibt keine ComfyUI-Verbindung, auf der er getestet werden könnte.'
MESSAGES['It did not pass: %(why)s'] = 'Nicht bestanden: %(why)s'
MESSAGES['It ran on %(name)s and made: %(what)s'] = 'Lief auf %(name)s und erzeugte: %(what)s'
MESSAGES['That job no longer exists.'] = 'Diesen Auftrag gibt es nicht mehr.'
MESSAGES['per second'] = 'pro Sekunde'
MESSAGES['per track'] = 'pro Titel'
MESSAGES['per model'] = 'pro Modell'
MESSAGES['What came back is not a picture.'] = 'Was zurückkam, ist kein Bild.'
MESSAGES['What came back is not a video.'] = 'Was zurückkam, ist kein Video.'
MESSAGES['What came back is not sound.'] = 'Was zurückkam, ist kein Ton.'
MESSAGES['What came back is not a 3D model this instance knows.'] = 'Was zurückkam, ist kein 3D-Modell, das diese Instanz kennt.'
MESSAGES['The file could not be given a name.'] = 'Der Datei konnte kein Name gegeben werden.'
MESSAGES['That is not a picture this instance can send.'] = 'Das ist kein Bild, das diese Instanz senden kann.'
MESSAGES['That picture is larger than the provider takes for editing.'] = 'Dieses Bild ist größer, als der Anbieter zum Bearbeiten annimmt.'
MESSAGES['The provider accepted the request but named no task.'] = 'Der Anbieter hat die Anfrage angenommen, aber keine Aufgabe genannt.'
MESSAGES['The provider could not make it.'] = 'Der Anbieter konnte es nicht erzeugen.'
MESSAGES['The provider no longer knows this task.'] = 'Der Anbieter kennt diese Aufgabe nicht mehr.'
MESSAGES['Resolutions'] = 'Auflösungen'
MESSAGES['Of 480P, 720P and 1080P, the ones this model takes, separated by commas. The first is the one asked for.'] = 'Von 480P, 720P und 1080P diejenigen, die dieses Modell annimmt, durch Kommas getrennt. Die erste wird angefordert.'
MESSAGES['Sign-in provider'] = 'Anmeldeanbieter'
MESSAGES['This client ID is pinned to <code>%(issuer)s</code>, the provider this server named when the client was first used. A sign-in is refused, and the secret sent nowhere, if the server later names another.'] = 'Diese Client-ID ist an <code>%(issuer)s</code> gebunden, den Anbieter, den dieser Server nannte, als der Client zum ersten Mal verwendet wurde. Nennt der Server später einen anderen, wird die Anmeldung abgelehnt und das Geheimnis nirgendwohin gesendet.'
MESSAGES['Re-pin at the next sign-in'] = 'Bei der nächsten Anmeldung neu binden'
MESSAGES['Re-pinning signs everyone out of this server.'] = 'Das Neubinden meldet alle von diesem Server ab.'
MESSAGES['Too many archives are being read at the moment. Open it again shortly.'] = 'Im Moment werden zu viele Archive gelesen. Öffnen Sie es gleich noch einmal.'
MESSAGES['%(name)s takes the provider it names at the next sign-in.'] = '%(name)s übernimmt bei der nächsten Anmeldung den Anbieter, den es nennt.'
