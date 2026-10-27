"""Slovenčina.

Keyed by the English source text, as `web/i18n/__init__.py` explains: a missing
entry renders the English, so a half-finished catalogue is a half-translated page
rather than a page with `settings.theme.label` written across it.

Two conventions, kept for consistency rather than because Slovak demands them:

* **Buttons and menu items are infinitives** -- "Uložiť", "Zmazať", "Poslať" --
  because a button says what pressing it does. A label above a field is a noun.
* **Second person plural for the reader** ("vy"), which is what a tool somebody
  else may also use should say. The models are spoken of in the third person.

⚠ `%(name)s` placeholders must survive. `i18n.t` falls back to the English
sentence if a translation's placeholders do not match, so a mistake here shows up
as an untranslated line rather than as a traceback -- but it still shows up.
"""

from __future__ import annotations

# What this language calls itself, as the language menus show it (2.0.0: this
# line is what makes the file a language -- see `web/i18n/__init__.py`).
NAME = "Slovenčina"

# Dates (2.1.0): the month in the form "26. septembra" wants, and the days, Monday first.
MONTHS = ("januára", "februára", "marca", "apríla", "mája", "júna",
          "júla", "augusta", "septembra", "októbra", "novembra", "decembra")
DAYS = ("pondelok", "utorok", "streda", "štvrtok", "piatok", "sobota", "nedeľa")

MESSAGES: dict[str, str] = {}

# --- Signing in, and the shell ------------------------------------------------
MESSAGES.update(
    {
        "Email": "E-mail",
        "Password": "Heslo",
        "Sign in": "Prihlásiť sa",
        "Sign out": "Odhlásiť sa",
        "Nobody has claimed this instance yet. The first account becomes its administrator.": (
            "Túto instanciu si ešte nikto nepriradil. Prvý účet sa stáva jej správcom."
        ),
        "At least 8 characters.": "Najmenej 8 znakov.",
        "Current password": "Súčasné heslo",
        "Confirm new password": "Potvrdenie nového hesla",
        "Change password": "Zmeniť heslo",
        "Every other session is signed out when the password changes. You stay signed in here.": (
            "Pri zmene hesla sa odhlásia všetky ostatné prihlásenia. Tu zostanete prihlásení."
        ),
        "Admin settings": "Nastavenia správy",
        "Your settings": "Vaše nastavenia",
        "Settings sections": "Časti nastavení",
        "People": "Ľudia",
        "Toggle sidebar": "Prepnúť bočný panel",
        "Close sidebar": "Zavrieť bočný panel",
        "Cancel": "Zrušiť",
        "Revert": "Vrátiť zmenu",
        "Try again": "Skúsiť znova",
        "Dismiss": "Zavrieť",
        "Discard": "Zahodiť",
        "Upload": "Nahrať",
        "Attach": "Priložiť",
        "Delete this": "Zmazať toto",
        "Forget": "Zabudnúť",
        "Forget this": "Zabudnúť toto",
        "Forget this?": "Zabudnúť toto?",
        "Remember": "Zapamätať",
        "Send now": "Poslať teraz",
        "Send to chat": "Poslať do konverzácie",
        "Save page": "Uložiť stránku",
        "Empty": "Prázdne",
        "Details": "Podrobnosti",
        "Title": "Názov",
        "Message": "Správa",
        "Text": "Text",
        "File": "Súbor",
        "Image": "Obrázok",
        "Kind": "Druh",
        "When": "Kedy",
        "At": "V",
        "Once": "Raz",
        "Inside": "Vnútri",
        "nobody": "nikto",
        "new": "nové",
        "shared": "zdieľané",
        "shared with you": "zdieľané s vami",
        "paused": "pozastavené",
        "scheduled": "naplánované",
        "remembered": "zapamätané",
        "set up for you": "nastavené pre vás",
        "written by a model": "napísal model",
        "edited by you": "upravili ste vy",
        "editing": "upravuje sa",
        "writing": "píše",
        "complete": "hotové",
        "error": "chyba",
        "failed": "zlyhalo",
        "estimated": "odhad",
        "no text": "bez textu",
        "weekly-report": "weekly-report",
    }
)

# --- The conversation ----------------------------------------------------------
MESSAGES.update(
    {
        "Chat": "Konverzácia",
        "Chats": "Konverzácie",
        "Chat settings": "Nastavenia konverzácie",
        "Agent chat": "Agentská konverzácia",
        "New chat here": "Nová konverzácia tu",
        "New chat in this folder": "Nová konverzácia v tejto zložke",
        "New folder": "Nová zložka",
        "Folder name": "Názov zložky",
        "Folder settings": "Nastavenia zložky",
        "Rename chat": "Premenovať konverzáciu",
        "Rename folder": "Premenovať zložku",
        "Delete chat": "Zmazať konverzáciu",
        "Delete folder": "Zmazať zložku",
        "Up a level": "O úroveň vyššie",
        "Temporary chat": "Dočasná konverzácia",
        "Which chats to show": "Ktoré konverzácie zobraziť",
        "Nothing was found.": "Nič sa nenašlo.",
        "Nothing has been answered in this chat yet.": (
            "V tejto konverzácii ešte nič nebolo odpovedané."
        ),
        "Last reply": "Posledná odpoveď",
        "What would you ask?": "Na čo sa chcete spýtať?",
        "Say it in your own words": "Povedzte to vlastnými slovami",
        "/ for commands": "/ pre príkazy",
        "Dictate a message": "Nadiktovať správu",
        "Drop to attach": "Pustite sem na priloženie",
        "Images, PDFs and text. A PDF has its text read once, now.": (
            "Obrázky, PDF a text. Z PDF sa text prečíta raz, hneď teraz."
        ),
        "PDF, text, code": "PDF, text, kód",
        "Sent only to vision models": "Posiela sa len modelom, ktoré vidia obrázky",
        "The generated image": "Vygenerovaný obrázok",
        "Copy message": "Skopírovať správu",
        "Copy code": "Skopírovať kód",
        "Edit message": "Upraviť správu",
        "Edit and retry from here": "Upraviť a pokračovať odtiaľto",
        "Files the device changed are not undone": "Súbory, ktoré zariadenie zmenilo, sa nevracajú späť",
        "Rewind and send": "Vrátiť sa a poslať",
        "Rewind the conversation": "Vrátiť konverzáciu",
        "Regenerate reply": "Vygenerovať odpoveď znova",
        "Discard this message?": "Zahodiť túto správu?",
        "Read this reply aloud": "Prečítať túto odpoveď nahlas",
        "Thinking": "Premýšľa",
        "Compact": "Zhrnúť",
        "Compacted": "Zhrnuté",
        "Summarise the earlier turns so they stop costing context": (
            "Zhrnúť starší priebeh, aby prestal zaberať kontext"
        ),
        "Earlier turns are summarised. They are still in the transcript; they just stop being sent.": (
            "Starší priebeh je zhrnutý. V zápise zostáva, len sa už neposiela."
        ),
        "These are no longer sent to the model; a summary of them goes instead. They are kept here so nothing is lost.": (
            "Tieto sa už modelu neposielajú, namiesto nich ide ich zhrnutie. "
            "Zostávajú tu, aby sa nič nestratilo."
        ),
        "The reply could not be completed.": "Odpoveď sa nepodarilo dokončiť.",
        "The model is asking you": "Model sa vás pýta",
        "Your own answer…": "Vaša vlastná odpoveď…",
        "Something else": "Niečo iné",
        "No opinion": "Bez názoru",
        "Ask me about these again": "Znova sa ma na to spýtať",
        "Always allow this": "Toto vždy povoliť",
        "Always allowed here": "Tu vždy povolené",
        "Change this before it runs": "Upraviť to pred spustením",
        "Allow runs what is in the box. It is not checked against this chat's rules again — you typed it.": (
            "Povolením sa spustí to, čo je v poli. Znova sa to neporovnáva s pravidlami "
            "tejto konverzácie — napísali ste to vy."
        ),
        "Carry it out": "Vykonať to",
        "Switches to Edit — commands still ask.": (
            "Prepne do režimu Edit — príkazy sa stále pýtajú."
        ),
        "What should happen": "Čo sa má stať",
        "What was found": "Čo sa našlo",
        "What is there now": "Čo tam je teraz",
        "Your version": "Vaša verzia",
        "Discard mine and reload": "Zahodiť moje a načítať znova",
        "A model wrote this version.": "Túto verziu napísal model.",
        "Canvas": "Plocha",
        "Close canvas": "Zavrieť plochu",
        "Resize the canvas": "Zmeniť veľkosť plochy",
        "Open a file beside the conversation": "Otvoriť súbor pri konverzácii",
        "This file is empty.": "Tento súbor je prázdny.",
        "No colour while you type. Tab inserts a tab.": (
            "Počas písania bez zvýraznenia. Tabulátor vloží tabulátor."
        ),
        "Markdown.": "Markdown.",
        "Rendered": "Vykreslené",
        "Inspect this chat": "Preskúmať túto konverzáciu",
        "Request inspector": "Prehliadka požiadavky",
        "Close inspector": "Zavrieť prehliadku",
        "Request body": "Telo požiadavky",
        "System message": "Systémová správa",
        "None is being sent.": "Neposiela sa žiadna.",
        "Rebuilt now against the current configuration. This is not a recording of the request that produced the last reply — if a prompt or a setting has changed since, this shows what would be sent today.": (
            "Zloží sa teraz podľa súčasného nastavenia. Nie je to záznam požiadavky, "
            "ktorá vytvorila poslednú odpoveď — ak sa odvtedy zmenil pokyn alebo "
            "nastavenie, vidíte, čo by sa poslalo dnes."
        ),
        "In the window now": "Teraz v okne",
        "Of which sent": "Z toho poslané",
        "Of which written": "Z toho napísané",
        "Spent in total": "Spolu spotrebované",
        "Max tokens": "Najviac tokenov",
        "Temperature": "Teplota",
        "Top-p": "Top-p",
        "Leave a field empty to let the provider decide. Values outside the allowed range are ignored rather than clamped.": (
            "Prázdne pole znamená, že rozhodne poskytovateľ. Hodnoty mimo povoleného "
            "rozsahu sa ignorujú, neupravujú sa na najbližšiu možnú."
        ),
        "Reasoning effort": "Náročnosť uvažovania",
        "Endpoint default": "Predvolené podľa endpointu",
        "Choose a model": "Vyberte model",
        "Filter models": "Filtrovať modely",
        "Filter models…": "Filtrovať modely…",
        "No model matches that.": "Tomu nezodpovedá žiadny model.",
        "No models available": "Žiadne dostupné modely",
        "No models are available to you yet.": "Zatiaľ pre vás nie sú dostupné žiadne modely.",
        "In the order an administrator arranged them.": "V poradí, ako ich zoradil správca.",
        "Available to you": "Dostupné pre vás",
        "Default model": "Predvolený model",
        "Overrides what the administrator chose, for you only.": (
            "Prebije voľbu správcu, ale len pre vás."
        ),
        "What a new chat starts as": "Ako začína nová konverzácia",
        "What a new chat starts with. Existing chats keep their model.": (
            "S čím začína nová konverzácia. Existujúce konverzácie si svoj model nechávajú."
        ),
    }
)

# --- Knowledge, notes and skills ----------------------------------------------
MESSAGES.update(
    {
        "Library": "Knižnica",
        "Knowledge": "Znalosti",
        "Knowledge base": "Znalostná báza",
        "New knowledge base": "Nová znalostná báza",
        "Delete knowledge base": "Zmazať znalostnú bázu",
        "No knowledge bases yet": "Zatiaľ žiadne znalostné bázy",
        "Knowledge bases are collections of documents, images and saved web pages. Keep them separate — one per subject, project or client — and a chat can be pointed at just the ones it should draw on. Sharing happens here too: share a base and everything in it comes with it.": (
            "Znalostné bázy sú zbierky dokumentov, obrázkov a uložených webových "
            "stránok. Držte ich oddelene — jednu na tému, projekt alebo klienta — a "
            "konverzáciu potom nasmerujete len na tie, z ktorých má čerpať. Zdieľanie "
            "je tiež tu: zdieľaním bázy ide s ňou všetko, čo je v nej."
        ),
        "Make one below, then put documents in it. A chat with no base attached searches everything you have; a chat pointed at one searches only that.": (
            "Vytvorte jednu nižšie a vložte do nej dokumenty. Konverzácia bez "
            "pripojenej bázy prehľadáva všetko, čo máte; konverzácia nasmerovaná na "
            "bázu prehľadáva len ju."
        ),
        "Add to this base": "Pridať do tejto bázy",
        "This base": "Táto báza",
        "Search this base…": "Hľadať v tejto báze…",
        "In your library": "Vo vašej knižnici",
        "From your library": "Z vašej knižnice",
        "Search only these": "Hľadať len v týchto",
        "Scope this chat to this knowledge base": "Zúžiť túto konverzáciu na túto bázu",
        "This chat now searches only the bases it is attached to": (
            "Táto konverzácia teraz prehľadáva len pripojené bázy"
        ),
        "Already in this chat": "Už v tejto konverzácii",
        "Delete document": "Zmazať dokument",
        "A file": "Súbor",
        "A folder inside another inherits its system prompt where it has none of its own.": (
            "Zložka v inej zložke prevezme jej systémový pokyn, ak nemá vlastný."
        ),
        "A page to read": "Stránka na prečítanie",
        "A web page": "Webová stránka",
        "Fetch a page and attach its text": "Načítať stránku a priložiť jej text",
        "Fetch this page": "Načítať túto stránku",
        "Fetched now and kept as text, so it survives the page changing.": (
            "Načíta sa teraz a uloží ako text, takže prežije zmenu stránky."
        ),
        "Notes": "Poznámky",
        "Delete note": "Zmazať poznámku",
        "Search notes…": "Hľadať v poznámkach…",
        "What this note is about": "O čom je táto poznámka",
        "Longer things worth keeping between conversations. A model writes these itself when it works something out, and you can edit or delete any of them — they are yours, not its.": (
            "Dlhšie veci, ktoré sa vyplatí uchovať medzi konverzáciami. Model si ich "
            "píše sám, keď na niečo príde, a vy ich môžete upraviť alebo zmazať — sú "
            "vaše, nie jeho."
        ),
        "Skills": "Schopnosti",
        "Delete skill": "Zmazať schopnosť",
        "Search skills…": "Hľadať v schopnostiach…",
        "Offer this skill to models": "Nabídnuť túto schopnosť modelom",
        "Turned off, it stays here but disappears from the list the model sees.": (
            "Vypnutá tu zostáva, ale zmizne zo zoznamu, ktorý vidí model."
        ),
        "Instructions": "Pokyny",
        "Markdown. Steps, conventions, things to avoid.": (
            "Markdown. Postup, dohody, čomu sa vyhnúť."
        ),
        "When to use it": "Kedy ju použiť",
        "What this is, and when it is worth reading.": "Čo to je a kedy sa to vyplatí prečítať.",
        "The load-bearing field.": "Toto pole je nosné.",
        "Shown in search results, so it is what decides whether a model opens it.": (
            "Zobrazuje sa vo výsledkoch hľadania, takže rozhoduje o tom, či to model otvorí."
        ),
        "Searched along with the contents, so a line here helps a model find it.": (
            "Prehľadáva sa spolu s obsahom, takže jeden riadok tu modelu pomôže to nájsť."
        ),
        "Lowercase letters, numbers and hyphens. This is how a model asks for it, and it cannot be changed later.": (
            "Malé písmená, číslice a spojovníky. Takto si to model vyžiada a neskôr sa "
            "to už nedá zmeniť."
        ),
        "Saved procedures. Every enabled skill's name and description are shown to the model on each turn; it reads the instructions only when it decides one applies. A model may write and revise its own — every version is kept, so any change can be read and undone.": (
            "Uložené postupy. Názov a opis každej zapnutej schopnosti vidí model v "
            "každom kroku; samotné pokyny si prečíta až vtedy, keď usúdi, že sa "
            "niektorá hodí. Model si môže písať a upravovať vlastné — každá verzia "
            "sa uchová, takže každú zmenu si možno prečítať aj vrátiť."
        ),
        "What this skill said before each change. This is the whole safety story for a model editing its own instructions: not a gate, but a record and a way back.": (
            "Čo táto schopnosť hovorila pred každou zmenou. To je celá bezpečnosť "
            "toho, že si model upravuje vlastné pokyny: nie zákaz, ale záznam a cesta "
            "späť."
        ),
        "Put the skill back to this version? The current one is kept in the history.": (
            "Vrátiť schopnosť na túto verziu? Súčasná zostane v histórii."
        ),
        "Shared with": "Zdieľané s",
        "Shared with you. You can read this but not change it.": (
            "Zdieľané s vami. Môžete to čítať, ale nie meniť."
        ),
        "Find somebody": "Nájsť niekoho",
        "Name, email or group…": "Meno, e-mail alebo skupina…",
        "Loading who this is shared with": "Načítava sa, s kým je toto zdieľané",
        "Showing the first few. Type to narrow it — anything already shared stays listed whatever you search for.": (
            "Zobrazuje sa prvých niekoľko. Písaním to zúžite — čo je už zdieľané, "
            "zostáva v zozname bez ohľadu na to, čo hľadáte."
        ),
        "They will be able to read this, and their models will find it. They cannot change it, delete it, or share it on — so “who can see this?” stays a question you can answer.": (
            "Budú to môcť čítať a ich modely to nájdu. Nemôžu to zmeniť, zmazať ani "
            "zdieľať ďalej — takže „kto to vidí?“ zostáva otázkou, na ktorú viete "
            "odpovedať."
        ),
        "Moving it changes who can see it.": "Presunutím sa zmení, kto to vidí.",
        "What belongs in here.": "Čo tu patrí.",
        "Nothing — a folder at the top": "Nič — zložka na najvyššej úrovni",
        "Contracts": "Zmluvy",
    }
)

# --- Memory, personality and settings -----------------------------------------
MESSAGES.update(
    {
        "Theme": "Vzhľad",
        "Saved to this browser and to your account, so it follows you.": (
            "Uloží sa do tohto prehliadača aj k vášmu účtu, takže vás sleduje."
        ),
        "Timezone": "Časová zóna",
        "Your timezone": "Vaša časová zóna",
        "Use the instance default": "Použiť predvoľbu instancie",
        "What time a model is told it is, and the zone anything you schedule runs in. Leave it unset to follow the server.": (
            "Aký čas sa modelu oznamuje a v akej zóne beží všetko, čo si naplánujete. "
            "Ak to nenastavíte, riadi sa serverom."
        ),
        "That is not a timezone this server knows about.": (
            "Takúto časovú zónu tento server nepozná."
        ),
        "What you see the interface in. Saved to your account rather than to this browser, so it follows you — and it overrides whatever the instance is set to.": (
            "V akom jazyku vidíte rozhranie. Uloží sa k vášmu účtu, nie do tohto "
            "prehliadača, takže vás sleduje — a prebíja to, čo je nastavené pre celú "
            "instanciu."
        ),
        "The interface only. Models answer in the language you write to them in, whatever this says.": (
            "Len rozhranie. Modely odpovedajú v jazyku, v ktorom im píšete, bez ohľadu "
            "na toto nastavenie."
        ),
        "Voice": "Hlas",
        "Dictation": "Diktovanie",
        "Reading replies aloud": "Čítanie odpovedí nahlas",
        "Read each reply aloud as it finishes": "Prečítať každú odpoveď nahlas, keď dopíše",
        "Only replies that arrive while you are looking at the chat.": (
            "Len odpovede, ktoré prídu, kým sa na konverzáciu pozeráte."
        ),
        "Save audio preferences": "Uložiť nastavenia zvuku",
        "Whatever is default at the time": "Čokoľvek je vtedy predvolené",
        "The microphone needs HTTPS or localhost — browsers do not grant it over plain HTTP.": (
            "Mikrofón potrebuje HTTPS alebo localhost — po obyčajnom HTTP ho "
            "prehliadače nepovolia."
        ),
        "Notifications": "Upozornenia",
        "Turn on notifications": "Zapnúť upozornenia",
        "A reply, a filed report or a scheduled run arriving while you are looking at something else. In the page always; from the browser as well if you allow it here.": (
            "Odpoveď, podaná správa alebo naplánovaný beh, ktoré prídu, kým sa "
            "pozeráte inam. V stránke vždy; z prehliadača tiež, ak to tu povolíte."
        ),
        "Kept per browser, because the permission is. Nothing is shown while you are looking at the page — the toast is there for that.": (
            "Uchováva sa pre každý prehliadač zvlášť, pretože povolenie je tiež "
            "také. Kým sa pozeráte na stránku, nič sa nezobrazí — na to je bublina."
        ),
        "Checking what this browser allows…": "Zisťuje sa, čo tento prehliadač dovoľuje…",
        "Install as an app": "Nainštalovať ako aplikáciu",
        "Runs in its own window, without browser chrome. Everything still comes from your server — there is no offline mode beyond a page saying so.": (
            "Beží vo vlastnom okne, bez ovládacích prvkov prehliadača. Všetko aj tak "
            "prichádza z vášho servera — offline režim je len stránka, ktorá to oznámi."
        ),
        "Installing needs a secure connection — HTTPS with a certificate this device trusts, or localhost — and some browsers never offer it. On iOS, use Share → Add to Home Screen.": (
            "Inštalácia potrebuje bezpečné spojenie — HTTPS s certifikátom, ktorému "
            "toto zariadenie verí, alebo localhost — a niektoré prehliadače ju "
            "neponúknu vôbec. Na iOS použite Zdieľať → Pridať na plochu."
        ),
        "Something worth remembering": "Niečo, čo sa vyplatí zapamätať",
        "What this memory says": "Čo si táto pamäť pamätá",
        "Prefers metric units and a 24-hour clock.": (
            "Preferuje metrické jednotky a 24-hodinový čas."
        ),
        "Nothing yet. A model with the memory tool adds to this when you tell it something worth keeping.": (
            "Zatiaľ nič. Model, ktorý má nástroj pamäti, tu pridá záznam, keď mu "
            "povieš niečo, čo sa vyplatí uchovať."
        ),
        "For you, not for any model. It is never sent anywhere.": (
            "Pre vás, nie pre model. Nikam sa to neposiela."
        ),
        "Session": "Prihlásenie",
    }
)

# --- Agent connections, the terminal and background work ----------------------
MESSAGES.update(
    {
        "Project directory": "Adresár projektu",
        "Choose the directory this chat works in": (
            "Vyberte adresár, v ktorom táto konverzácia pracuje"
        ),
        "Approval mode": "Režim schvaľovania",
        "Terminal": "Terminál",
        "Close terminal": "Zavrieť terminál",
        "Resize the terminal": "Zmeniť veľkosť terminálu",
        "Connecting…": "Pripája sa…",
        "Copy the last command and its output": "Skopírovať posledný príkaz a jeho výstup",
        "Put the last command and its output into the message box": (
            "Vložiť posledný príkaz a jeho výstup do políčka správy"
        ),
        "What to do when a command finishes": "Čo robiť, keď príkaz dobehne",
        "What to do with each command you run": "Čo robiť s každým príkazom, ktorý spustíte",
        "Auto: off": "Automaticky: nič",
        "Auto: copy": "Automaticky: skopírovať",
        "Auto: send": "Automaticky: poslať",
        "Opening…": "Otvára sa…",
        "Put this in the message box as an attachment": "Vložiť do políčka správy ako prílohu",
        "This chat": "Táto konverzácia",
        "Keep this chat": "Nechať túto konverzáciu",
        "Window size": "Veľkosť okna",
        "Why not, and what to do instead": "Prečo nie a čo urobiť namiesto toho",
        "Why not, and what to do instead…": "Prečo nie a čo urobiť namiesto toho…",
        "None. A model has to be marked as supporting tools, the user needs the permission, and the tool itself has to be turned on.": (
            "Žiadne. Model musí byť označený, že podporuje nástroje, používateľ "
            "potrebuje oprávnenie a samotný nástroj musí byť zapnutý."
        ),
    }
)

# --- Folders, reports, schedules and messages ---------------------------------
MESSAGES.update(
    {
        "What this folder is for.": "Na čo je táto zložka.",
        "Used by every chat in this folder, and by folders nested inside it, unless the chat has a prompt of its own. Read each time a reply is built rather than copied when a chat is made, so editing this reaches the chats already here.": (
            "Použije ho každá konverzácia v tejto zložke aj vo zložkách v nej, ak "
            "konverzácia nemá vlastný pokyn. Číta sa pri každom zostavení odpovede, "
            "nekopíruje sa pri vytvorení konverzácie — úprava teda zasiahne aj "
            "konverzácie, ktoré tu už sú."
        ),
        "Leave empty to fall through to the model's prompt, then the instance's.": (
            "Prázdne znamená prepadnutie na pokyn modelu a potom instancie."
        ),
        "Leave empty to use the instance default.": "Prázdne znamená predvoľbu instancie.",
        "Precedence, not concatenation: chat, then folder, then model, then instance. The most specific one wins outright.": (
            "Prednosť, nie skladanie: konverzácia, potom zložka, potom model, potom "
            "instancia. Najkonkrétnejší vyhráva úplne."
        ),
        "Seeds, copied onto a chat when it is created and its own from then on. Anything chosen on the new-chat screen wins over these.": (
            "Základ, ktorý sa skopíruje na konverzáciu pri vytvorení a odvtedy je jej "
            "vlastný. Čo vyberiete na obrazovke novej konverzácie, má prednosť."
        ),
        "A folder set to one kind shows on only that side of the sidebar's switch, and opens the new-chat screen already on that fork.": (
            "Zložka nastavená na jeden druh sa zobrazí len na tej strane prepínača v "
            "bočnom paneli a obrazovku novej konverzácie otvorí už na tej vetve."
        ),
        "What a model is given when it reads this.": "Čo model dostane, keď si to prečíta.",
        "When the user asks for the weekly report.": "Keď si používateľ vyžiada týždennú správu.",
        "Reports": "Správy",
        "New reports": "Nové správy",
        "New reply": "Nová odpoveď",
        "Search reports…": "Hľadať v správach…",
        "This run did not finish cleanly.": "Tento beh sa nedokončil čisto.",
        "Delete this report? It cannot be brought back.": (
            "Zmazať túto správu? Už sa nedá vrátiť."
        ),
        "Finished work, filed to be read later. A model writes one when you ask for it or when it finishes something worth keeping, and anything running on a schedule leaves its result here. Nothing on this page can be replied to.": (
            "Hotová práca, odložená na prečítanie neskôr. Model ju napíše, keď si ju "
            "vyžiadate alebo keď dokončí niečo, čo sa vyplatí uchovať, a všetko, čo "
            "beží podľa plánu, tu necháva svoj výsledok. Na nič na tejto stránke sa "
            "nedá odpovedať."
        ),
        "Nothing scheduled": "Nič naplánované",
        "Work that runs on its own, whether or not you are here. Each one has its own chat, and replies into it every time it comes round.": (
            "Práca, ktorá beží sama, či tu ste alebo nie. Každá má vlastnú "
            "konverzáciu a pri každom opakovaní do nej odpovedá."
        ),
        "Set something to run later — a daily summary, a check every Monday morning, a reminder in an hour.": (
            "Nastavte niečo na neskôr — denné zhrnutie, kontrolu každé pondelkové "
            "ráno, pripomienku za hodinu."
        ),
        "files a report": "podá správu",
        "Monday build check": "Pondelková kontrola buildu",
        "What this appears as in the list.": "Ako sa to zobrazí v zozname.",
        "Check whether the build is passing and summarise anything that broke since last week.": (
            "Skontroluj, či build prechádza, a zhrň všetko, čo sa od minulého týždňa "
            "pokazilo."
        ),
        "Every Monday morning, check whether the build is passing and write me a report.": (
            "Každé pondelkové ráno skontroluj, či build prechádza, a napíš mi správu."
        ),
        "Written for a model that will read it with no conversation around it, so say the whole thing. Nobody will be there to answer a question about it.": (
            "Píše sa pre model, ktorý to prečíta bez konverzácie okolo, takže povedzte "
            "celú vec. Nikto tam nebude, aby na otázku odpovedal."
        ),
        "Where the result goes": "Kam ide výsledok",
        "Every so often": "Každú chvíľu",
        "On particular days": "V určité dni",
        "Starting": "Začína",
        "Repeat every": "Opakovať každých",
        "On these days": "V tieto dni",
        "Leave all of them unticked for every day.": "Nechajte všetky nezaškrtnuté pre každý deň.",
        "One or more times, separated by commas. These are wall-clock times: 09:00 stays 09:00 when the clocks change.": (
            "Jeden alebo viac časov, oddelených čiarkami. Ide o časy na hodinách: "
            "09:00 zostáva 09:00 aj po zmene času."
        ),
        "Only on these dates": "Len v tieto dni",
        "Days of the month, if you want it narrower. Optional.": (
            "Dni v mesiaci, ak to chcete zúžiť. Nepovinné."
        ),
        "Stop after": "Zastaviť po",
        "Number of runs. Leave it at 0 to keep going until you stop it.": (
            "Počet behov. Nechajte 0, aby to pokračovalo, kým to nezastavíte."
        ),
        "Or stop on": "Alebo zastaviť dňa",
        "Optional. Nothing runs after this date.": "Nepovinné. Po tomto dni už nič nebeží.",
        "Run it now without using up the next scheduled run": (
            "Spustiť teraz bez toho, aby sa spotreboval ďalší naplánovaný beh"
        ),
        "Remove this schedule? It will stop running.": "Odstrániť tento plán? Prestane bežať.",
        "Keep its chat": "Nechať jeho konverzáciu",
        "Stops it running. Its chat is kept by default and becomes an ordinary one, so the transcript of everything it has already done stays where it is.": (
            "Prestane bežať. Jeho konverzácia sa predvolene zachová a stane sa "
            "obyčajnou, takže zápis všetkého, čo už urobil, zostáva na svojom mieste."
        ),
        "This chat belonged to a schedule that has been removed. It is kept as a record of what was done.": (
            "Táto konverzácia patrila plánu, ktorý bol odstránený. Zostáva ako záznam "
            "toho, čo sa urobilo."
        ),
        "What you can do": "Čo môžete robiť",
        "Add one": "Pridať",
    }
)

# --- Date formats -------------------------------------------------------------
# The pattern, not the words: Slovak puts a full stop after the day and says "v"
# rather than "on". `i18n.stamp` translates the format and then substitutes the
# month and weekday names, which are in the genitive because that is what a date
# takes.
MESSAGES.update(
    {
        "%H:%M on %A %-d %B": "%H:%M v %A, %-d. %B",
        "%d %B %Y at %H:%M": "%d. %B %Y v %H:%M",
    }
)

# --- Administration: the pages and their controls ------------------------------
MESSAGES.update(
    {
        "Administration": "Správa",
        "General": "Všeobecné",
        "Language": "Jazyk",
        "Interface language": "Jazyk rozhrania",
        "Connections": "Spojenia",
        "All connections": "Všetky spojenia",
        "All tools": "Všetky nástroje",
        "All servers": "Všetky servery",
        "All models": "Všetky modely",
        "Add a connection": "Pridať spojenie",
        "Models": "Modely",
        "Model": "Model",
        "Context window": "Kontextové okno",
        "Sees images": "Vidí obrázky",
        "Loaded": "Načítaný",
        "Loading": "Načítava sa",
        "Groups": "Skupiny",
        "Members": "Členovia",
        "Account": "Účet",
        "Agents": "Agenti",
        "Agent mode": "Režim agenta",
        "Audio": "Zvuk",
        "Images": "Obrázky",
        "Image generation": "Generovanie obrázkov",
        "Extraction": "Extrakcia",
        "Memory": "Pamäť",
        "Helpers": "Pomocníci",
        "Custom tools": "Vlastné nástroje",
        "MCP servers": "Servery MCP",
        "Guidance": "Vedenie",
        "Capabilities": "Schopnosti",
        "Availability": "Dostupnosť",
        "Description": "Opis",
        "Display name": "Zobrazované meno",
        "Name": "Názov",
        "Identifier": "Identifikátor",
        "Length": "Dĺžka",
        "Format": "Formát",
        "Method": "Metóda",
        "Headers": "Hlavičky",
        "Body": "Telo",
        "Link": "Odkaz",
        "Add": "Pridať",
        "Enable": "Zapnúť",
        "Disable": "Vypnúť",
        "Delete": "Zmazať",
        "Next": "Ďalej",
        "Move up": "Posunúť vyššie",
        "Move down": "Posunúť nižšie",
        "Make public": "Zverejniť",
        "Active": "Aktívne",
        "Auto": "Automaticky",
        "Asking you things": "Otázky pre vás",
        "Attached files": "Priložené súbory",
        "Back to chats": "Späť na konverzácie",
        "Baseline permissions": "Základné oprávnenia",
        "Groups with access": "Skupiny s prístupom",
        "Models they can use": "Modely, ktoré môžu používať",
        "Models this group unlocks": "Modely, ktoré táto skupina odomkne",
        "Limits in force": "Platné obmedzenia",
        "New group name": "Názov novej skupiny",
        "New password": "Nové heslo",
        "Add an account": "Pridať účet",
        "Add a suggestion": "Pridať návrh",
        "Delete suggestion": "Zmazať návrh",
        "No groups yet. Everybody gets the baseline above and nothing more.": (
            "Zatiaľ žiadne skupiny. Každý dostane základ uvedený vyššie a nič viac."
        ),
        "In no group. They get the baseline and nothing more.": (
            "V žiadnej skupine. Dostanú základ a nič viac."
        ),
        "An administrator bypasses every permission and every quota below.": (
            "Správca obchádza každé oprávnenie a každú kvótu nižšie."
        ),
        "Edited from the group's own page. One control per value, so a save here cannot undo a save there.": (
            "Upravuje sa na vlastnej stránke skupiny. Jeden ovládací prvok na hodnotu, "
            "takže uloženie tu nemôže zrušiť uloženie tam."
        ),
        "Members keep their accounts and lose whatever this group granted them. Every share naming this group goes too — nothing cascades to those, so they are deleted explicitly.": (
            "Členovia si svoje účty nechajú a stratia to, čo im táto skupina "
            "poskytovala. Zmizne aj každé zdieľanie, ktoré túto skupinu menuje — nič "
            "sa na ne nekaskáduje, takže sa mažú výslovne."
        ),
        "Model access is separate from permissions: a permission says what somebody may do, this says what they may do it with.": (
            "Prístup k modelom je oddelený od oprávnení: oprávnenie hovorí, čo niekto "
            "smie robiť, toto hovorí, s čím to smie robiť."
        ),
        "A model marked public is available to everyone; one that is not is available to the groups named here. Model access is separate from permissions — one says what somebody may do, the other what with.": (
            "Model označený ako verejný je dostupný všetkým; ten, ktorý nie je, je "
            "dostupný skupinám uvedeným tu. Prístup k modelom je oddelený od "
            "oprávnení — jedno hovorí, čo niekto smie, druhé s čím."
        ),
        "Ignored while the model is available to everyone.": (
            "Ignoruje sa, kým je model dostupný všetkým."
        ),
        "Ignored while the server is available to everyone.": (
            "Ignoruje sa, kým je server dostupný všetkým."
        ),
        "Ignored while the tool is available to everyone.": (
            "Ignoruje sa, kým je nástroj dostupný všetkým."
        ),
        "Available to everyone": "Dostupné všetkým",
        "Instance-wide settings. These are stored in the database and take effect immediately — no restart, and they survive one.": (
            "Nastavenia pre celú instanciu. Ukladajú sa do databázy a platia okamžite "
            "— bez restartu, a restart prežijú."
        ),
        "Default system prompt": "Predvolený systémový pokyn",
        "Applied to every chat that does not have a prompt of its own. A model's prompt overrides this, and a chat's prompt overrides both — most specific wins outright rather than the three being stacked together.": (
            "Použije sa na každú konverzáciu, ktorá nemá vlastný pokyn. Pokyn modelu "
            "toto prebije a pokyn konverzácie prebije oba — najkonkrétnejší vyhráva "
            "úplne, namiesto toho, aby sa všetky tri skladali."
        ),
        "Leave empty to send no system prompt at all.": (
            "Prázdne znamená neposielať žiadny systémový pokyn."
        ),
        "Compaction": "Zhrnutie",
        "Compact at": "Zhrnúť pri",
        "A long conversation eventually fills the model's context. When it gets close, the earlier turns are summarised and the summary is sent in their place. The messages themselves are kept and stay readable in the transcript — they simply stop being sent.": (
            "Dlhá konverzácia nakoniec zaplní kontext modelu. Keď sa priblíži, starší "
            "priebeh sa zhrnie a namiesto neho sa posiela zhrnutie. Samotné správy "
            "zostávajú a v zápise sú stále čitateľné — len sa prestanú posielať."
        ),
        "Most rounds of tool calls": "Najviac kôl volania nástrojov",
        "A model ends its own turn the moment it stops asking for tools — that is it saying it has what it needs, and nothing here overrides it. This is a ceiling for the case where it never says so.": (
            "Model ukončí svoj krok vo chvíli, keď si prestane žiadať nástroje — tým "
            "hovorí, že má, čo potrebuje, a nič tu to neprebíja. Toto je strop pre "
            "prípad, že to nepovie nikdy."
        ),
        "A backstop, as it is above. The clock and the token ceiling are what normally end one.": (
            "Záchranná sieť, ako vyššie. Beh obyčajne ukončia hodiny a strop tokenov."
        ),
        "A ceiling — a model asking for more gets this.": (
            "Strop — model, ktorý si žiada viac, dostane toto."
        ),
        "Chat title request": "Požiadavka na názov konverzácie",
        "Empty, so no model is asked to name a chat. Chats are named from the first thing said in them.": (
            "Prázdne, takže o názov konverzácie sa nežiada žiadny model. Konverzácie "
            "sa pomenúvajú podľa prvej vety, ktorá v nich padne."
        ),
    }
)

# --- Administration: connections, models and endpoints -------------------------
MESSAGES.update(
    {
        "API URL": "URL rozhrania",
        "API key": "Kľúč rozhrania",
        "Base URL": "Základná URL",
        "Endpoint URL": "URL endpointu",
        "Instance URL": "URL instancie",
        "Extra headers": "Ďalšie hlavičky",
        "Header or parameter name": "Názov hlavičky alebo parametra",
        "How it is sent": "Ako sa posiela",
        "How to ask it to unload": "Ako ho požiadať o uvoľnenie",
        "Credential": "Prihlasovací údaj",
        "No key set": "Kľúč nie je nastavený",
        "No secret set": "Tajný údaj nie je nastavený",
        "No models configured": "Žiadne nastavené modely",
        "Encrypted at rest. Clear the field to remove it.": (
            "Zašifrované pri uložení. Vymazaním poľa ho odstránite."
        ),
        "Encrypted before it is stored, and never sent back to the browser. Leave empty for endpoints that need no key.": (
            "Zašifruje sa pred uložením a nikdy sa neposiela späť do prehliadača. Pre "
            "endpointy, ktoré kľúč nepotrebujú, nechajte prázdne."
        ),
        "Enabled — its models are offered in chats": (
            "Zapnuté — jeho modely sa ponúkajú v konverzáciách"
        ),
        "Enabled — offered in chats": "Zapnuté — ponúka sa v konverzáciách",
        "Context length": "Dĺžka kontextu",
        "How many tokens this model can hold, filled in from the endpoint where it says. Leave it empty if you do not know: the context percentage and automatic compaction both stay off rather than working from a guess.": (
            "Koľko tokenov tento model zvládne; vypĺňa sa z endpointu tam, kde to "
            "uvádza. Ak to neviete, nechajte prázdne: percento kontextu aj "
            "automatické zhrnutie zostanú vypnuté, namiesto toho, aby pracovali s "
            "odhadom."
        ),
        "Default reasoning effort": "Predvolená náročnosť uvažovania",
        "Facts for other models": "Fakty pre ostatné modely",
        "Never shown to a person. It goes into the list of the other models that a model sees when it is allowed to ask one of them a question, so write what would help it choose: size, what this one is good and bad at, a score you trust. Leave it empty and the description above carries that on its own.": (
            "Človeku sa nikdy nezobrazí. Ide do zoznamu ostatných modelov, ktorý model "
            "vidí, keď sa smie niektorého na niečo spýtať — napíšte teda, čo mu pomôže "
            "vybrať si: veľkosť, v čom je tento dobrý a v čom slabý, skóre, ktorému "
            "veríte. Ak to necháte prázdne, ponesie to opis vyššie sám."
        ),
        "Answering model": "Odpovedajúci model",
        "Model image": "Obrázok modelu",
        "Model that reviews": "Model, ktorý posudzuje",
        "Name the model uses": "Názov, ktorý model používa",
        "Embedding model": "Model pre vnorenia",
        "Export (API)": "Export (API)",
        "GET": "GET",
        "GitHub": "GitHub",
        "Firecrawl": "Firecrawl",
        "ComfyUI": "ComfyUI",
        "ComfyUI API format": "Formát ComfyUI API",
        "Local LM Studio": "Lokálne LM Studio",
        "Checkout": "Checkout",
        "Commit": "Commit",
        "DuckDuckGo region": "Región DuckDuckGo",
        "Change only for a self-hosted Firecrawl.": (
            "Meňte len pri vlastnom Firecrawle."
        ),
        "Accept: application/json": "Accept: application/json",
        "HTTP-Referer: https://example.org": "HTTP-Referer: https://example.org",
        "- Check the weather rather than guessing at it.": (
            "- Počasie si over, nehádaj ho."
        ),
        "- Use the GitHub tools for anything about our repositories.": (
            "- Na čokoľvek o našich repozitároch použi nástroje GitHubu."
        ),
        "Look up the current weather for a city.": "Zisti aktuálne počasie pre mesto.",
        "Arguments": "Argumenty",
        "Extra instructions": "Ďalšie pokyny",
        "Dotted; a number indexes a list. Leave empty for the whole document. A path that leads nowhere gives the whole document rather than nothing.": (
            "S bodkami; číslo indexuje zoznam. Prázdne znamená celý dokument. Cesta, "
            "ktorá nikam nevedie, vráti celý dokument, nie nič."
        ),
        "Ignored for GET. Placeholders are escaped for JSON, so a value cannot end the string it sits in and add a field.": (
            "Pri GET sa ignoruje. Zástupné hodnoty sa pre JSON escapujú, takže hodnota "
            "nemôže ukončiť reťazec, v ktorom je, a pridať ďalšie pole."
        ),
        "Leave the second unticked unless this tool points at something on your own network. It is what stops a tool being aimed at this server, a router, or a cloud metadata endpoint.": (
            "Druhé nechajte nezaškrtnuté, ak tento nástroj nemieri na niečo vo vašej "
            "vlastnej sieti. Práve to zabraňuje tomu, aby nástroj mieril na tento "
            "server, na router alebo na metadátový endpoint cloudu."
        ),
        "May reach private and loopback addresses": (
            "Môže dosiahnuť privátne a loopback adresy"
        ),
        "Allow fetching addresses on this machine and this network": (
            "Povoliť načítanie adries na tomto stroji a v tejto sieti"
        ),
        "Let a model fetch a page itself": "Nechať model načítať stránku sám",
        "Lets a model look things up while it answers. It is offered as a tool the model chooses to call, so nothing changes for a question that does not need it — and it is only offered to models marked as supporting tools, because sending a tool list to one that does not fails the whole request.": (
            "Umožní modelu vyhľadávať, kým odpovedá. Ponúka sa ako nástroj, ktorý si "
            "model sám zvolí, takže pri otázke, ktorá ho nepotrebuje, sa nič nemení — "
            "a ponúka sa len modelom označeným, že podporujú nástroje, pretože "
            "poslanie zoznamu nástrojov modelu, ktorý ich nepodporuje, zhodí celú "
            "požiadavku."
        ),
        "Most characters to keep": "Najviac znakov na uchovanie",
        "Most characters to keep per call": "Najviac znakov na uchovanie za volanie",
        "Characters. Roughly four to a token, so 120,000 is about 30,000 tokens — already most of a small context window. The rest is cut and the model is told so.": (
            "Znaky. Približne štyri na token, takže 120 000 je asi 30 000 tokenov — už "
            "väčšina malého kontextového okna. Zvyšok sa odreže a modelu sa to oznámi."
        ),
        "Every number here is a trade, and a large one usually breaks a request rather than being slow — the text of a whole book does not fit in a context window, and a model handed it fails the request outright rather than reading the first half.": (
            "Každé číslo tu je kompromis a veľká hodnota obyčajne požiadavku zhodí, nie "
            "spomalí — text celej knihy sa do kontextového okna nevojde a model, "
            "ktorému ho podáte, požiadavku odmietne, namiesto toho, aby prečítal prvú "
            "polovicu."
        ),
    }
)

# --- Administration: agents, terminals and background work ---------------------
MESSAGES.update(
    {
        "Allow agent chats": "Povoliť agentské konverzácie",
        "Allow the terminal panel": "Povoliť panel terminálu",
        "Allow messages to be dictated": "Povoliť diktovanie správ",
        "Allow replies to be read out": "Povoliť čítanie odpovedí nahlas",
        "Let a model delegate": "Nechať model delegovať",
        "Let people write their own answer": "Nechať ľudí napísať vlastnú odpoveď",
        "Let scheduled work run": "Nechať bežať naplánovanú prácu",
        "Delegate to a helper": "Delegovať pomocníkovi",
        "Ask another model": "Spýtať sa iného modelu",
        "Open a terminal": "Otvoriť terminál",
        "Connection": "Spojenie",
        "Close a shell after": "Zavrieť shell po",
        "Most shells at once": "Najviac shellov naraz",
        "Most shells per person": "Najviac shellov na osobu",
        "Most a helper may write": "Najviac, čo smie napísať pomocník",
        "Most helpers one reply may send": "Najviac pomocníkov, ktorých smie odpoveď poslať",
        "Longest a helper may take": "Najdlhšie, čo smie trvať pomocník",
        "How long a question waits": "Ako dlho otázka čaká",
        "Keep a helper's own chat afterwards": "Nechať potom vlastnú konverzáciu pomocníka",
        "Each is a whole generation against the same endpoint the reply that asked for it is waiting on. Past this a model is told to do the work itself rather than made to wait.": (
            "Každý je celá generácia proti tomu istému endpointu, na ktorý čaká "
            "odpoveď, ktorá si ho vyžiadala. Nad týmto sa modelu povie, aby prácu "
            "urobil sám, namiesto toho, aby čakal."
        ),
        "Fanning out across a handful of independent questions is what this is for. A reply that wants twenty has misread the tool. Questions put to other models count against this same number, so one reply cannot spend the allowance twice.": (
            "Je to na rozvetvenie do niekoľkých nezávislých otázok. Odpoveď, ktorá "
            "chce dvadsať, nástroj nepochopila. Otázky položené iným modelom sa "
            "počítajú do toho istého čísla, takže jedna odpoveď nemôže limit "
            "spotrebovať dvakrát."
        ),
        "In tokens, across every round. A helper answers one question, so this should run out well before the reply that asked does. Zero means no ceiling.": (
            "V tokenoch, cez všetky kolá. Pomocník odpovedá na jednu otázku, takže "
            "toto by malo skončiť výrazne skôr než odpoveď, ktorá sa pýtala. Nula "
            "znamená bez stropu."
        ),
        "Every restart ends every open terminal session — a command still running on the far side is cut off. A reply being written is saved with whatever it has.": (
            "Každý restart ukončí každé otvorené sedenie terminálu — príkaz, ktorý na "
            "druhej strane stále beží, sa odstrihne. Odpoveď, ktorá sa práve píše, sa "
            "uloží s tým, čo má."
        ),
        "Off means it is deleted once its answer has been handed over, which is what keeps this cheap to use. Turn it on to work out why one came back with something odd. Kept chats are temporary either way and are swept a day later, and neither appears in anybody's sidebar.": (
            "Vypnuté znamená, že sa po odovzdaní odpovede zmaže, a práve preto je toto "
            "lacné na používanie. Zapnite, keď chcete zistiť, prečo sa niektorý vrátil "
            "s niečím čudným. Ponechané konverzácie sú aj tak dočasné a o deň neskôr "
            "sa zmetú, a ani jedna sa nikomu nezobrazí v bočnom paneli."
        ),
        "Check for due work every": "Kontrolovať splatnú prácu každých",
        "How often it looks": "Ako často sa pozerá",
        "Nothing may repeat faster than": "Nič sa nesmie opakovať častejšie než",
        "A schedule that comes round faster than its chat can answer would build a backlog for ever. Past this, a run is skipped and says so on the schedule rather than joining the queue.": (
            "Plán, ktorý sa vracia rýchlejšie, než jeho konverzácia dokáže odpovedať, "
            "by donekonečna hromadil nevybavené. Nad týmto sa beh preskočí a v pláne "
            "sa to uvedie, namiesto toho, aby sa pridal do radu."
        ),
        "Fifty schedules due at nine o'clock must not open fifty replies against one endpoint. The rest wait their turn rather than being dropped.": (
            "Päťdesiat plánov splatných o deviatej nesmie otvoriť päťdesiat odpovedí "
            "proti jednému endpointu. Ostatné počkajú, kým na ne príde rad, "
            "nezahodia sa."
        ),
        "Counted from the first of the month, UTC. Recorded for every reply including one that was stopped or failed — an endpoint charges for tokens it generated whether or not anybody wanted them.": (
            "Počíta sa od prvého dňa mesiaca v UTC. Zapisuje sa pri každej odpovedi "
            "vrátane zastavenej alebo neúspešnej — endpoint si účtuje tokeny, ktoré "
            "vygeneroval, či ich niekto chcel alebo nie."
        ),
        "Empty means a chat that can see everything.": (
            "Prázdne znamená konverzáciu, ktorá vidí všetko."
        ),
        "Knowledge bases in scope": "Znalostné bázy v rozsahu",
        "Permissions this group adds": "Oprávnenia, ktoré táto skupina pridáva",
        "Password and removal": "Heslo a odstránenie",
        "None. They cannot start a chat at all.": "Žiadne. Nemôžu vôbec začať konverzáciu.",
        "None": "Žiadne",
        "Nothing matches that filter.": "Tomuto filtru nič nezodpovedá.",
        "No suggestions": "Žiadne návrhy",
        "Order on the screen, lowest first.": "Poradie na obrazovke, najnižšie prvé.",
        "One line under the name, saying what it is for.": (
            "Jeden riadok pod názvom, ktorý hovorí, na čo to je."
        ),
        "One per chat. Each keeps a shell open on the linked device, fed over its link.": (
            "Jeden na konverzáciu. Každý udržiava otvorený shell na prepojenom zariadení, napojený cez jeho spojenie."
        ),
    }
)

# --- Administration: prompts, themes, users and saving -------------------------
MESSAGES.update(
    {
        "Save": "Uložiť",
        "Save changes": "Uložiť zmeny",
        "Save settings": "Uložiť nastavenia",
        "Save group": "Uložiť skupinu",
        "Save baseline": "Uložiť základ",
        "Save search": "Uložiť hľadanie",
        "Save extraction": "Uložiť extrakciu",
        "Saved.": "Uložené.",
        "Settings saved.": "Nastavenia uložené.",
        "Prompts saved.": "Pokyny uložené.",
        "Audio settings saved.": "Nastavenia zvuku uložené.",
        "Search settings saved.": "Nastavenia hľadania uložené.",
        "Restore": "Obnoviť",
        "Restore defaults": "Obnoviť predvolené",
        "Restore all defaults": "Obnoviť všetky predvolené",
        "Reset password": "Obnoviť heslo",
        "Remove": "Odstrániť",
        "Remove image": "Odstrániť obrázok",
        "Restrict": "Obmedziť",
        "Select all": "Vybrať všetko",
        "Select every model on this page": "Vybrať každý model na tejto stránke",
        "Set default": "Nastaviť ako predvolené",
        "Show this one": "Zobraziť tento",
        "Switch": "Zapnutie",
        "Switch theme": "Prepnúť vzhľad",
        "Send": "Poslať",
        "Try it": "Vyskúšať",
        "Preview": "Náhľad",
        "Previous": "Predchádzajúce",
        "Position": "Pozícia",
        "Presentation": "Zobrazenie",
        "Pinned — shortcut in the chat sidebar": "Pripnuté — skratka v bočnom paneli",
        "Prompt": "Pokyn",
        "Prompts": "Pokyny",
        "Prompt groups": "Skupiny pokynov",
        "System prompt": "Systémový pokyn",
        "Preamble character cap": "Limit znakov preambuly",
        "Suggestions": "Návrhy",
        "Role": "Rola",
        "Quotas": "Kvóty",
        "Tokens": "Tokeny",
        "Tokens this month": "Tokeny tento mesiac",
        "This month": "Tento mesiac",
        "Replies": "Odpovede",
        "Running": "Beží",
        "Runs at once": "Behov naraz",
        "Running at once, instance-wide": "Beží naraz, v celej instancii",
        "Schedule work": "Naplánovať prácu",
        "Scheduling": "Plánovanie",
        "Scheduler": "Plánovač",
        "Scheduled": "Naplánované",
        "Schedules per person": "Plánov na osobu",
        "Run commands": "Spúšťať príkazy",
        "Tool calls in an ordinary chat": "Volania nástrojov v obyčajnej konverzácii",
        "Turns that may pile up in one chat": "Kroky, ktoré sa môžu nakopiť v jednej konverzácii",
        "Tools": "Nástroje",
        "Tools it offers": "Nástroje, ktoré ponúka",
        "Tools offered": "Ponúkané nástroje",
        "Tool descriptions": "Opisy nástrojov",
        "Search tools": "Hľadať nástroje",
        "Search tools…": "Hľadať nástroje…",
        "Search models": "Hľadať modely",
        "Search models…": "Hľadať modely…",
        "Search users": "Hľadať používateľov",
        "Search by name or email…": "Hľadať podľa mena alebo e-mailu…",
        "Provider": "Poskytovateľ",
        "Results per search": "Výsledkov na hľadanie",
        "Safe search": "Bezpečné hľadanie",
        "SearXNG": "SearXNG",
        "Searching by meaning": "Hľadanie podľa významu",
        "Secret": "Tajný údaj",
        "Speed": "Rýchlosť",
        "Steps": "Kroky",
        "Sampler": "Vzorkovač",
        "Reasoning": "Uvažovanie",
        "Reasoning efforts this model accepts": "Náročnosti uvažovania, ktoré tento model prijíma",
        "Required.": "Povinné.",
        "Timeout (seconds)": "Časový limit (sekundy)",
        "The answer": "Odpoveď",
        "The call": "Volanie",
        "The index": "Index",
        "The modes": "Režimy",
        "The server": "Server",
        "The terminal": "Terminál",
        "The workflow": "Pracovný postup",
        "The placeholders": "Zástupné hodnoty",
        "The first one": "Prvý",
        "The first in the list above": "Prvý v zozname vyššie",
        "This chat is": "Táto konverzácia je",
        "This chat's own working document": "Vlastný pracovný dokument tejto konverzácie",
        "This is what the model would read back:": "Toto by si model prečítal:",
        "Text kept per file": "Text uchovaný na súbor",
        "The heading on the card.": "Nadpis na karte.",
        "Shown to you, in the list.": "Zobrazuje sa vám v zozname.",
        "Shown instead of the raw id. Empty uses the id.": (
            "Zobrazuje sa namiesto surového id. Prázdne použije id."
        ),
        "Shown in the transcript when the model uses it.": (
            "Zobrazí sa v zápise, keď to model použije."
        ),
        "Sent as the first message when the card is clicked.": (
            "Pošle sa ako prvá správa po kliknutí na kartu."
        ),
        "Sent as the first message. Nothing is added to it, so a prompt that needs material should ask for it.": (
            "Pošle sa ako prvá správa. Nič sa k nej nepridáva, takže pokyn, ktorý "
            "potrebuje podklady, si ich má vyžiadať."
        ),
        "Sent on its own after the first reply, not as part of any conversation.": (
            "Pošle sa samostatne po prvej odpovedi, nie ako súčasť konverzácie."
        ),
        "Sent to the model verbatim. This is the whole basis on which it decides whether to call this tool, so say what it does and when it is the right thing to use.": (
            "Posiela sa modelu slovo za slovom. Je to celý základ, na ktorom sa "
            "rozhoduje, či tento nástroj zavolá — napíšte teda, čo robí a kedy je to "
            "správna voľba."
        ),
        "Set per chat and switchable at any time. This is what each one means; the two lists below adjust them.": (
            "Nastavuje sa pre každú konverzáciu a dá sa kedykoľvek prepnúť. Toto je "
            "význam každého z nich; dva zoznamy nižšie ich upravujú."
        ),
        "Signs them out everywhere. An administrator resetting a password usually means the account is compromised or the person has gone.": (
            "Odhlási ich všade. Keď správca obnovuje heslo, obyčajne to znamená, že je "
            "účet napadnutý alebo že daný človek odišiel."
        ),
        "Turning this off signs them out everywhere at once, rather than waiting for a cookie to expire.": (
            "Vypnutím ich odhlásite všade naraz, namiesto čakania na vypršanie cookie."
        ),
        "Their chats, folders and library go too, and every share naming them or naming anything of theirs.": (
            "Zmiznú aj ich konverzácie, zložky a knižnica a každé zdieľanie, ktoré "
            "menuje ich alebo čokoľvek ich."
        ),
        "The one place membership is edited. A user's own page links here rather than offering a second control for the same value.": (
            "Jediné miesto, kde sa upravuje členstvo. Stránka používateľa sem odkazuje, "
            "namiesto toho, aby pre tú istú hodnotu ponúkala druhý ovládací prvok."
        ),
        "The whole system message, assembled from what is in the boxes below — including changes you have not saved yet. Your own memories and skills are used, because a preview against invented ones cannot tell you whether it reads well against what is actually there.": (
            "Celá systémová správa, zložená z toho, čo je v poliach nižšie — vrátane "
            "zmien, ktoré ste ešte neuložili. Použijú sa vaše vlastné pamäti a "
            "schopnosti, pretože náhľad proti vymysleným vám nepovie, či to dobre "
            "sedí oproti tomu, čo tam naozaj je."
        ),
        "Defined in code — part of the schema sent to the endpoint alongside the prompt, not guidance layered on top of it. They are statements of fact about what each tool does, so they change when the tool does; editing them here would let the text quietly become a lie. A custom tool's description will be editable, because a custom tool is a row rather than a function.": (
            "Definované v kóde — sú súčasťou schémy, ktorá ide endpointu spolu s "
            "pokynom, nie vedenie navrstvené nad ňou. Sú to tvrdenia o tom, čo každý "
            "nástroj robí, takže sa menia, keď sa mení nástroj; ich úprava tu by "
            "nechala text potichu sa stať lžou. Opis vlastného nástroja sa upravovať "
            "dá, pretože vlastný nástroj je záznam, nie funkcia."
        ),
        "Put every prompt back to its built-in wording? Everything you have edited here is lost.": (
            "Vrátiť každý pokyn na jeho zabudovanú formuláciu? Všetko, čo ste tu "
            "upravili, sa stratí."
        ),
        "Refused at the point of creation, with the reason. Existing schedules over a lowered limit keep running; only new ones are refused.": (
            "Odmietne sa pri vytvorení, s dôvodom. Existujúce plány nad zníženým "
            "limitom bežia ďalej; odmietajú sa len nové."
        ),
        "Seconds. A floor on how often one schedule may come round. Raise it if people are setting things to run more often than the work takes.": (
            "Sekundy. Spodná hranica toho, ako často sa jeden plán môže vrátiť. Zvýšte "
            "ju, ak si ľudia nastavujú veci častejšie, než práca trvá."
        ),
        "Seconds. After this the reply carries on without an answer and says so. At least a minute, whatever is typed here.": (
            "Sekundy. Po tomto čase odpoveď pokračuje bez odpovede a povie to. Najmenej "
            "minúta, čokoľvek sem napíšete."
        ),
        "Seconds. This is how late a run can be, not how often anything happens: the finest a schedule can be set to is one minute, so anything under that buys nothing. One indexed query per tick.": (
            "Sekundy. Je to o tom, ako neskoro beh môže byť, nie ako často sa niečo "
            "deje: najmenšia jednotka, na akú sa plán dá nastaviť, je jedna minúta, "
            "takže čokoľvek pod ňou nič neprinesie. Jedna indexovaná otázka na tik."
        ),
        "Spent out of the context window on every call. The rest is cut off, and the model is told so.": (
            "Spotrebuje sa z kontextového okna pri každom volaní. Zvyšok sa odreže a "
            "modelu sa to oznámi."
        ),
        "Read new replies aloud as they finish, by default": (
            "Predvolene čítať nové odpovede nahlas, keď dopíšu"
        ),
        "Read the response as": "Prečítať odpoveď ako",
        "Only the starting value for each account — anyone can turn it off in their own settings, and nobody is made to listen.": (
            "Len počiatočná hodnota pre každý účet — každý si to môže vo vlastných "
            "nastaveniach vypnúť a nikoho nikto počúvať nenúti."
        ),
        "Only used when SearXNG is the chosen provider. Your own instance, so no third party sees the queries.": (
            "Používa sa len vtedy, keď je zvoleným poskytovateľom SearXNG. Vaša vlastná "
            "instancia, takže dotazy nevidí tretia strana."
        ),
        "Adds a microphone to the composer. Recordings are sent to this endpoint and never written to disk.": (
            "Pridá do písania mikrofón. Nahrávky sa posielajú na tento endpoint a nikdy "
            "sa nezapisujú na disk."
        ),
        "Adds a speaker button to every reply. Each reader can pick their own voice in their settings; what is chosen here is the default.": (
            "Pridá ku každej odpovedi tlačidlo prehrávania. Každý si vo svojich "
            "nastaveniach môže vybrať vlastný hlas; čo je vybrané tu, je predvolené."
        ),
        "Default voice": "Predvolený hlas",
        "Summarise a document": "Zhrnúť dokument",
        "Saving links": "Ukladanie odkazov",
        "Applying an update": "Aplikuje sa aktualizácia",
        "An update has been requested and is waiting for the helper to pick it up. The service restarts when it does.": (
            "Aktualizácia bola vyžiadaná a čaká, kým si ju pomocník prevezme. Služba sa "
            "potom restartuje."
        ),
        "This checkout has uncommitted changes, and updating discards them. Nothing here is meant to be edited in place, so this usually means somebody was debugging on the box.": (
            "Tento checkout má nezapísané zmeny a aktualizácia ich zahodí. Nič tu nie je "
            "určené na úpravu na mieste, takže to obyčajne znamená, že niekto na "
            "stroji ladil."
        ),
        "This was not installed from a git checkout — a container image, or a wheel — so there is nothing here to compare or update. Pull a new image instead.": (
            "Toto nebolo nainštalované z git checkoutu — je to obraz kontejnera alebo "
            "wheel — takže tu niet čo porovnávať ani aktualizovať. Stiahnite namiesto "
            "toho nový obraz."
        ),
    }
)

# --- Administration: images, extraction, MCP and updates -----------------------
MESSAGES.update(
    {
        "URL": "URL",
        "Unload URL": "URL na uvoľnenie",
        "No unload call": "Bez volania na uvoľnenie",
        "POST": "POST",
        "Parameters": "Parametre",
        "Variables": "Premenné",
        "Version": "Verzia",
        "Updates": "Aktualizácie",
        "Users": "Používatelia",
        "Workflows": "Pracovné postupy",
        "Weather": "Počasie",
        "Web search": "Vyhľadávanie na webe",
        "Width": "Šírka",
        "Height": "Výška",
        "Overlap": "Presah",
        "Piece size": "Veľkosť časti",
        "Pieces per request": "Častí na požiadavku",
        "Denoise": "Odšumenie",
        "Guidance (cfg)": "Vedenie (cfg)",
        "Negative prompt": "Negatívny pokyn",
        "Checkpoint": "Checkpoint",
        "Checkpoints": "Checkpointy",
        "Available checkpoints": "Dostupné checkpointy",
        "Default workflow": "Predvolený pracovný postup",
        "Offer this workflow": "Ponúkať tento postup",
        "Images per run": "Obrázkov na beh",
        "Attempts per image": "Pokusov na obrázok",
        "JPEG quality": "Kvalita JPEG",
        "Longest image edge": "Najdlhšia hrana obrázka",
        "Largest upload": "Najväčší súbor",
        "Keep abandoned uploads for": "Uchovať opustené súbory",
        "Pages read from a PDF": "Strán prečítaných z PDF",
        "Path into the JSON": "Cesta do JSON",
        "Also treat as text": "Považovať za text aj",
        "Checking the result": "Kontrola výsledku",
        "Use default": "Použiť predvolené",
        "With selected:": "S vybranými:",
        "Withdraw the request": "Zrušiť požiadavku",
        "SDXL, photographic": "SDXL, fotografický",
        "What it is": "Čo to je",
        "What it is for": "Na čo to je",
        "What it is for, in one line.": "Na čo to je, v jednom riadku.",
        "What it may spend": "Čo smie spotrebovať",
        "What this account can do": "Čo tento účet smie",
        "What a file may cost": "Čo smie stáť jeden súbor",
        "What a generation uses by default": "Čo generovanie predvolene používa",
        "What is this model good at?": "V čom je tento model dobrý?",
        "Parameters, quantisation, a benchmark figure, what it is bad at": (
            "Parametre, kvantizácia, číslo z benchmarku, v čom je slabý"
        ),
        "Offer web search to models that support tools": (
            "Ponúkať vyhľadávanie modelom, ktoré podporujú nástroje"
        ),
        "Offer image generation to models that support tools": (
            "Ponúkať generovanie obrázkov modelom, ktoré podporujú nástroje"
        ),
        "Look at each image before showing it, and try again if it is wrong": (
            "Pozrieť sa na každý obrázok pred zobrazením a pri chybe to skúsiť znova"
        ),
        "Refuse a PDF whose text cannot be read": "Odmietnuť PDF, ktorého text sa nedá prečítať",
        "Preserve VRAM: unload the language model while ComfyUI works": (
            "Šetriť VRAM: uvoľniť jazykový model, kým pracuje ComfyUI"
        ),
        "The chat's own model, when it has vision": (
            "Vlastný model konverzácie, ak vidí obrázky"
        ),
        "Only models marked as having vision are listed. If there is nothing to ask, the first image is kept and nothing fails.": (
            "Vypisujú sa len modely označené, že vidia obrázky. Ak nie je koho sa "
            "spýtať, zachová sa prvý obrázok a nič nezlyhá."
        ),
        "A vision model is shown the picture and the request it came from, and says keep or retry. Only clearly wrong images are retried — a missing subject, a mangled picture — because taste is not a fault and the next attempt is not promised to be better. The reader sees only the image that was kept.": (
            "Modelu, ktorý vidí obrázky, sa ukáže obrázok aj požiadavka, z ktorej "
            "vznikol, a on povie zachovať alebo skúsiť znova. Znova sa skúšajú len "
            "jasne pokazené obrázky — chýbajúci motív, zdeformovaná kresba — pretože "
            "vkus nie je chyba a ďalší pokus nemá zaručené, že bude lepší. Čitateľ "
            "vidí len ten obrázok, ktorý sa zachoval."
        ),
        "Including the first. The last attempt is kept whatever the review says, so a request always produces a picture.": (
            "Vrátane prvého. Posledný pokus sa zachová bez ohľadu na posudok, takže "
            "požiadavka vždy vytvorí obrázok."
        ),
        "Lets a model draw a picture and show it in the conversation, on a ComfyUI you are running. It is offered as a tool the model chooses to call, so nothing changes for a conversation that never asks for one — and it is only offered once there is a ComfyUI, a workflow and at least one checkpoint, because a tool that fails on its first call is worse than a tool nobody was given.": (
            "Umožní modelu nakresliť obrázok a zobraziť ho v konverzácii, na ComfyUI, "
            "ktoré prevádzkujete. Ponúka sa ako nástroj, ktorý si model sám zvolí, "
            "takže pri konverzácii, ktorá o obrázok nikdy nepožiada, sa nič nemení — "
            "a ponúka sa až vtedy, keď existuje ComfyUI, pracovný postup a aspoň jeden "
            "checkpoint, pretože nástroj, ktorý zlyhá pri prvom volaní, je horší než "
            "nástroj, ktorý nikto nedostal."
        ),
        "For a machine that cannot hold both at once. Before generating, the chat's own connection is asked to unload — set an unload URL on it under Connections, or nothing happens. Afterwards ComfyUI is asked to free its own models, and the language model loads again by itself on the next request.": (
            "Pre stroj, ktorý neudrží oba naraz. Pred generovaním sa vlastné spojenie "
            "konverzácie požiada o uvoľnenie — nastavte mu URL na uvoľnenie v "
            "Spojeniach, inak sa nestane nič. Potom sa ComfyUI požiada, aby uvoľnilo "
            "svoje modely, a jazykový model sa pri ďalšej požiadavke nahrá sám."
        ),
        "How long to wait for one image, queue included. Far longer than any other timeout here, because the thing being waited for genuinely takes that long.": (
            "Ako dlho čakať na jeden obrázok, vrátane radu. Oveľa dlhšie než "
            "akýkoľvek iný limit tu, pretože to, na čo sa čaká, naozaj tak dlho trvá."
        ),
        "What every picture is drawn with unless the model names otherwise. Leave a box empty to use the built-in value shown beside it — the built-ins are SD1.5-era, and 512×512 on an SDXL checkpoint is what produces the duplicated limbs.": (
            "S čím sa kreslí každý obrázok, ak model neuvedie inak. Prázdne pole "
            "použije zabudovanú hodnotu uvedenú vedľa — zabudované sú z éry SD1.5 a "
            "512×512 na SDXL checkpointe je presne to, čo vytvára zdvojené končatiny."
        ),
        "One filename per line, exactly as ComfyUI spells it. This is the list the model chooses from, so leaving out a checkpoint is how you stop it being used. Press Test below to read them off ComfyUI — that fills this in the first time and never overwrites it afterwards.": (
            "Jeden názov súboru na riadok, presne ako ho píše ComfyUI. Toto je zoznam, "
            "z ktorého model vyberá, takže vynechanie checkpointu je spôsob, ako "
            "zabrániť jeho použitiu. Stlačením Overiť nižšie sa prečítajú z ComfyUI — "
            "prvý raz to toto pole vyplní a potom ho už nikdy neprepíše."
        ),
        "Where ComfyUI is listening. An address on this machine or this network is fine here and is not checked against the request-forgery rules — you typed it, unlike an address a model asks for.": (
            "Kde ComfyUI počúva. Adresa na tomto stroji alebo v tejto sieti je tu v "
            "poriadku a neporovnáva sa s pravidlami proti falšovaniu požiadaviek — "
            "napísali ste ju vy, na rozdiel od adresy, ktorú si vyžiada model."
        ),
        "Lowercase letters, digits, hyphens and underscores. This is what the model writes when it picks this workflow.": (
            "Malé písmená, číslice, spojovníky a podčiarkovníky. Toto model napíše, keď "
            "si tento postup vyberie."
        ),
        "Used when the model names no template and the chat has no preference.": (
            "Použije sa, keď model neuvedie šablónu a konverzácia nemá preferenciu."
        ),
        "Used when the model does not write one of its own. It writes one often, so this is a floor rather than something always applied — “always add these words” belongs in the instructions below, where the model is told to include them.": (
            "Použije sa, keď si model nenapíše vlastný. Píše si ho často, takže je to "
            "skôr spodná hranica než niečo, čo sa použije vždy — „vždy pridaj tieto "
            "slová“ patrí do pokynov nižšie, kde sa modelu povie, aby ich zahrnul."
        ),
        "Used by chats on this model that have no prompt of their own. A chat's own prompt overrides this; this overrides the instance prompt.": (
            "Použijú ho konverzácie na tomto modeli, ktoré nemajú vlastný pokyn. Vlastný "
            "pokyn konverzácie toto prebíja; toto prebíja pokyn instancie."
        ),
        "Prefer the SDXL template for anything photographic.": (
            "Na čokoľvek fotografické použi šablónu SDXL."
        ),
        "Photographic and slow. Best for people, interiors and product shots at 1024px.": (
            "Fotografický a pomalý. Najlepší na ľudí, interiéry a produktové zábery "
            "pri 1024 px."
        ),
        "Pixels. Images are re-encoded before they are sent, because a phone photo is several megabytes of base64.": (
            "Pixely. Obrázky sa pred poslaním prekódujú, pretože fotka z telefónu je "
            "niekoľko megabajtov base64."
        ),
        "Megabytes, before anything is done to it.": "Megabajty, pred akoukoľvek úpravou.",
        "Hours. A file chosen in the composer and never sent. Swept at startup.": (
            "Hodiny. Súbor vybraný pri písaní a nikdy neposlaný. Zmetá sa pri štarte."
        ),
        "PNG, JPEG, WEBP or GIF, under 2 MB. Without one, the model gets a generated badge whose colour is derived from its id.": (
            "PNG, JPEG, WEBP alebo GIF, do 2 MB. Bez neho model dostane vygenerovaný "
            "odznak, ktorého farba sa odvodí z jeho id."
        ),
        "Extraction is slow and happens once, at upload. Beyond this the file is still stored; only its text stops.": (
            "Extrakcia je pomalá a stane sa raz, pri nahraní. Nad týmto sa súbor "
            "stále uloží; prestane len jeho text."
        ),
        "Off, a scanned PDF is stored with an explanation saying why it contributes nothing — there is no OCR here. That is usually what somebody wants: the file is still attached and still downloadable.": (
            "Vypnuté: naskenované PDF sa uloží s vysvetlením, prečo neprináša nič — "
            "OCR tu nie je. Obyčajne je to presne to, čo niekto chce: súbor je aj tak "
            "priložený a aj tak sa dá stiahnuť."
        ),
        "Characters. A record is split on paragraph boundaries into pieces of about this size, and each is embedded separately — a document is found by its best piece, not by its average.": (
            "Znaky. Záznam sa na hraniciach odstavcov rozdelí na časti približne tejto "
            "veľkosti a každá sa vnorí samostatne — dokument sa nájde podľa svojej "
            "najlepšej časti, nie podľa priemeru."
        ),
        "How much of each piece is repeated at the start of the next, so a sentence across a boundary is whole somewhere. Capped at half the piece size.": (
            "Koľko z každej časti sa zopakuje na začiatku ďalšej, aby veta na hranici "
            "bola niekde celá. Najviac polovica veľkosti časti."
        ),
        "Lower this if the endpoint refuses large requests; raise it if a rebuild is slow and the far side has room.": (
            "Znížte, ak endpoint odmieta veľké požiadavky; zvýšte, ak je "
            "prestavba pomalá a druhá strana má priestor."
        ),
        "Documents, notes, skills and reports are indexed as they are written. A rebuild is for everything that already existed — or for after changing the model or the piece size, both of which make what is stored stop meaning anything. It runs in the background and can be left.": (
            "Dokumenty, poznámky, schopnosti a správy sa indexujú pri zápise. Prestavba "
            "je pre všetko, čo už existovalo — alebo po zmene modelu či veľkosti "
            "častí, po ktorej to, čo je uložené, prestane čokoľvek znamenať. Beží na "
            "pozadí a dá sa nechať."
        ),
        "Placeholders are filled from the arguments and escaped, so a value cannot add a path segment or a query of its own. The scheme and host must be written out — they cannot come from an argument.": (
            "Zástupné hodnoty sa vyplnia z argumentov a escapujú, takže hodnota nemôže "
            "pridať vlastný segment cesty ani vlastný dotaz. Schéma a hostiteľ musia "
            "byť napísané — nemôžu prísť z argumentu."
        ),
        "A server's tool names and descriptions are sent to the model as instructions, and what it returns is read back as fact. Add servers you trust, the way you would a dependency.": (
            "Názvy a opisy nástrojov servera idú modelu ako pokyny a to, čo vráti, sa "
            "čita ako fakt. Pridávajte servery, ktorým veríte, tak ako by ste pridávali "
            "závislosť."
        ),
        "The streamable-HTTP endpoint itself, the one that accepts a POST. A server that answers with a redirect to somewhere else will be refused.": (
            "Samotný streamable-HTTP endpoint, ten, ktorý prijíma POST. Server, ktorý "
            "odpovie presmerovaním niekam inam, bude odmietnutý."
        ),
        "Discovered at the last refresh. Untick one to withhold it — a tool this server adds later is offered by default.": (
            "Zistené pri poslednom obnovení. Odškrtnutím ho zadržíte — nástroj, ktorý "
            "tento server pridá neskôr, sa predvolene ponúka."
        ),
        "No servers yet. You will need the URL of an MCP endpoint that speaks streamable HTTP — local ones launched as a subprocess are not supported.": (
            "Zatiaľ žiadne servery. Budete potrebovať URL endpointu MCP, ktorý hovorí "
            "streamable HTTP — lokálne, spúšťané ako podproces, nie sú podporované."
        ),
        "No workflows yet. One is needed before anything can be drawn; the default one is filled in for you when you add the first.": (
            "Zatiaľ žiadne postupy. Jeden je potrebný, aby sa dalo niečo nakresliť; "
            "predvolený sa vyplní sám, keď pridáte prvý."
        ),
        "Nothing configured yet. Add a connection above and its models appear here.": (
            "Zatiaľ nič nastavené. Pridajte spojenie vyššie a jeho modely sa tu zobrazia."
        ),
        "Nothing known about the remote yet. Check it above.": (
            "O vzdialenej strane sa zatiaľ nič nevie. Overte ju vyššie."
        ),
        "None — keyword search only": "Žiadny — len hľadanie podľa slov",
        "None — send nothing": "Žiadne — neposielať nič",
        "The new-chat screen shows its empty state instead.": (
            "Obrazovka novej konverzácie namiesto toho zobrazí svoj prázdny stav."
        ),
        "When a model asks a question it can offer answers to pick from, and by default a box to write something else. Turn this off if you would rather nobody typed free text into a prompt a model composed.": (
            "Keď sa model na niečo pýta, môže ponúknuť odpovede na výber a predvolene "
            "aj pole na napísanie niečoho iného. Vypnite, ak nechcete, aby niekto "
            "písal voľný text do pokynu, ktorý zložil model."
        ),
        "What every signed-in user can do before any group is considered. Turn something off here and grant it through a group to make it opt-in.": (
            "Čo smie každý prihlásený používateľ predtým, než sa berie do úvahy "
            "akákoľvek skupina. Vypnite tu niečo a udeľte to cez skupinu, aby to bolo "
            "na prihlásenie."
        ),
        "What happens to a file between the upload and the model, and how anything is found again afterwards. The two are the same pipeline: what is extracted decides what there is to search.": (
            "Čo sa so súborom stane medzi nahraním a modelom a ako sa potom čokoľvek "
            "znova nájde. Sú to tie isté koľaje: čo sa vytiahne, rozhoduje o tom, v "
            "čom sa dá hľadať."
        ),
        "What the interface is in for anybody who has not chosen for themselves. Everyone can pick their own under Appearance in their settings, and that choice wins here.": (
            "V akom jazyku je rozhranie pre každého, kto si nevybral sám. Každý si môže "
            "vybrať vlastný v nastaveniach pod Vzhľad a tá voľba tu vyhráva."
        ),
        "Two endpoints speaking the OpenAI audio API: one that turns speech into text so a message can be dictated, one that reads a reply out. They are configured separately because they usually are separate servers — whisper.cpp and Kokoro, say, or Speaches for both.": (
            "Dva endpointy hovoriace zvukovým API OpenAI: jeden premieňa reč na text, "
            "aby sa dala správa nadiktovať, druhý odpoveď prečíta. Nastavujú sa "
            "oddelene, pretože to obyčajne sú oddelené servery — napríklad whisper.cpp "
            "a Kokoro, alebo Speaches na oboje."
        ),
        "Uncheck to restrict this model to chosen groups. Administrators always have access.": (
            "Odškrtnutím obmedzíte tento model na vybrané skupiny. Správcovia majú "
            "prístup vždy."
        ),
        "Uncheck to restrict this tool to chosen groups. Administrators always have access.": (
            "Odškrtnutím obmedzíte tento nástroj na vybrané skupiny. Správcovia majú "
            "prístup vždy."
        ),
        "Without going through registration — useful when sign-up is closed.": (
            "Bez prechodu registráciou — užitočné, keď je registrácia zatvorená."
        ),
        "You are a helpful assistant.": "Si užitočný asistent.",
    }
)

# --- Small words and status labels ---------------------------------------------
# The technical ones map to themselves on purpose: a model id, a parameter name, a
# URL or an effort level is sent to an endpoint or typed by a person, and
# translating it would either break the request or describe something that is not
# what the field contains.
MESSAGES.update(
    {
        "admin": "správca",
        "already in the baseline": "už v základe",
        "and": "a",
        "below the cut": "pod hranicou",
        "default": "predvolené",
        "detect": "zistiť",
        "disabled": "vypnuté",
        "edited": "upravené",
        "empty": "prázdne",
        "hidden": "skryté",
        "last call failed": "posledné volanie zlyhalo",
        "mallorn tree": "mallornový strom",
        "needs tools": "potrebuje nástroje",
        "no opinion": "bez názoru",
        "no setup": "bez nastavenia",
        "not granted": "neudelené",
        "nothing released yet": "zatiaľ nič vydané",
        "off": "vypnuté",
        "on": "zapnuté",
        "only in the title request": "len v požiadavke na názov",
        "pending": "čaká",
        "pinned": "pripnuté",
        "private network": "privátna sieť",
        "quotas": "kvóty",
        "restricted": "obmedzené",
        "stopped": "zastavené",
        "uncommitted changes": "nezapísané zmeny",
        "unknown": "neznáme",
        "up to date": "aktuálne",
        "with tools": "s nástrojmi",
        "you": "vy",
        "model": "model",
        # Sent to an endpoint or typed verbatim: not prose.
        "embeddings": "embeddings",
        "euler (built-in)": "euler (zabudovaný)",
        "normal (built-in)": "normal (zabudovaný)",
        "text, watermark": "text, watermark",
        "sd_xl_base_1.0.safetensors": "sd_xl_base_1.0.safetensors",
        "sdxl-photo": "sdxl-photo",
        "data.items.0.title": "data.items.0.title",
        "github": "github",
        "weather": "weather",
        "wt-wt": "wt-wt",
        "sk-…": "sk-…",
        "tts-1": "tts-1",
        "whisper-1": "whisper-1",
        "http://127.0.0.1:8081": "http://127.0.0.1:8081",
        "http://127.0.0.1:8188": "http://127.0.0.1:8188",
        "http://127.0.0.1:8880": "http://127.0.0.1:8880",
        "http://127.0.0.1:8888": "http://127.0.0.1:8888",
        "http://localhost:1234/v1": "http://localhost:1234/v1",
        "https://api.firecrawl.dev": "https://api.firecrawl.dev",
        "https://mcp.example.com/mcp": "https://mcp.example.com/mcp",
    }
)

# --- Data groups (1.10.0) -------------------------------------------------------
MESSAGES.update(
    {
        'Data group': 'Dátová oblasť',
        'Data groups': 'Dátové oblasti',
        'Data': 'Dáta',
        "Its models read only this group's memories, notes, skills, knowledge and reports, and a chat started on one of them stays in it. Moving a connection does not move any data: it starts reading the other group.": 'Jeho modely čítajú len pamäť, poznámky, schopnosti, znalosti a správy tejto oblasti a konverzácia začatá na niektorom z nich v nej zostáva. Presunutie spojenia nepresúva žiadne dáta: začne čítať druhú oblasť.',
        'All data groups': 'Všetky dátové oblasti',
        'A personal group of %(who)s.': 'Osobná oblasť používateľa %(who)s.',
        'Connections in this group': 'Spojenia v tejto oblasti',
        "Their models read this group's data and nothing else. A connection taken out of a group goes back to the default one. Moving a connection moves no data: it starts reading the other group's.": 'Ich modely čítajú dáta tejto oblasti a nič iné. Spojenie vybraté z oblasti sa vráti do predvolenej. Presunutie spojenia nepresúva žiadne dáta: začne čítať dáta druhej oblasti.',
        'There are no connections yet.': 'Zatiaľ tu nie sú žiadne spojenia.',
        'A connection leaves the default group by being put into another one.': 'Spojenie opustí predvolenú oblasť tak, že ho zaradíte do inej.',
        'Services that read this group': 'Služby, ktoré čítajú túto oblasť',
        "The embedder is sent the full text of every document, note, skill and report it indexes, and the reviewer is sent every generated picture with its prompt. A group can name its own, so its data does not have to go to the instance's.": 'Model pre vnorenia dostane celý text každého dokumentu, poznámky, schopnosti a správy, ktoré indexuje, a posudzovateľ dostane každý vygenerovaný obrázok aj s jeho zadaním. Oblasť si môže určiť vlastné, aby jej dáta nemuseli ísť k tým, ktoré zvolila inštancia.',
        "The instance's choice": 'Voľba inštancie',
        "Changing it leaves this group's search keyword-only until the index is rebuilt on the Extraction page.": 'Po zmene vyhľadáva táto oblasť len podľa kľúčových slov, kým sa index neprestavia na stránke Extrakcia.',
        'Image reviewer': 'Posudzovateľ obrázkov',
        'Only models that can see images are listed.': 'Zobrazujú sa len modely, ktoré vidia obrázky.',
        'Delete the data group “%(name)s”?': 'Zmazať dátovú oblasť „%(name)s“?',
        "Where this group's data goes": 'Kam idú dáta tejto oblasti',
        "Every provider this group's data is sent to, as the instance has it set. A person holding “Manage their own data groups” may have arranged their own differently. Speech to text and text to speech are not grouped: audio is sent and not kept.": 'Každý poskytovateľ, ktorému sa dáta tejto oblasti posielajú, tak ako to má nastavené inštancia. Kto má oprávnenie „Spravovať vlastné dátové oblasti“, môže mať svoje usporiadané inak. Prevod reči na text a textu na reč sa do oblastí nedelí: zvuk sa posiela a neuchováva.',
        'its connection is in %(group)s': 'jeho spojenie je v oblasti %(group)s',
        'Nothing: no connection is in this group, and no service reads it.': 'Nikam: v tejto oblasti nie je žiadne spojenie a žiadna služba ju nečíta.',
        'Holds': 'Obsahuje',
        "A data group keeps one provider's models away from the data another provider's models have been given. Every connection is in one group. Its models read only that group's memories, notes, skills, knowledge, reports and personalities, and a chat stays in the group it was started in.": 'Dátová oblasť drží modely jedného poskytovateľa ďalej od dát, ktoré dostali modely iného poskytovateľa. Každé spojenie patrí do jednej oblasti. Jeho modely čítajú len pamäť, poznámky, schopnosti, znalosti, správy a osobnosti tejto oblasti a konverzácia zostáva v oblasti, v ktorej začala.',
        'Data group deleted.': 'Dátová oblasť zmazaná.',
        'Instance groups': 'Oblasti inštancie',
        '%(n)s connections': 'spojenia: %(n)s',
        '%(n)s chats': 'konverzácie: %(n)s',
        'own embedder': 'vlastné vnorenia',
        'own reviewer': 'vlastný posudzovateľ',
        'New data group name': 'Názov novej dátovej oblasti',
        'Create data group': 'Vytvoriť dátovú oblasť',
        'A new group holds nothing and reads nothing until a connection is put into it. Nothing moves on its own.': 'Nová oblasť nič neobsahuje a nič nečíta, kým do nej nezaradíte spojenie. Nič sa nepresúva samo.',
        'Personal groups': 'Osobné oblasti',
        'In another data group than this chat (%(group)s). Start a new chat to use them:': 'V inej dátovej oblasti než táto konverzácia (%(group)s). Ak ich chcete použiť, začnite novú konverzáciu:',
        'Only models whose connection is in this group can read it.': 'Čítať to môžu len modely, ktorých spojenie je v tejto oblasti.',
        "Moving it hands it to the other group's models, and takes it away from this group's.": 'Presunutím to dostanú modely druhej oblasti a modely tejto oblasti o to prídu.',
        'Only models whose connection is in this group can read it. Moving records between groups needs the permission to manage your own data groups.': 'Čítať to môžu len modely, ktorých spojenie je v tejto oblasti. Na presúvanie záznamov medzi oblasťami potrebujete oprávnenie spravovať vlastné dátové oblasti.',
        'Every group': 'Všetky oblasti',
        'Show': 'Zobraziť',
        'Your data groups': 'Vaše dátové oblasti',
        'yours': 'vaša',
        'Create': 'Vytvoriť',
        'A group of your own, for keeping one provider away from the rest of your data. Nobody else can see it.': 'Vlastná oblasť, aby ste jedného poskytovateľa udržali ďalej od zvyšku svojich dát. Nikto iný ju nevidí.',
        'Which group each connection reads': 'Ktorú oblasť číta ktoré spojenie',
        'Your choice applies to you alone. Following the instance keeps up with whatever the administrator sets. Moving a connection moves no data: from then on it reads the other group, and your chats on it that were started in the old group can no longer use it.': 'Vaša voľba platí len pre vás. Keď sa riadite inštanciou, sledujete to, čo nastaví správca. Presunutie spojenia nepresúva žiadne dáta: odvtedy číta druhú oblasť a vaše konverzácie na ňom, ktoré začali v pôvodnej oblasti, ho už nemôžu použiť.',
        'Follow the instance (%(group)s)': 'Podľa inštancie (%(group)s)',
        'Reads: %(group)s': 'Číta: %(group)s',
        'Set by the administrator. Changing it for yourself needs the permission to manage your own data groups.': 'Nastavuje správca. Ak to chcete zmeniť pre seba, potrebujete oprávnenie spravovať vlastné dátové oblasti.',
        'chats': 'konverzácie',
        'memories': 'záznamy v pamäti',
        'notes': 'poznámky',
        'skills': 'schopnosti',
        'knowledge bases': 'znalostné bázy',
        'reports': 'správy',
        'connections': 'spojenia',
        'Chat models': 'Modely v konverzáciách',
        'Embedding': 'Vnorenia',
        'Image review': 'Posudzovanie obrázkov',
    }
)

# --- Model rules (1.11.0) ------------------------------------------------------

# --- Helpers on another model (1.12.0) -----------------------------------------
MESSAGES.update(
    {
        'Holds one model at a time': 'Drží naraz jeden model',
        'Tick this for llama-swap in front of one GPU, or anything else that unloads one model to serve another. A model here may then be its own helper, but never send a helper to another model on this connection: loading it would unload the model whose reply is waiting.': 'Označte pri llama-swap pred jednou GPU alebo čomkoľvek, čo jeden model uvoľní, aby obslúžilo iný. Model tu potom môže byť vlastným pomocníkom, ale nikdy nepošle pomocníka inému modelu na tomto spojení: jeho načítanie by uvoľnilo model, ktorého odpoveď čaká.',
        'Serves one request at a time': 'Obsluhuje naraz jednu požiadavku',
        'Tick this for a model with a single slot. It then cannot be its own helper, because the helper would take that slot and push out the conversation the server has cached, so the waiting reply would have to read it all again. Other models can still be its helpers.': 'Označte pri modeli s jedným slotom. Potom nemôže byť vlastným pomocníkom, lebo pomocník by obsadil ten slot a vytlačil z neho rozhovor, ktorý má server uložený, takže čakajúca odpoveď by ho musela načítať celý znova. Iné modely môžu byť jeho pomocníkmi aj tak.',
    }
)

# --- Prototypes (1.14.0) --------------------------------------------------------
MESSAGES.update(
    {
        'Prototypes': 'Prototypy',
        "A model can answer with a live HTML page — a chart, a web design, a small app — shown in the conversation or in a panel beside it. It runs in the reader's browser, sealed off from this application: it cannot see the conversation around it, the reader's account or anything on this server, and nothing it contains runs here.": 'Model môže odpovedať živou stránkou HTML — grafom, návrhom webu, malou aplikáciou — zobrazenou v konverzácii alebo v paneli vedľa nej. Beží v prehliadači čitateľa, oddelená od tejto aplikácie: nevidí konverzáciu okolo seba, účet čitateľa ani nič na tomto serveri a nič z nej sa nespúšťa tu.',
        'Models may answer with prototypes': 'Modely smú odpovedať prototypmi',
        'Who may see them is the “See prototypes” permission, and every chat can switch them off in its Toggle menu.': 'Kto ich smie vidieť, určuje oprávnenie „See prototypes“ a každá konverzácia ich môže vypnúť vo svojej ponuke Prepnúť.',
        'Prototypes may load from the internet': 'Prototypy smú načítavať z internetu',
        "Off, a prototype is entirely self-contained: charts are drawn by its own code and nothing leaves the reader's browser. On, it may load libraries, fonts and pictures over HTTPS — which tells whoever serves them that somebody opened it, and stops it working offline. It can never reach this application either way.": 'Vypnuté: prototyp je úplne sebestačný — grafy kreslí vlastný kód a nič neopustí prehliadač čitateľa. Zapnuté: smie načítavať knižnice, písma a obrázky cez HTTPS — čo tomu, kto ich poskytuje, prezradí, že ho niekto otvoril, a bez siete potom nefunguje. K tejto aplikácii sa nedostane ani v jednom prípade.',
        'Open': 'Otvoriť',
        'Open beside the conversation': 'Otvoriť vedľa konverzácie',
        'Open in a new tab': 'Otvoriť na novej karte',
        'Prototype': 'Prototyp',
        'Writing it… line {lines}': 'Píše sa… riadok {lines}',
        'Shown as soon as this part of the reply is finished.': 'Zobrazí sa hneď, ako sa táto časť odpovede dokončí.',
        'A page design. Open it to see it full size, as a desktop or a phone.': 'Návrh stránky. Otvorte ho a uvidíte ho v plnej veľkosti, ako na počítači alebo na telefóne.',
        'Resize the prototype panel': 'Zmeniť šírku panela s prototypom',
        'View': 'Zobrazenie',
        'Desktop view': 'Zobrazenie pre počítač',
        'Phone view': 'Zobrazenie pre telefón',
        'Run it again': 'Spustiť znova',
        'Download the HTML': 'Stiahnuť HTML',
        'Close the prototype': 'Zavrieť prototyp',
        'Nothing open. When a model designs a page, it opens here; a prototype shown in the conversation can be opened here too.': 'Nič nie je otvorené. Keď model navrhne stránku, otvorí sa tu; aj prototyp zobrazený v konverzácii sa dá otvoriť sem.',
        'Show the prototype beside the conversation': 'Zobraziť prototyp vedľa konverzácie',
        'Windows': 'Okná',
        'Prototypes are turned off here.': 'Prototypy sú tu vypnuté.',
        'This prototype is not there any more.': 'Tento prototyp už neexistuje.',
        'This prototype is too large to show.': 'Tento prototyp je príliš veľký na zobrazenie.',
        'when you leave it': 'keď ju opustíte',
        '1 hour after the last message': 'hodinu po poslednej správe',
        '%(n)s hours after the last message': '%(n)s h po poslednej správe',
        '%(n)s minutes after the last message': '%(n)s min po poslednej správe',
        'Temporary chats': 'Dočasné konverzácie',
        'A temporary chat is not listed in the sidebar, may read the library but cannot write to it — no notes, memories, skills, reports or schedules — and is deleted when its time is up. Keep this chat turns one into an ordinary chat.': 'Dočasná konverzácia nie je v bočnom paneli, smie čítať knižnicu, ale nič do nej nezapíše — žiadne poznámky, pamäť, schopnosti, správy ani plány — a keď jej čas uplynie, zmaže sa. „Ponechať konverzáciu“ z nej urobí obyčajnú.',
        'Delete a temporary chat after': 'Zmazať dočasnú konverzáciu po',
        'Minutes after its last message, up to 1440 (a day). 0 deletes it a minute after the person leaves it; one left open in a tab that was closed without saying so goes ten minutes after its last message.': 'Minúty od poslednej správy, najviac 1440 (deň). 0 ju zmaže minútu po tom, čo ju človek opustí; tá, ktorej karta sa zavrela bez ohlásenia, zmizne desať minút po poslednej správe.',
        'Not listed in the sidebar. It may read the library but saves nothing, and is deleted %(after)s.': 'Nie je v bočnom paneli. Smie čítať knižnicu, ale nič neukladá, a zmaže sa %(after)s.',
        'Temporary · read-only': 'Dočasná · len na čítanie',
        'Starting a temporary chat. Click to go back to a normal one.': 'Začína sa dočasná konverzácia. Kliknutím sa vrátite k obyčajnej.',
        'Start a temporary chat: not listed, saves nothing, and deleted %(after)s.': 'Začať dočasnú konverzáciu: nie je v zozname, nič neukladá a zmaže sa %(after)s.',
        'Makes it an ordinary chat that can save. Otherwise it is deleted %(after)s.': 'Urobí z nej obyčajnú konverzáciu, ktorá môže ukladať. Inak sa zmaže %(after)s.',
        'A temporary chat reads and changes nothing. Keep it to choose another mode.': 'Dočasná konverzácia len číta a nič nemení. Ponechajte ju, ak chcete iný režim.',
        'There is no such reset.': 'Taký reset neexistuje.',
        'Your password is not correct, so nothing was deleted.': 'Heslo nie je správne, preto sa nič nezmazalo.',
        'RESET': 'ZMAZAŤ',
        'Type %(word)s to confirm a full reset. Nothing was deleted.': 'Úplný reset potvrďte napísaním %(word)s. Nič sa nezmazalo.',
        'Nothing was deleted.': 'Nič sa nezmazalo.',
        'There was nothing to delete.': 'Nebolo čo zmazať.',
        'Your chats and folders have been deleted.': 'Vaše konverzácie a zložky sú zmazané.',
        'Everything models remembered about you has been forgotten.': 'Všetko, čo si o vás modely pamätali, je zabudnuté.',
        'You have started over. Your account and settings are as they were.': 'Začali ste odznova. Účet a nastavenia zostali, ako boli.',
        'messages': 'správy v konverzáciách',
        'folders': 'zložky',
        'documents': 'dokumenty',
        'schedules': 'plány',
        'scratch documents': 'pracovné dokumenty',
        'Start over': 'Začať odznova',
        "Delete your own data and begin again. None of this can be undone, and nothing here touches anybody else's data. Your account, password and settings stay as they are.": 'Zmažte svoje údaje a začnite odznova. Nič z toho sa nedá vrátiť a nič tu nezasiahne údaje nikoho iného. Účet, heslo a nastavenia zostanú, ako sú.',
        'Delete all chats': 'Zmazať všetky konverzácie',
        'Every chat you own — archived, temporary and scheduled ones too — with its files, and your folders. A schedule that posts into a chat stops, since its chat is gone.': 'Každá vaša konverzácia — aj archivované, dočasné a plánované — s jej súbormi, a vaše zložky. Plán, ktorý píše do konverzácie, sa zastaví, lebo jeho konverzácia zmizne.',
        'Nothing to delete.': 'Nie je čo mazať.',
        'Delete all chats?': 'Zmazať všetky konverzácie?',
        'This deletes %(what)s. It cannot be undone.': 'Zmaže sa: %(what)s. Nedá sa to vrátiť.',
        'Forget everything models remember': 'Zabudnúť všetko, čo si modely pamätajú',
        'Your memories, and what each model has made of you — the two lists on the Memory tab.': 'Vaša pamäť a to, čo si o vás myslí každý model — dva zoznamy na karte Pamäť.',
        'Forget everything?': 'Zabudnúť všetko?',
        'Full reset': 'Úplný reset',
        'Your password': 'Vaše heslo',
        'Type %(word)s to confirm': 'Na potvrdenie napíšte %(word)s',
        'Start over completely?': 'Začať úplne odznova?',
        'nothing': 'nič',
        'API tokens': 'API tokeny',
        'Audit log': 'Záznam auditu',
        'Who did what, when and from where: sign-ins and failed ones, tokens and devices, every change an administrator makes, people starting over, updates. It names things and never quotes them — no chat, note or password is ever in it.': 'Kto čo urobil, kedy a odkiaľ: prihlásenia aj neúspešné pokusy, tokeny a zariadenia, každá zmena správcu, začatia odznova, aktualizácie. Veci pomenúva, nikdy necituje — nie je v ňom žiadna konverzácia, poznámka ani heslo.',
        'Kept for %(days)s days.': 'Uchováva sa %(days)s dní.',
        'Kept for ever.': 'Uchováva sa navždy.',
        'Action': 'Akcia',
        'A prefix: auth finds every sign-in.': 'Začiatok názvu: auth nájde každé prihlásenie.',
        'Who': 'Kto',
        'Part of an email, or an account id.': 'Časť e-mailu alebo ID účtu.',
        'From': 'Od',
        'Before': 'Pred',
        'Filter': 'Filtrovať',
        'Download CSV': 'Stiahnuť CSV',
        'Nothing matches.': 'Nič nezodpovedá.',
        'What': 'Čo',
        'token': 'token',
        'Newer': 'Novšie',
        'Older': 'Staršie',
        'Scopes': 'Rozsahy',
        'Last used': 'Naposledy použitý',
        'device': 'zariadenie',
        'personal': 'osobný',
        'never': 'nikdy',
        'Revoke this token?': 'Zrušiť tento token?',
        'Whatever holds %(name)s is signed out at once.': 'Čokoľvek drží %(name)s, bude okamžite odhlásené.',
        'Revoke': 'Zrušiť',
        'revoked': 'zrušený',
        'expired': 'vypršal',
        'Sign in a device': 'Prihlásiť zariadenie',
        'A program on another machine is asking to use this instance as you.': 'Program na inom počítači žiada, aby smel používať túto inštanciu ako vy.',
        'Your account may not sign devices in. Ask an administrator for the “Use the API” permission.': 'Váš účet nesmie prihlasovať zariadenia. Požiadajte správcu o oprávnenie „Používať API“.',
        'Device': 'Zariadenie',
        'Machine': 'Počítač',
        'Program': 'Program',
        'Asked from': 'Žiadosť z',
        'It may': 'Smie',
        'Approve only a device you are signing in yourself, right now. It keeps access until you revoke it under Settings → Devices.': 'Schváľte len zariadenie, ktoré práve teraz prihlasujete sami. Prístup si ponechá, kým ho nezrušíte v Nastavenia → Zariadenia.',
        'Approve': 'Schváliť',
        'Deny': 'Odmietnuť',
        'That code is not waiting for anybody. It may have expired — codes last ten minutes — or been used already.': 'Na tento kód nikto nečaká. Možno vypršal — kódy platia desať minút — alebo už bol použitý.',
        'The code the device shows': 'Kód, ktorý ukazuje zariadenie',
        'Continue': 'Pokračovať',
        'Back to your settings': 'Späť do nastavení',
        'Sign a device in': 'Prihlásiť zariadenie',
        'Continue from where it stopped': 'Pokračovať tam, kde sa zastavil',
        'Places on this device': 'Miesta na tomto zariadení',
        'The device will not open a session here.': 'Zariadenie tu reláciu neotvorí.',
        'All places': 'Všetky miesta',
        'Not trusted on the device': 'Na zariadení nie je dôveryhodné',
        'No directories here.': 'Nie sú tu žiadne priečinky.',
        'This device offers no directories: its remote roots are empty or missing.': 'Toto zariadenie neponúka žiadne priečinky: jeho vzdialené korene sú prázdne alebo chýbajú.',
        'This device is not linked right now.': 'Toto zariadenie teraz nie je pripojené.',
        'The device did not answer.': 'Zariadenie neodpovedalo.',
        'The code the device shows. You see what it asks for before anything is approved.': 'Kód, ktorý ukazuje zariadenie. Skôr než čokoľvek schválite, uvidíte, o čo žiada.',
        'last used %(when)s': 'naposledy %(when)s',
        'never used': 'nikdy nepoužitý',
        'Sign this device out?': 'Odhlásiť toto zariadenie?',
        '%(name)s loses access at once, and has to log in again.': '%(name)s okamžite stratí prístup a musí sa prihlásiť znova.',
        'For a script or a program of your own: send one as Authorization: Bearer to /v1 on this instance, an OpenAI-compatible API over your models. The value is shown once, when it is made.': 'Pre skript alebo vlastný program: posielajte ho ako Authorization: Bearer na /v1 tejto inštancie, API kompatibilné s OpenAI nad vašimi modelmi. Hodnota sa ukáže raz, pri vytvorení.',
        'expires %(when)s': 'vyprší %(when)s',
        'Expires': 'Platnosť',
        'Never': 'Nikdy',
        'No expiry': 'Bez obmedzenia',
        'In 30 days': 'O 30 dní',
        'In 90 days': 'O 90 dní',
        'In a year': 'O rok',
        'Make a token': 'Vytvoriť token',
        'Your new token': 'Váš nový token',
        'Copy it now. It is shown this once and cannot be shown again; if it is lost, revoke it and make another.': 'Skopírujte si ho teraz. Ukáže sa len tento raz a znova sa ukázať nedá; ak sa stratí, zrušte ho a vytvorte iný.',
        'Token': 'Token',
        'Send it as Authorization: Bearer … to /v1 on this instance.': 'Posielajte ho ako Authorization: Bearer … na /v1 tejto inštancie.',
        'Done': 'Hotovo',
        'You may not sign devices in to this instance.': 'Na túto inštanciu nesmiete prihlasovať zariadenia.',
        'Approved. The device is signing in now; you can close this page.': 'Schválené. Zariadenie sa práve prihlasuje; túto stránku môžete zavrieť.',
        'Denied. The device was not signed in.': 'Odmietnuté. Zariadenie nebolo prihlásené.',
        'You may not make API tokens.': 'Nesmiete vytvárať API tokeny.',
        'Choose at least one thing the token may do.': 'Vyberte aspoň jednu vec, ktorú token smie robiť.',
        'Revoked. Anything using it is signed out now.': 'Zrušené. Čokoľvek ho používalo, je teraz odhlásené.',
        'Revoked.': 'Zrušené.',
        'You may not read the audit log.': 'Nesmiete čítať záznam auditu.',
        'Use your models, and spend your usage': 'Používať vaše modely a míňať vašu spotrebu',
        'Read and write your memory, notes, skills and knowledge': 'Čítať a zapisovať vašu pamäť, poznámky, schopnosti a znalosti',
        'Accept work from this instance as a linked device': 'Prijímať prácu z tejto inštancie ako prepojené zariadenie',
        'Read every account and its usage': 'Čítať všetky účty a ich spotrebu',
        'Read the audit log': 'Čítať záznam auditu',
        'Credits': 'Kredity',
        'Settings': 'Nastavenia',
        'Use credits on this instance': 'Používať na tejto inštancii kredity',
        'Starting credits': 'Počiatočné kredity',
        'What an account gets the first time credits touch it.': 'Čo účet dostane, keď sa ho kredity dotknú prvýkrát.',
        'Plan for new accounts': 'Plán pre nové účty',
        'Given with the starting credits.': 'Pridelí sa spolu s počiatočnými kreditmi.',
        'People see their own balance and limits in their settings': 'Ľudia vidia vo svojich nastaveniach svoj zostatok a limity',
        'Plans': 'Plány',
        'A plan gives credits every month and can limit how fast they are spent, over the last 5 hours, day, week and 30 days. Reset: unused credits expire at renewal. Add: the monthly amount is added, up to the cap.': 'Plán dáva kredity každý mesiac a môže obmedziť, ako rýchlo sa míňajú — za posledných 5 hodín, deň, týždeň a 30 dní. Obnoviť: nevyužité kredity pri obnove prepadnú. Pridať: mesačná suma sa pripočíta, najviac po strop.',
        'a month': 'mesačne',
        'A new plan': 'Nový plán',
        'Credits a month': 'Kreditov mesačne',
        'At renewal': 'Pri obnove',
        'Reset: unused credits expire': 'Obnoviť: nevyužité kredity prepadnú',
        'Add, up to the cap': 'Pridať, najviac po strop',
        'Cap': 'Strop',
        'For Add. 0 = no cap.': 'Pre Pridať. 0 = bez stropu.',
        'Limit per 5 hours': 'Limit za 5 hodín',
        'Limit per day': 'Limit za deň',
        'Limit per week': 'Limit za týždeň',
        'Limit per 30 days': 'Limit za 30 dní',
        '0 = no limit.': '0 = bez limitu.',
        'Only these models': 'Len tieto modely',
        'Any model': 'Akýkoľvek model',
        'Model ids, separated by commas. Empty: any priced model.': 'ID modelov oddelené čiarkami. Prázdne: akýkoľvek spoplatnený model.',
        'Save plan': 'Uložiť plán',
        'Delete this plan?': 'Zmazať tento plán?',
        'Everybody on %(name)s is taken off it. Their credits stay.': 'Všetci na pláne %(name)s z neho budú vyradení. Ich kredity zostanú.',
        'Delete plan': 'Zmazať plán',
        'Balance': 'Zostatok',
        'Plan': 'Plán',
        'Change': 'Zmena',
        'Set': 'Nastaviť',
        'Top up': 'Dobiť',
        'Adjust': 'Upraviť',
        'Note': 'Poznámka',
        'Apply': 'Použiť',
        'renews %(when)s': 'obnoví sa %(when)s',
        'Last %(span)s': 'Posledných %(span)s',
        'Input': 'Vstup',
        'Per million prompt tokens.': 'Za milión tokenov zadania.',
        'Cached input': 'Vstup z cache',
        'Per million the server had cached; the input price when empty.': 'Za milión tokenov, ktoré mal server v cache; prázdne = cena vstupu.',
        'Output': 'Výstup',
        'Per million tokens written.': 'Za milión napísaných tokenov.',
        'Per million tokens of thinking; the output price when empty.': 'Za milión tokenov premýšľania; prázdne = cena výstupu.',
        'Per reply': 'Za odpoveď',
        'Added to every reply, whatever its length.': 'Pripočíta sa ku každej odpovedi bez ohľadu na dĺžku.',
        'You may not manage credits.': 'Nesmiete spravovať kredity.',
        'Only an administrator can change this.': 'Toto môže zmeniť len správca.',
        'Plan saved.': 'Plán je uložený.',
        'There is no such plan.': 'Taký plán neexistuje.',
        'Plan deleted.': 'Plán je zmazaný.',
        'There is no such account.': 'Taký účet neexistuje.',
        'Plan changed.': 'Plán je zmenený.',
        'That is not a number.': 'To nie je číslo.',
        'A top-up adds credits: give a positive number.': 'Dobitie pridáva kredity: zadajte kladné číslo.',
        'An adjustment needs an amount and a note saying why.': 'Úprava potrebuje sumu a poznámku, prečo.',
        'Top up and adjust credits, and choose plans': 'Dobíjať a upravovať kredity a vyberať plány',
        'Updated to %(version)s.': 'Aktualizované na %(version)s.',
        'What changed': 'Čo sa zmenilo',
        'Harness spec: %(have)s here': 'Špecifikácia harnessu: tu %(have)s',
        '%(want)s in %(label)s': '%(want)s vo verzii %(label)s',
        'Waiting to install %(label)s.': 'Čaká sa na inštaláciu %(label)s.',
        'Still: %(what)s.': 'Ešte: %(what)s.',
        'New replies are held until it is done.': 'Nové odpovede čakajú, kým sa to nedokončí.',
        'Nothing released': 'Nič nie je vydané',
        'Nothing to apply': 'Nie je čo nainštalovať',
        'Update to %(label)s when idle': 'Aktualizovať na %(label)s, keď bude voľno',
        'Update now?': 'Aktualizovať teraz?',
        'Replies being written are saved with what they have; open terminals are cut off. A backup is taken first.': 'Rozpísané odpovede sa uložia s tým, čo majú; otvorené terminály sa prerušia. Najprv sa urobí záloha.',
        'Update now': 'Aktualizovať teraz',
        'Now': 'Teraz',
        'Automatic updates': 'Automatické aktualizácie',
        'Check for releases on a schedule and install the ones the policy allows — only when nothing is running and nobody has been active for a while, and always after a backup. Off by default.': 'Pravidelne hľadať vydania a inštalovať tie, ktoré pravidlo dovolí — len keď nič nebeží a nikto chvíľu nič nerobil, a vždy po zálohe. Predvolene vypnuté.',
        'Install automatically': 'Inštalovať automaticky',
        'Fixes only (1.2.x)': 'Len opravy (1.2.x)',
        'Fixes and features (1.x)': 'Opravy a funkcie (1.x)',
        'Every release': 'Každé vydanie',
        'Judged on the newest release, which is what the helper installs.': 'Posudzuje sa najnovšie vydanie, lebo to pomocník nainštaluje.',
        'Check every (hours)': 'Kontrolovať každých (hodín)',
        'Last checked %(when)s UTC.': 'Naposledy skontrolované %(when)s UTC.',
        'Only between': 'Len medzi',
        'Server time. Empty: any time.': 'Čas servera. Prázdne: kedykoľvek.',
        'Idle for (minutes)': 'Nečinné (minút)',
        'Nobody opened a page or sent anything for this long.': 'Tak dlho nikto neotvoril stránku ani nič neposlal.',
        'Hold new replies after (minutes)': 'Pozdržať nové odpovede po (minútach)',
        'If it is never idle, new replies wait so the running ones can finish. 0: never hold.': 'Ak nikdy nie je voľno, nové odpovede počkajú, aby bežiace mohli skončiť. 0: nikdy nedržať.',
        'Backed up and requested. The service will restart in a moment.': 'Zálohované a vyžiadané. Služba sa o chvíľu reštartuje.',
        'It will be installed as soon as nothing is running and nobody is active.': 'Nainštaluje sa, len čo nič nebude bežať a nikto nebude aktívny.',
        'Typed in the terminal on the device; this chat shows the same session': 'Napísané v termináli na zariadení; táto konverzácia ukazuje tú istú reláciu',
        'in the terminal': 'v termináli',
        'Show the whole change': 'Zobraziť celú zmenu',
        'No output.': 'Žiadny výstup.',
        'failed': 'zlyhalo',
        'Written by': 'Napísal',
        'Background job': 'Úloha na pozadí',
        'Carried on after the instance restarted': 'Pokračované po reštarte inštancie',
        '%(total)s events · page %(page)s of %(pages)s': '%(total)s udalostí · strana %(page)s z %(pages)s',
        'Per page': 'Na stranu',
        'Pages': 'Strany',
    }
)

# --- 2.0.0: personalization, CLI connections, tokens on the user page ----------
MESSAGES.update(
    {
        "A model reads only the memories, notes, skills, knowledge and reports of the data group its connection is in, and a chat stays in the group it was started in. Two providers in different groups never see each other's.": "Model číta len pamäť, poznámky, zručnosti, znalosti a správy dátovej skupiny, v ktorej je jeho pripojenie, a chat zostáva v skupine, v ktorej sa začal. Dvaja poskytovatelia v rôznych skupinách nikdy nevidia údaje toho druhého.",
        "A register, not a character: how answers sound, never what they say.": "Tón, nie postava: ako odpovede znejú, nikdy nie čo hovoria.",
        "Both of the above, and your notes, skills, knowledge bases and their files, reports, schedules and scratch documents, with everything you shared. Kept: your account, password, settings, groups and usage.": "Oboje vyššie a k tomu vaše poznámky, zručnosti, znalostné bázy a ich súbory, správy, plány a pracovné dokumenty spolu so všetkým, čo ste zdieľali. Zostane: váš účet, heslo, nastavenia, skupiny a spotreba.",
        "Connect a machine": "Pripojiť počítač",
        "Custom instructions": "Vlastné pokyny",
        "Default": "Predvolené",
        "Each person then chooses for themselves, under Settings → Account: how they want to be helped, and a personality (concise, pragmatic, optimistic, funny, formal, Socratic, or their own words). It applies to every model, after the system prompt. Nothing is sent for anybody who has not chosen.": "Každý si potom sám vyberie v Nastavenia → Účet, ako chce, aby sa mu pomáhalo, a osobnosť (stručná, pragmatická, optimistická, vtipná, formálna, sokratovská alebo vlastnými slovami). Platí pre každý model, za systémovým promptom. Za toho, kto si nevybral, sa nič neposiela.",
        "How you want to be helped, and in what voice. It goes to every model after the system prompt, here and in the LLeMbas CLI signed in as you. Leave both empty to send nothing.": "Ako chcete, aby sa vám pomáhalo, a akým hlasom. Dostane to každý model za systémovým promptom, tu aj v LLeMbas CLI prihlásenom za vás. Ak necháte oboje prázdne, nič sa neposiela.",
        "In my own words": "Vlastnými slovami",
        "Install the LLeMbas CLI on it:": "Nainštalujte naň LLeMbas CLI:",
        "Instance name": "Názov inštancie",
        "It dials out to this instance; nothing on your machine listens. It decides which directories and modes remote work may use, whatever is asked here.": "Pripája sa sám k tejto inštancii; na vašom počítači nič nepočúva. O tom, ktoré adresáre a režimy môže práca na diaľku použiť, rozhoduje on, nech sa tu žiada čokoľvek.",
        "Let people set custom instructions and a personality": "Dovoliť ľuďom nastaviť vlastné pokyny a osobnosť",
        "None.": "Žiadne.",
        "Off, nobody can start one, whatever permissions they hold.": "Vypnuté, nikto ho nemôže začať, nech má akékoľvek oprávnenia.",
        "Open a shell on %(device)s": "Otvoriť shell na %(device)s",
        "Personality": "Osobnosť",
        "Personalization": "Prispôsobenie",
        "Sign it in to this instance, and approve the code it shows here:": "Prihláste ho k tejto inštancii a kód, ktorý ukáže, schváľte tu:",
        "The API tokens this account made and the LLeMbas CLIs it signed in. Each acts as this account, with only its scopes. Revoking one deletes it and signs out whatever holds it at once.": "API tokeny, ktoré tento účet vytvoril, a LLeMbas CLI, ktoré prihlásil. Každý koná za tento účet, len v rozsahu svojich oprávnení. Zrušenie ho zmaže a okamžite odhlási všetko, čo ho drží.",
        "The LLeMbas CLI on your machines, signed in as you. Each uses your models, your knowledge and your usage, and an agent chat here runs on one of them — under its own limits — while it is linked.": "LLeMbas CLI na vašich počítačoch, prihlásené za vás. Každé používa vaše modely, vaše znalosti a vašu spotrebu a agentový chat tu beží na jednom z nich — v jeho vlastných medziach — kým je pripojené.",
        "To run agent chats on it from here, allow it in its config (~/.config/lembas/config.yaml) and start the service:": "Aby na ňom odtiaľto bežali agentové chaty, povoľte to v jeho konfigurácii (~/.config/lembas/config.yaml) a spustite službu:",
        "Tokens and devices": "Tokeny a zariadenia",
        "Used when the personality is “In my own words”.": "Použije sa, keď je osobnosť „Vlastnými slovami“.",
        "What a device asked to sign in here is told it is signing in to, and what the LLeMbas CLI names this connection after. Empty is LLeMbas.": "Čo sa zariadeniu pri prihlasovaní povie, kam sa prihlasuje, a podľa čoho LLeMbas CLI pomenuje toto pripojenie. Prázdne znamená LLeMbas.",
        "What should a model know about you, and how should it answer?": "Čo by o vás mal model vedieť a ako by mal odpovedať?",
        "Your own words": "Vaše vlastné slová",
        "device missing": "zariadenie chýba",
        "linked now": "práve pripojené",
        "the device": "zariadenie",
    }
)

# --- 2.1.0: the agents page and the canvas, now translated --------------------
MESSAGES.update(
    {
        "A panel beside an agent chat holding an interactive shell on the linked device, when the device allows it (remote.terminal: true). What somebody types there is theirs: the modes govern the model, not the person at the keyboard. The model cannot see the panel; sending it something is a button they press.": "Panel vedľa agentovej konverzácie s interaktívnym shellom na pripojenom zariadení, ak to zariadenie dovolí (remote.terminal: true). Čo tam niekto napíše, je jeho: režimy riadia model, nie človeka pri klávesnici. Model panel nevidí; poslať mu niečo je tlačidlo, ktoré človek stlačí.",
        "An agent chat runs on a linked LLeMbas CLI: the CLI on somebody's machine does the work, under its own limits (its remote: roots and modes), and this instance shows it and passes on what is typed here. Nothing runs on this server. What you decide here is whether the feature exists, how long an approval waits, and the terminal panel.": "Agentová konverzácia beží na pripojenom LLeMbas CLI: prácu robí CLI na niečom počítači, v rámci vlastných obmedzení (svojich koreňov remote: a režimov), a táto inštancia ju zobrazuje a odovzdáva mu, čo sa tu napíše. Na tomto serveri nebeží nič. Tu rozhodujete, či funkcia existuje, ako dlho čaká schválenie a o paneli terminálu.",
        "Nothing open. A file the model reads or writes appears here, and notes, skills and this chat's own scratch document can be opened from the @ menu.": "Nič nie je otvorené. Súbor, ktorý model číta alebo zapisuje, sa objaví tu; poznámky, zručnosti a pracovný dokument tejto konverzácie sa dajú otvoriť z ponuky @.",
        "People also need the permission “%(permission)s” and a linked LLeMbas CLI (Settings → Devices).": "Ľudia potrebujú aj oprávnenie „%(permission)s“ a pripojené LLeMbas CLI (Nastavenia → Zariadenia).",
        "People also need the permission “%(permission)s”.": "Ľudia potrebujú aj oprávnenie „%(permission)s“.",
        "Shells open right now: %(n)s": "Práve otvorené shelly: %(n)s",
        "This device's LLeMbas CLI is too old to list its directories. Type the path below.": "LLeMbas CLI tohto zariadenia je príliš staré na to, aby vypísalo svoje adresáre. Napíšte cestu nižšie.",
    }
)

# --- 2.2.0: the rest of the interface -- the templates, the scripts, the
# messages the server sends back, and permissions said to the person ------------
MESSAGES.update(
    {
        "being an administrator": "postavenia správcu",
        "the baseline": "základu",
        "%(name)s — a web UI for your language models.": "%(name)s — webové rozhranie pre vaše jazykové modely.",
        "Send a message…": "Napíšte správu…",
        "Ask anything…": "Spýtajte sa čokoľvek…",
        "Enter to send, Shift+Enter for a new line. %(commands)s, @ for files.": "Enter odošle, Shift+Enter pridá nový riadok. %(commands)s, @ pre súbory.",
        "Drag files in, or paste an image.": "Súbory sem pretiahnite alebo vložte obrázok.",
        "<strong>%(model)s has no vision</strong>, so images will not be sent — documents still will.": "<strong>%(model)s nevidí obrázky</strong>, takže sa obrázky neodošlú — dokumenty áno.",
        "New chat": "Nová konverzácia",
        "What should this chat be called?": "Ako sa má táto konverzácia volať?",
        "Instructions that apply to every message in this chat.": "Pokyny, ktoré platia pre každú správu v tejto konverzácii.",
        "Leave empty to use the folder prompt shown above.": "Nechajte prázdne a použije sa pokyn zložky uvedený vyššie.",
        "Leave empty to use the model prompt shown above.": "Nechajte prázdne a použije sa pokyn modelu uvedený vyššie.",
        "Leave empty to use the instance prompt shown above.": "Nechajte prázdne a použije sa pokyn inštancie uvedený vyššie.",
        "Overrides the model and instance prompts for this chat only.": "Len pre túto konverzáciu nahradí pokyny modelu aj inštancie.",
        "Searches in this chat are limited to what is ticked.": "Vyhľadávanie v tejto konverzácii je obmedzené na to, čo je zaškrtnuté.",
        "Nothing ticked, so this chat can search everything in your <a href=\"/library/knowledge\">library</a>.": "Nič nie je zaškrtnuté, takže táto konverzácia môže prehľadávať celú vašu <a href=\"/library/knowledge\">knižnicu</a>.",
        "Add an OpenAI-compatible connection and %(name)s will load its models.": "Pridajte spojenie kompatibilné s OpenAI a %(name)s načíta jeho modely.",
        "No model connections have been set up yet. Ask an administrator.": "Zatiaľ nie je nastavené žiadne spojenie s modelmi. Obráťte sa na správcu.",
        "Set up a connection": "Nastaviť spojenie",
        "Restore chat": "Obnoviť konverzáciu",
        "Archive chat": "Archivovať konverzáciu",
        "Put this chat back in the list": "Vrátiť túto konverzáciu do zoznamu",
        "Hide this chat without deleting it": "Skryť túto konverzáciu bez zmazania",
        "Delete “%(title)s”? This cannot be undone.": "Zmazať „%(title)s“? Nedá sa to vrátiť.",
        "What should this folder be called?": "Ako sa má táto zložka volať?",
        "Delete the folder “%(name)s”? Chats inside it are kept.": "Zmazať zložku „%(name)s“? Konverzácie v nej zostanú.",
        "Pinned models": "Pripnuté modely",
        "No agent chats yet. Nothing is stirring out there.": "Zatiaľ žiadne agentské konverzácie. Tam vonku sa nič nehýbe.",
        "No chats yet. The road begins here.": "Zatiaľ žiadne konverzácie. Cesta sa začína tu.",
        "Archived": "Archivované",
        "Instance default — %(voice)s": "Predvolený inštancie — %(voice)s",
        "%(voice)s (not currently offered)": "%(voice)s (momentálne neponúkaný)",
        "Appearance": "Vzhľad",
        "Security": "Zabezpečenie",
        "Language saved.": "Jazyk je uložený.",
        "Timezone saved.": "Časová zóna je uložená.",
        "That language is not offered here.": "Tento jazyk tu nie je k dispozícii.",
        "Administrator": "Správca",
        "Member": "Člen",
        "You are an administrator, so every permission applies.": "Ste správca, takže platia všetky oprávnenia.",
        "From the instance baseline plus whatever your groups add.": "Zo základu inštancie a k tomu všetko, čo pridávajú vaše skupiny.",
        "allowed": "povolené",
        "not allowed": "nepovolené",
        "Follow the instance (%(language)s)": "Podľa inštancie (%(language)s)",
        "dark": "tmavá",
        "light": "svetlá",
        "Follow the server (%(zone)s)": "Podľa servera (%(zone)s)",
        "Saved. It is %(time)s where you are.": "Uložené. U vás je %(time)s.",
        "It is currently %(time)s where you are.": "U vás je práve %(time)s.",
        "Install": "Nainštalovať",
        "The voice list could not be read: %(error)s": "Zoznam hlasov sa nepodarilo načítať: %(error)s",
        "%(n)s available.": "K dispozícii: %(n)s.",
        "An ISO code such as <code>en</code> or <code>sk</code>. Empty lets the server work it out, which is usually best.": "Kód ISO, napríklad <code>en</code> alebo <code>sk</code>. Prázdne pole nechá jazyk určiť server, čo býva najlepšie.",
        "What models remember about you": "Čo si o vás modely pamätajú",
        "Short facts, shown to every model you talk to on every turn — so they are few and short on purpose. Anything longer belongs in a <a href=\"/library/notes\">note</a>. Nothing here is shared with anyone else, ever.": "Krátke fakty, ktoré vidí každý model, s ktorým hovoríte, v každom kroku — preto ich je zámerne málo a sú krátke. Čokoľvek dlhšie patrí do <a href=\"/library/notes\">poznámky</a>. Nič z toho sa nikdy s nikým nezdieľa.",
        "One fact per record, at most %(n)s characters. Never put a password or a key here — it is sent to the model with every message.": "Jeden fakt na záznam, najviac %(n)s znakov. Nikdy sem nedávajte heslo ani kľúč — posiela sa modelu s každou správou.",
        "Something went wrong.": "Niečo sa pokazilo.",
        "Start chats": "Začínať konverzácie",
        "Create new conversations.": "Vytvárať nové konverzácie.",
        "Delete chats": "Mazať konverzácie",
        "Delete their own conversations.": "Mazať vlastné konverzácie.",
        "Set system prompts": "Nastavovať systémové pokyny",
        "Give an individual chat its own system prompt.": "Dať jednotlivej konverzácii vlastný systémový pokyn.",
        "Adjust sampling": "Upravovať vzorkovanie",
        "Change temperature, top-p and similar per chat.": "Meniť teplotu, top-p a podobné nastavenia pre jednotlivé konverzácie.",
        "Choose the model": "Vyberať model",
        "Switch a chat to a different model. Without this, chats use the default.": "Prepnúť konverzáciu na iný model. Bez tohto konverzácie používajú predvolený.",
        "See prototypes": "Vidieť prototypy",
        "Let a model answer with a live HTML page — a chart, a design, a small app — shown in the conversation or in a panel beside it. It runs in the reader's browser, sealed off from this application, and nothing runs on the server.": "Model môže odpovedať živou stránkou HTML — grafom, návrhom, malou aplikáciou — zobrazenou v konverzácii alebo v paneli vedľa nej. Beží v prehliadači čitateľa, oddelená od tejto aplikácie, a na serveri sa nespúšťa nič.",
        "Manage folders": "Spravovať zložky",
        "Create, rename, nest and delete folders.": "Vytvárať, premenúvať, vnárať a mazať zložky.",
        "Attach files": "Prikladať súbory",
        "Attach images, PDFs and text files to a message. Images only reach models marked as having vision.": "Prikladať k správe obrázky, PDF a textové súbory. Obrázky dostanú len modely označené ako vidiace.",
        "Search the web": "Hľadať na webe",
        "Let a model look things up while it answers. Only offered to models marked as supporting tools, and only when web search is configured.": "Model si môže počas odpovede niečo vyhľadať. Ponúka sa len modelom označeným, že podporujú nástroje, a len keď je vyhľadávanie na webe nastavené.",
        "Let a model retrieve one web page and read it, given its address. Addresses on this machine and this network are refused unless an administrator has allowed them.": "Model môže podľa adresy stiahnuť jednu webovú stránku a prečítať ju. Adresy na tomto stroji a v tejto sieti sa odmietajú, pokiaľ ich správca nepovolil.",
        "Generate images": "Generovať obrázky",
        "Let a model draw a picture and show it in the conversation. Only offered when an image generator has been configured, and every generation spends time on whatever machine is running it.": "Model môže nakresliť obrázok a ukázať ho v konverzácii. Ponúka sa len vtedy, keď je nastavený generátor obrázkov, a každé generovanie zaberie čas stroju, na ktorom beží.",
        "Use custom tools": "Používať vlastné nástroje",
        "Let a model call the HTTP tools an administrator has defined. Which ones depends on the groups each tool is restricted to.": "Model môže volať nástroje HTTP, ktoré definoval správca. Ktoré, závisí od skupín, na ktoré je každý nástroj obmedzený.",
        "Use MCP servers": "Používať servery MCP",
        "Let a model call tools from the MCP servers an administrator has added. Which ones depends on the groups each server is restricted to.": "Model môže volať nástroje zo serverov MCP, ktoré pridal správca. Ktoré, závisí od skupín, na ktoré je každý server obmedzený.",
        "Let them start agent chats on a LLeMbas CLI they have linked: its model reads files, writes files and runs commands on that machine, within the CLI's own limits and the chat's mode. Nothing runs on this server.": "Môže začínať agentské konverzácie na prepojenom LLeMbas CLI: jeho model na tom stroji číta a zapisuje súbory a spúšťa príkazy, v medziach samotného CLI a režimu konverzácie. Na tomto serveri sa nespúšťa nič.",
        "Open an interactive shell on their linked device, from inside an agent chat, where the device allows it. What they type there is theirs: the chat's mode governs the model, not the person at the keyboard.": "Otvoriť interaktívny shell na prepojenom zariadení priamo z agentskej konverzácie, ak to zariadenie dovoľuje. Čo tam napíše, je jeho vec: režim konverzácie riadi model, nie človeka pri klávesnici.",
        "Let a model hand a self-contained piece of work to a second one that runs on its own and reports back — reading and searching in parallel rather than one thing at a time. A helper cannot ask questions, cannot spawn helpers of its own, and can only do what this chat could already do without stopping to ask.": "Model môže odovzdať samostatnú časť práce druhému, ktorý pracuje sám a podá správu — číta a hľadá súbežne namiesto jednej veci po druhej. Pomocník sa nemôže pýtať, nemôže spúšťať vlastných pomocníkov a smie len to, čo by táto konverzácia smela aj bez pýtania.",
        "Be asked questions": "Dostávať otázky",
        "Let a model stop mid-reply and ask you something, with answers to pick from or a box to write your own.": "Model sa môže uprostred odpovede zastaviť a na niečo sa vás opýtať — s odpoveďami na výber alebo s políčkom na vlastnú.",
        "Write in the canvas": "Písať na plochu",
        "Let a model build something up in this chat's scratch document, which sits open beside the conversation and can be edited and attached to a message. It belongs to the chat and is not searchable afterwards.": "Model môže niečo budovať v pracovnom dokumente konverzácie, ktorý je otvorený vedľa nej a dá sa upravovať a priložiť k správe. Patrí konverzácii a neskôr sa v ňom nedá vyhľadávať.",
        "Set things to run later, on their own — once, or on a repeating timetable. This spends model time with nobody at the keyboard, so it is a capability chosen on purpose rather than one everybody has.": "Nastaviť veci, aby bežali neskôr samy — raz alebo podľa opakovaného rozvrhu. Míňa to čas modelu bez nikoho pri klávesnici, preto je to schopnosť udelená zámerne, nie samozrejmosť pre všetkých.",
        "Keep reports": "Mať správy",
        "Read the Reports section: finished pieces of work filed for them to read later, by a model that was asked for one or by something that ran while they were away.": "Čítať sekciu Správy: hotové kusy práce odložené na neskoršie čítanie, ktoré podal model, keď ho o to požiadali, alebo niečo, čo bežalo počas neprítomnosti.",
        "File reports": "Podávať správy",
        "Let a model write a report when it finishes a piece of work, and read back ones it filed earlier. A report is addressed to the reader and cannot be replied to, so this costs nothing but a place to put things.": "Model môže po dokončení práce napísať správu a čítať tie, ktoré podal skôr. Správa je určená čitateľovi a nedá sa na ňu odpovedať, takže to nestojí nič okrem miesta, kam veci odložiť.",
        "Dictate messages": "Diktovať správy",
        "Speak a message instead of typing it. Needs a transcription endpoint.": "Správu vysloviť namiesto písania. Vyžaduje endpoint na prepis.",
        "Play replies aloud": "Prehrávať odpovede nahlas",
        "Have a reply read out. Needs a speech endpoint.": "Nechať si odpoveď prečítať. Vyžaduje endpoint reči.",
        "Use the library": "Používať knižnicu",
        "Keep knowledge documents, notes, memories and skills of their own.": "Mať vlastné dokumenty znalostí, poznámky, spomienky a schopnosti.",
        "Share library items": "Zdieľať položky knižnice",
        "Give other people, or a group, access to their knowledge bases, notes, skills and reports. Sharing grants reading only — never changing, and never sharing on.": "Dať iným ľuďom alebo skupine prístup k vlastným znalostným bázam, poznámkam, schopnostiam a správam. Zdieľanie dovoľuje len čítať — nikdy meniť ani zdieľať ďalej.",
        "Search their knowledge": "Prehľadávať vlastné znalosti",
        "Let a model search the documents this user has collected.": "Model môže prehľadávať dokumenty, ktoré tento používateľ zhromaždil.",
        "Read and write notes": "Čítať a písať poznámky",
        "Let a model keep its own notes for this user, and read them back later.": "Model si môže pre tohto používateľa viesť vlastné poznámky a neskôr si ich prečítať.",
        "Remember things": "Pamätať si veci",
        "Let a model record short facts about this user, shown to it on every turn.": "Model si môže zaznamenávať krátke fakty o tomto používateľovi, ktoré vidí v každom kroku.",
        "Use and write skills": "Používať a písať schopnosti",
        "Let a model follow saved instructions, and write new ones. Every change is recorded and can be rolled back.": "Model sa môže riadiť uloženými postupmi a písať nové. Každá zmena sa zaznamená a dá sa vrátiť.",
        "Write notes": "Písať poznámky",
        "Let a model create, change and delete notes. Without it, it can still search and read the ones that are there.": "Model môže poznámky vytvárať, meniť a mazať. Bez toho ich stále môže prehľadávať a čítať.",
        "Record memories": "Zaznamenávať spomienky",
        "Let a model add and forget short facts about this person. Without it, the memories it already has are still shown to it every turn.": "Model môže pridávať a zabúdať krátke fakty o tomto človeku. Bez toho mu už existujúce spomienky stále ukazujeme v každom kroku.",
        "Write skills": "Písať schopnosti",
        "Let a model write new skills and change existing ones. Without it, it follows the skills that are there and cannot add to them — which is the setting for an instance whose skills are curated by hand.": "Model môže písať nové schopnosti a meniť existujúce. Bez toho sa riadi tými, ktoré existujú, a nemôže pridávať — to je nastavenie pre inštanciu, ktorej schopnosti sa starostlivo vyberajú ručne.",
        "Manage their own data groups": "Spravovať vlastné dátové skupiny",
        "Make personal data groups, choose which of them each connection reads for this person, and move their own records between groups.": "Vytvárať osobné dátové skupiny, voliť, ktoré z nich každé spojenie pre tohto človeka číta, a presúvať vlastné záznamy medzi skupinami.",
        "Use the API": "Používať API",
        "Make API tokens and sign devices in (the LLeMbas CLI's /login): their own models through /v1, as themselves, counted in their usage.": "Vytvárať tokeny API a prihlasovať zariadenia (/login v LLeMbas CLI): vlastné modely cez /v1, vo vlastnom mene a započítané do vlastnej spotreby.",
        "Grant credits": "Prideľovať kredity",
        "Top up and adjust other people's credits and choose their plans -- for a team lead, say. Every change is in the audit log.": "Dobíjať a upravovať kredity iných ľudí a vyberať im plány -- napríklad pre vedúceho tímu. Každá zmena je v zázname auditu.",
        "See who signed in, who changed what and which devices were signed in, across the instance.": "Vidieť, kto sa prihlásil, kto čo zmenil a ktoré zariadenia boli prihlásené, v celej inštancii.",
        "Workspace": "Pracovný priestor",
        "Agent": "Agent",
        "API": "API",
        "Tools a model may use": "Nástroje, ktoré smie model použiť",
        "Agents, devices and the API": "Agenti, zariadenia a API",
        "Scheduling and reports": "Plánovanie a správy",
        "Other people": "Iní ľudia",
        "Start new chats.": "Začínať nové konverzácie.",
        "Delete your own chats.": "Mazať vlastné konverzácie.",
        "Give a chat its own system prompt.": "Dať konverzácii vlastný systémový pokyn.",
        "Change temperature, top-p and the like for a chat.": "Meniť teplotu, top-p a podobne pre konverzáciu.",
        "Switch a chat to another model.": "Prepnúť konverzáciu na iný model.",
        "See the live pages a model answers with.": "Vidieť živé stránky, ktorými model odpovedá.",
        "Attach images, PDFs and text files to a message.": "Prikladať k správe obrázky, PDF a textové súbory.",
        "Look things up on the web while answering.": "Počas odpovede vyhľadávať na webe.",
        "Read a web page from its address.": "Prečítať webovú stránku podľa jej adresy.",
        "Draw pictures in the conversation.": "Kresliť obrázky do konverzácie.",
        "Call the HTTP tools an administrator has defined.": "Volať nástroje HTTP, ktoré definoval správca.",
        "Use tools from the MCP servers an administrator has added.": "Používať nástroje zo serverov MCP, ktoré pridal správca.",
        "Hand a self-contained task to a helper model.": "Odovzdať samostatnú úlohu pomocnému modelu.",
        "Stop mid-reply and ask you a question.": "Zastaviť sa uprostred odpovede a niečo sa vás opýtať.",
        "Write in the chat's canvas document.": "Písať do pracovného dokumentu konverzácie na ploche.",
        "File a report when a piece of work is finished.": "Podať správu, keď je kus práce hotový.",
        "Start agent chats on a LLeMbas CLI you have linked.": "Začínať agentské konverzácie na prepojenom LLeMbas CLI.",
        "Open a shell on your linked device from an agent chat.": "Z agentskej konverzácie otvoriť shell na prepojenom zariadení.",
        "Make API tokens and sign the LLeMbas CLI in as you.": "Vytvárať tokeny API a prihlásiť LLeMbas CLI vo vašom mene.",
        "Set work to run later, once or on a timetable.": "Nastaviť prácu na neskôr, raz alebo podľa rozvrhu.",
        "Read the reports filed for you.": "Čítať správy, ktoré pre vás niekto podal.",
        "Speak a message instead of typing it.": "Správu vysloviť namiesto písania.",
        "Have replies read aloud.": "Nechať si odpovede čítať nahlas.",
        "Keep documents, notes, memories and skills of your own.": "Mať vlastné dokumenty, poznámky, spomienky a schopnosti.",
        "Let other people read your library items.": "Dovoliť iným čítať položky vašej knižnice.",
        "Let a model search your documents.": "Model smie prehľadávať vaše dokumenty.",
        "Let a model read your notes.": "Model smie čítať vaše poznámky.",
        "Let a model write, change and delete notes.": "Model smie písať, meniť a mazať poznámky.",
        "Let a model see what it remembers about you.": "Model smie vidieť, čo si o vás pamätá.",
        "Let a model add and forget facts about you.": "Model smie pridávať a zabúdať fakty o vás.",
        "Let a model follow your saved skills.": "Model sa smie riadiť vašimi uloženými schopnosťami.",
        "Let a model write and change skills.": "Model smie písať a meniť schopnosti.",
        "Make your own data groups and choose what each connection reads.": "Vytvárať vlastné dátové skupiny a voliť, čo ktoré spojenie číta.",
        "Top up and adjust other people's credits.": "Dobíjať a upravovať kredity iných ľudí.",
        "Read the instance's audit log.": "Čítať záznam auditu inštancie.",
        "Waybread for the long road of thought.": "Lembas na dlhú cestu myslenia.",
        "Speak friend and enter.": "Povedz, priateľ, a vstúp.",
        "No road from here": "Odtiaľto nevedie cesta",
        "The Road goes ever on and on — but not without a connection.": "Cesta vedie ďalej a ďalej — ale nie bez pripojenia.",
        "Speak friend and enter. This door is not yours to open.": "Povedz, priateľ, a vstúp. Tieto dvere však nie sú vaše, aby ste ich otvárali.",
        "Not all those who wander are lost. This page, however, is.": "Nie každý, kto blúdi, je stratený. Táto stránka však áno.",
        "The Road goes ever on, but this stretch of it has washed out.": "Cesta vedie stále ďalej, no tento jej úsek odplavila voda.",
        "Switch to the light theme": "Prepnúť na svetlú tému",
        "Switch to the dark theme": "Prepnúť na tmavú tému",
        "Copied": "Skopírované",
        "Stop recording": "Zastaviť nahrávanie",
        "Open the command menu": "Otvoriť ponuku príkazov",
        "Send, from anywhere on the page": "Odoslať odkiaľkoľvek na stránke",
        "Dictate": "Diktovať",
        "Read the last reply aloud": "Prečítať nahlas poslednú odpoveď",
        "Sidebar": "Bočný panel",
        "↑ in an empty box": "↑ v prázdnom poli",
        "Edit your last message": "Upraviť poslednú správu",
        "Close what is open, or stop the reply": "Zavrieť, čo je otvorené, alebo zastaviť odpoveď",
        "Copy and paste inside the terminal": "Kopírovať a vkladať v termináli",
        "Commands and keyboard shortcuts": "Príkazy a klávesové skratky",
        "Tokens and context used by this chat": "Tokeny a kontext, ktoré táto konverzácia spotrebovala",
        "Usage": "Spotreba",
        "Could not read this chat's usage.": "Spotrebu tejto konverzácie sa nepodarilo načítať.",
        "How hard a reasoning model should think": "Ako usilovne má model uvažovať",
        "Approval mode: manual, edit, auto or plan": "Režim schvaľovania: manual, edit, auto alebo plan",
        "Modes: manual, edit, auto, plan.": "Režimy: manual, edit, auto, plan.",
        "“%(mode)s” is not a mode.": "„%(mode)s“ nie je režim.",
        "Mode set to %(mode)s.": "Režim je nastavený na %(mode)s.",
        "Rename this chat": "Premenovať túto konverzáciu",
        "the new title": "nový názov",
        "Give it a title: /title Something.": "Zadajte názov: /title Niečo.",
        "Renamed.": "Premenované.",
        "Show or hide the canvas": "Zobraziť alebo skryť plochu",
        "Show or hide the terminal": "Zobraziť alebo skryť terminál",
        "Show or hide the request inspector": "Zobraziť alebo skryť inšpektora požiadaviek",
        "Show or hide the sidebar": "Zobraziť alebo skryť bočný panel",
        "Draw a picture": "Nakresliť obrázok",
        "what to draw": "čo nakresliť",
        "Say what to draw: /image a red bicycle in the rain.": "Povedzte, čo nakresliť: /image červený bicykel v daždi.",
        "Could not start the image.": "Obrázok sa nepodarilo začať.",
        "Start a new chat": "Začať novú konverzáciu",
        "Start a temporary chat that saves nothing": "Začať dočasnú konverzáciu, ktorá nič neukladá",
        "Carry on from where the reply stopped": "Pokračovať tam, kde odpoveď skončila",
        "There is no reply to continue yet.": "Zatiaľ nie je odpoveď, v ktorej by sa dalo pokračovať.",
        "Stop the reply being written": "Zastaviť písanú odpoveď",
        "Nothing is being written.": "Nič sa práve nepíše.",
        "Your library": "Vaša knižnica",
        "Notes the model has written": "Poznámky, ktoré napísal model",
        "Saved procedures": "Uložené postupy",
        "This model is not marked as a reasoning model, so effort would do nothing. An administrator can mark it on the model's page.": "Tento model nie je označený ako uvažujúci, takže náročnosť by nič nezmenila. Správca ho môže označiť na stránke modelu.",
        "No effort is being sent. Try %(levels)s.": "Neposiela sa žiadna náročnosť. Skúste %(levels)s.",
        "Effort is %(level)s. /effort %(levels)s, or off.": "Náročnosť je %(level)s. /effort %(levels)s alebo off.",
        "“%(level)s” is not an effort this model takes. Try %(levels)s or off.": "„%(level)s“ nie je náročnosť, ktorú tento model prijíma. Skúste %(levels)s alebo off.",
        "Effort cleared; nothing is sent.": "Náročnosť je zrušená; nič sa neposiela.",
        "Effort set to %(level)s.": "Náročnosť je nastavená na %(level)s.",
        "Already summarising.": "Zhrnutie už prebieha.",
        "Summarise everything before the last reply? The messages stay in the transcript; they just stop being sent to the model.": "Zhrnúť všetko pred poslednou odpoveďou? Správy v prepise zostanú; len sa prestanú posielať modelu.",
        "Compact this chat": "Zhrnúť túto konverzáciu",
        "Choose a connection above, and this panel will open a shell on it.": "Vyberte vyššie spojenie a tento panel na ňom otvorí shell.",
        "Disconnected. Close and reopen to reconnect.": "Odpojené. Zavrite a znova otvorte, aby sa pripojilo.",
        "Could not connect. If this instance is behind a proxy, it may not be passing WebSocket upgrades through.": "Nepodarilo sa pripojiť. Ak je táto inštancia za proxy, možno nepúšťa ďalej prechody na WebSocket.",
        "Connected. This shell is also open in another tab, and they share a size.": "Pripojené. Tento shell je otvorený aj v inej karte a zdieľajú veľkosť.",
        "Connected.": "Pripojené.",
        "Reconnecting…": "Znova sa pripája…",
        "The terminal could not be loaded.": "Terminál sa nepodarilo načítať.",
        "This shell did not load the LLeMbas command markers, so there is no way to tell where the output of one command ends.": "Tento shell nenačítal značky príkazov LLeMbas, takže sa nedá zistiť, kde končí výstup jedného príkazu.",
        "Start a session": "Nová relácia",
        "Open a fresh conversation with nothing assumed.": "Čistá konverzácia bez akýchkoľvek predpokladov.",
        "What can you do?": "Čo všetko dokážeš?",
        "Have the model say which tools, instructions and limits it has.": "Nechajte model povedať, aké má nástroje, pokyny a obmedzenia.",
        "Show me some code": "Ukáž mi nejaký kód",
        "See how the model writes, in a language of its own choosing.": "Pozrite sa, ako model píše, v jazyku podľa vlastného výberu.",
        "Tell me something": "Povedz mi niečo",
        "A fact worth knowing, from a subject picked at random.": "Fakt, ktorý stojí za to poznať, z náhodne vybranej oblasti.",
        "Everything is shown to you before it happens.": "Všetko vám ukáže skôr, než sa to stane.",
        "Files are read and written freely; commands are shown to you first.": "Súbory číta a zapisuje voľne; príkazy vám najprv ukáže.",
        "Nothing is shown to you first. Only for work you would do yourself.": "Nič vám vopred neukáže. Len na prácu, ktorú by ste urobili aj sami.",
        "Reads freely, changes nothing, and finishes by proposing a plan.": "Číta voľne, nič nemení a skončí návrhom plánu.",
        "1 model": "1 model",
        "%(n)s models": "modely: %(n)s",
        "Test & refresh": "Otestovať a obnoviť",
        "Currently <code>%(masked)s</code>. Leave the dots alone to keep it, or clear the field to remove the key entirely.": "Teraz <code>%(masked)s</code>. Bodky nechajte tak, ak ho chcete ponechať, alebo pole vymažte a kľúč sa úplne odstráni.",
        "No key is stored for this endpoint.": "Pre tento endpoint nie je uložený žiadny kľúč.",
        "Only used by image generation's Preserve VRAM, to free this endpoint's memory while ComfyUI works. llama-swap answers <code>GET /unload</code>. Leave it empty for anything on another machine — its memory is not the memory being freed, and it would be an unexplained request.": "Používa ho len voľba Šetriť VRAM pri generovaní obrázkov, aby sa uvoľnila pamäť tohto endpointu, kým pracuje ComfyUI. llama-swap odpovedá na <code>GET /unload</code>. Pre čokoľvek na inom stroji ho nechajte prázdne — jeho pamäť nie je tá, ktorá sa uvoľňuje, a bola by to nevysvetliteľná požiadavka.",
        "One <code>Name: value</code> per line, sent with every request to this endpoint. OpenRouter reads <code>HTTP-Referer</code> and <code>X-Title</code> and attributes your usage with them. Leave it empty unless an endpoint has asked for something.": "Jedna <code>Name: value</code> na riadok, posiela sa s každou požiadavkou na tento endpoint. OpenRouter číta <code>HTTP-Referer</code> a <code>X-Title</code> a podľa nich priraďuje vašu spotrebu. Nechajte prázdne, pokiaľ si endpoint niečo nevyžiadal.",
        "Last checked %(when)s UTC": "Naposledy overené %(when)s UTC",
        "Never checked": "Ešte neoverené",
        "Delete the connection “%(name)s”? Existing chats keep their history.": "Zmazať spojenie „%(name)s“? Existujúce konverzácie si históriu ponechajú.",
        "Your list above was left alone. What ComfyUI has:": "Váš zoznam vyššie zostal nezmenený. Čo má ComfyUI:",
        "Filled in above:": "Doplnené vyššie:",
        "Rebuilding: %(done)s of %(total)s records (%(percent)s%%), %(written)s pieces written. You can leave this page — it carries on.": "Prestavuje sa: %(done)s z %(total)s záznamov (%(percent)s %%), zapísané časti: %(written)s. Túto stránku môžete opustiť — pokračuje sa ďalej.",
        "Finished: %(done)s records, %(written)s pieces indexed.": "Hotovo: záznamy: %(done)s, zaindexované časti: %(written)s.",
        "Indexed now:": "Teraz zaindexované:",
        "<strong>%(count)s</strong> from %(kind)s": "%(kind)s: <strong>%(count)s</strong>",
        "Nothing is indexed yet.": "Zatiaľ nie je nič zaindexované.",
        "Rebuilding…": "Prestavuje sa…",
        "Rebuild the index": "Prestavať index",
        "Groups & permissions": "Skupiny a oprávnenia",
        "1 tool": "1 nástroj",
        "%(n)s tools": "nástroje: %(n)s",
        "Edit": "Upraviť",
        "Put the built-in wording back in this box? Your edit is lost, but nothing is saved until you press Save settings.": "Vrátiť do tohto poľa vstavané znenie? Vaša úprava sa stratí, no nič sa neuloží, kým nestlačíte Uložiť nastavenia.",
        "— only appears when %(names)s has something in it.": "— objaví sa, len keď %(names)s niečo obsahuje.",
        "Leave the box empty to leave this out of the prompt entirely.": "Ak pole necháte prázdne, táto časť sa do pokynu vôbec nedostane.",
        "The preamble is %(chars)s characters and the cap is %(limit)s. Everything past the cap is cut off before it reaches the model.": "Preambula má %(chars)s znakov a limit je %(limit)s. Všetko za limitom sa odreže skôr, než sa to dostane k modelu.",
        "%(chars)s of %(limit)s characters before the authored prompt.": "%(chars)s z %(limit)s znakov pred napísaným pokynom.",
        "The instance prompt is shown below the line; a model or chat prompt would replace it, never stack with it.": "Pokyn inštancie je zobrazený pod čiarou; pokyn modelu alebo konverzácie by ho nahradil, nikdy by sa k nemu nepridal.",
        "No instance prompt is set, so nothing follows the preamble.": "Pokyn inštancie nie je nastavený, takže za preambulou nič nenasleduje.",
        "%(name)s answered.": "%(name)s odpovedal.",
        "Seconds with nobody watching <em>and</em> nothing typed. Closing the panel does not end the session — a build carries on and is still there on the way back — so this is what eventually ends one.": "Sekundy, keď sa nikto nepozerá <em>a</em> nič sa nepíše. Zatvorenie panela reláciu neukončí — zostavovanie pokračuje a po návrate tam stále je — takže práve toto ju nakoniec ukončí.",
        "<strong>Asking another model a question uses the same switch and the same allowance below</strong>, because it costs the same thing: one reply setting another reply going. Which people may do it is a separate permission — <strong>%(permission)s</strong> — and which models may is a switch on each model's own page.": "<strong>Otázka pre iný model používa ten istý prepínač a ten istý limit nižšie</strong>, pretože stojí to isté: jedna odpoveď spúšťa ďalšiu. Ktorí ľudia to smú, určuje samostatné oprávnenie — <strong>%(permission)s</strong> — a ktoré modely, prepínač na stránke každého modelu.",
        "People also need the <strong>%(permission)s</strong> permission, and the model needs the <strong>%(capability)s</strong> capability. Off by default: a reply that spawns helpers spends model time multiplicatively, and on one local endpoint four at once is four times the queue rather than four times the speed.": "Ľudia potrebujú aj oprávnenie <strong>%(permission)s</strong> a model schopnosť <strong>%(capability)s</strong>. Predvolene vypnuté: odpoveď, ktorá spúšťa pomocníkov, míňa čas modelu násobne a na jednom lokálnom endpointe sú štyria naraz štvornásobný rad, nie štvornásobná rýchlosť.",
        "Seconds. Past it the helper is <em>stopped</em>, not abandoned: what it had written is kept and handed back with a note saying it is partial.": "Sekundy. Potom sa pomocník <em>zastaví</em>, no neopustí: čo stihol napísať, sa ponechá a odovzdá s poznámkou, že je to neúplné.",
        "Where <code>/v1/audio/transcriptions</code> lives — whisper.cpp's <code>whisper-server</code>, Speaches, or anything else speaking it. With or without <code>/v1</code>; either is understood.": "Kde sa nachádza <code>/v1/audio/transcriptions</code> — <code>whisper-server</code> z whisper.cpp, Speaches alebo čokoľvek iné, čo ho podporuje. S <code>/v1</code> aj bez; rozumie sa obom.",
        "None needed for a local server": "Pre lokálny server netreba",
        "A server hosting one model <strong>ignores this</strong> and uses whatever it was started with — <code>whisper-1</code> is then just a label. It only selects anything on a server that hosts several.": "Server s jedným modelom <strong>toto ignoruje</strong> a použije to, s čím bol spustený — <code>whisper-1</code> je potom len označenie. Niečo vyberá len na serveri, ktorý ich má niekoľko.",
        "An ISO code such as <code>en</code> or <code>sk</code>. Leave empty to let the server detect it, which is what whisper does best.": "Kód ISO, napríklad <code>en</code> alebo <code>sk</code>. Nechajte prázdne a server jazyk rozpozná sám, čo whisper vie najlepšie.",
        "Test dictation": "Otestovať diktovanie",
        "Read aloud": "Čítanie nahlas",
        "Where <code>/v1/audio/speech</code> lives — Kokoro-FastAPI, OpenAI, or anything else speaking it.": "Kde sa nachádza <code>/v1/audio/speech</code> — Kokoro-FastAPI, OpenAI alebo čokoľvek iné, čo ho podporuje.",
        "Could not read the voice list: %(error)s": "Zoznam hlasov sa nepodarilo načítať: %(error)s",
        "Read from the endpoint. Save and test to refresh it.": "Načítané z endpointu. Obnovíte ho uložením a otestovaním.",
        "Test speech": "Otestovať reč",
        "Any endpoint that speaks the OpenAI HTTP API: OpenAI itself, or a local runner such as LM Studio, vLLM, llama.cpp or Ollama. %(brand)s asks each one for its model list and offers those models in the chat picker.": "Akýkoľvek endpoint, ktorý podporuje HTTP API OpenAI: samotné OpenAI alebo lokálny spúšťač ako LM Studio, vLLM, llama.cpp či Ollama. %(brand)s si od každého vypýta zoznam modelov a ponúkne ich vo výbere modelu v konverzácii.",
        "With or without the trailing <code>/v1</code> — both are accepted.": "S koncovým <code>/v1</code> aj bez neho — prijme sa oboje.",
        "Add and load models": "Pridať a načítať modely",
        "Configured connections": "Nastavené spojenia",
        "Extraction and search": "Extrakcia a vyhľadávanie",
        "One extension per line, <code>.env</code> or <code>env</code>. Only needed to pick a media type — whether the bytes decode as text is what actually decides, so an unlisted extension already works.": "Jedna prípona na riadok, <code>.env</code> alebo <code>env</code>. Treba ju len na výber typu média — v skutočnosti rozhoduje, či sa bajty dajú dekódovať ako text, takže aj neuvedená prípona už funguje.",
        "Keyword search finds a document that uses your words. This finds one that means the same thing — <em>\"how do I get in\"</em> reaching a note about passwords. Both run and the two rankings are fused, so nothing that keyword search found is lost.": "Vyhľadávanie podľa kľúčových slov nájde dokument, ktorý používa vaše slová. Toto nájde taký, ktorý znamená to isté — <em>„ako sa dostanem dnu“</em> nájde poznámku o heslách. Bežia obe a ich poradia sa zlúčia, takže nič, čo našlo vyhľadávanie podľa kľúčových slov, sa nestratí.",
        "No model is marked as producing embeddings. Tick <strong>%(capability)s</strong> on one under <a href=\"/admin/models\">Models</a> — usually a small dedicated model such as <code>nomic-embed-text</code> or <code>bge-m3</code>, not a chat model.": "Žiadny model nie je označený ako tvorca vnorení. Zaškrtnite <strong>%(capability)s</strong> pri jednom z nich v časti <a href=\"/admin/models\">Modely</a> — zvyčajne malom vyhradenom modeli ako <code>nomic-embed-text</code> alebo <code>bge-m3</code>, nie pri konverzačnom modeli.",
        "<code>%(model)s</code> is selected but is not available — its model or its connection has gone, or it is no longer marked for embeddings. Search has fallen back to keywords.": "<code>%(model)s</code> je vybraný, ale nie je dostupný — jeho model alebo spojenie zmizlo, alebo už nie je označený pre vnorenia. Vyhľadávanie sa vrátilo ku kľúčovým slovám.",
        "<strong>%(none)s</strong> is not a degraded mode: it is the keyword search this has always had, with nothing stored and nothing sent anywhere. Changing the model changes what a vector means, so anything already indexed is ignored until it is rebuilt.": "<strong>%(none)s</strong> nie je obmedzený režim: je to vyhľadávanie podľa kľúčových slov, aké tu bolo vždy, nič sa neukladá a nikam neposiela. Zmena modelu mení, čo vektor znamená, takže všetko už zaindexované sa ignoruje, kým sa index neprestavia.",
        "This is the interface only. Models answer in whatever language you write to them in, which is a line you can edit under <a href=\"/admin/prompts\">Prompts</a>.": "Týka sa to len rozhrania. Modely odpovedajú v jazyku, ktorým im píšete — to je riadok, ktorý môžete upraviť v časti <a href=\"/admin/prompts\">Pokyny</a>.",
        "Percent of the model's context length. <code>0</code> turns automatic compaction off; the button in each chat still works. Nothing happens for a model whose context length is unset under <a href=\"/admin/models\">Models</a> — that is \"unknown\", not \"small\", and this will not act on a number nobody supplied. The wording of the summary is under <a href=\"/admin/prompts\">Prompts</a>.": "Percento dĺžky kontextu modelu. <code>0</code> automatické zhrnutie vypne; tlačidlo v každej konverzácii funguje ďalej. Pri modeli, ktorý nemá v časti <a href=\"/admin/models\">Modely</a> nastavenú dĺžku kontextu, sa nestane nič — to znamená „neznáma“, nie „malá“, a podľa čísla, ktoré nikto nezadal, sa nekoná. Znenie zhrnutia je v časti <a href=\"/admin/prompts\">Pokyny</a>.",
        "Several tools can be called in one round, so this is not a count of tools. Leave room for at least two: <code>knowledge_get</code> and <code>note_view</code> read a document by an id a <em>search</em> returned, so a ceiling of one leaves the library searchable and not readable. <code>0</code> means no ceiling, which is how an agent chat already works — those are bounded under <a href=\"/admin/agents\">Agents</a> by time and tokens instead.": "V jednom kole sa dá zavolať niekoľko nástrojov, takže to nie je počet nástrojov. Nechajte priestor aspoň na dve: <code>knowledge_get</code> a <code>note_view</code> čítajú dokument podľa ID, ktoré vrátilo <em>vyhľadávanie</em>, takže strop jedna nechá knižnicu prehľadávateľnú, ale nečitateľnú. <code>0</code> znamená bez stropu, tak už funguje konverzácia s agentom — tie sú namiesto toho obmedzené časom a tokenmi v časti <a href=\"/admin/agents\">Agenti</a>.",
        "All groups": "Všetky skupiny",
        "A group only ever <em>adds</em>. Anything already in the baseline is shown below as such, so a tick here that changes nothing looks like one.": "Skupina vždy len <em>pridáva</em>. Čokoľvek, čo už je v základe, je nižšie tak aj označené, aby zaškrtnutie, ktoré nič nemení, tak aj vyzeralo.",
        "Resolved across a person's groups by <strong>maximum</strong> — the union rule applied to numbers, so a second group can only grant more.": "Naprieč skupinami človeka sa určuje podľa <strong>maxima</strong> — pravidlo zjednotenia použité na čísla, takže ďalšia skupina môže len pridať.",
        "<strong>Leave a box empty</strong> for “no opinion”, and use <strong>0</strong> for “no limit”, which beats any number another group sets. Administrators are unlimited whatever is here.": "<strong>Prázdne pole</strong> znamená „bez názoru“ a <strong>0</strong> znamená „bez limitu“, čo prebije akékoľvek číslo z inej skupiny. Správcovia sú neobmedzení bez ohľadu na to, čo je tu.",
        "Delete %(name)s? Its members keep their accounts.": "Zmazať %(name)s? Členovia si účty ponechajú.",
        "Delete this group": "Zmazať túto skupinu",
        "Permissions are a <strong>union</strong>: everyone starts with the baseline below, and each group they belong to can add more. A group never takes something away, so being in a second group can only widen what someone can do — which is what keeps “why can this person not do X?” answerable without simulating every group they are in. Administrators bypass all of it.": "Oprávnenia sú <strong>zjednotenie</strong>: každý začína so základom nižšie a každá skupina, do ktorej patrí, môže pridať ďalšie. Skupina nikdy nič neodoberie, takže členstvo v ďalšej skupine môže len rozšíriť, čo kto smie — vďaka tomu sa dá na otázku „prečo tento človek nemôže urobiť X?“ odpovedať bez simulovania všetkých jeho skupín. Správcov sa nič z toho netýka.",
        "1 member": "1 člen",
        "%(n)s members": "členovia: %(n)s",
        "+1 permission": "+1 oprávnenie",
        "+%(n)s permissions": "oprávnenia: +%(n)s",
        "Create group": "Vytvoriť skupinu",
        "Who may use it is a permission — <code>tools.image</code> under Groups &amp; permissions. Which models may is a checkbox on each model.": "Kto ho smie používať, určuje oprávnenie — <code>tools.image</code> v časti Skupiny a oprávnenia. Ktoré modely, určuje začiarkavacie políčko pri každom modeli.",
        "Currently <code>%(masked)s</code>. Leave the dots alone to keep it, or clear the field to remove it.": "Teraz <code>%(masked)s</code>. Bodky nechajte tak, ak ho chcete ponechať, alebo pole vymažte a odstráni sa.",
        "Only needed if something sits in front of ComfyUI. Encrypted at rest.": "Treba ho len vtedy, ak je niečo pred ComfyUI. Uložený zašifrovaný.",
        "Test & read what it has": "Otestovať a načítať, čo má",
        "%(name)s (not in this ComfyUI's list)": "%(name)s (nie je v zozname tohto ComfyUI)",
        "Press <strong>%(button)s</strong> above to fill the sampler and scheduler lists from your ComfyUI.": "Stlačte <strong>%(button)s</strong> vyššie a zoznamy vzorkovačov a plánovačov sa naplnia z vášho ComfyUI.",
        "Both are on, so every retry costs two model loads — one to review, one to generate again. That is the slow combination; consider two attempts rather than %(n)s.": "Obe sú zapnuté, takže každý ďalší pokus stojí dve načítania modelu — jedno na kontrolu, jedno na nové generovanie. To je pomalá kombinácia; zvážte dva pokusy namiesto %(n)s.",
        "Added to what every model is told about image generation, on this instance. Where “always put <em>%(example)s</em> in the negative prompt” lives. Leave it empty and nothing is added at all.": "Pridá sa k tomu, čo sa každý model v tejto inštancii dozvie o generovaní obrázkov. Sem patrí „do negatívneho pokynu vždy dajte <em>%(example)s</em>“. Ak to necháte prázdne, nepridá sa nič.",
        "A workflow is a ComfyUI graph exported with <strong>%(menu)s</strong>, with <code>&lbrace;&lbrace;prompt&rbrace;&rbrace;</code> and the other placeholders where the values go. The model picks between them by their descriptions, so write the description for a reader who cannot see the graph.": "Pracovný postup je graf ComfyUI exportovaný cez <strong>%(menu)s</strong>, s <code>&lbrace;&lbrace;prompt&rbrace;&rbrace;</code> a ďalšími zástupnými symbolmi tam, kam patria hodnoty. Model si medzi nimi vyberá podľa popisov, takže popis píšte pre čitateľa, ktorý graf nevidí.",
        "Add a workflow": "Pridať pracovný postup",
        "Remote servers speaking the Model Context Protocol over HTTP. Their tools are offered beside the built-in ones to models marked <strong>%(mcp)s</strong>. The list of tools is discovered and cached — press <strong>%(button)s</strong> after adding one, and again whenever the server changes.": "Vzdialené servery, ktoré hovoria protokolom Model Context Protocol cez HTTP. Ich nástroje sa ponúkajú popri vstavaných modelom označeným <strong>%(mcp)s</strong>. Zoznam nástrojov sa zisťuje a ukladá do vyrovnávacej pamäte — po pridaní servera stlačte <strong>%(button)s</strong> a znova vždy, keď sa server zmení.",
        "Add a server": "Pridať server",
        "New server": "Nový server",
        "New MCP server": "Nový server MCP",
        "Last contacted unsuccessfully: %(error)s": "Posledný pokus o spojenie zlyhal: %(error)s",
        "Prefixed onto every tool name this server offers, so that two servers both exposing <code>search</code> do not collide.": "Pridáva sa pred názov každého nástroja, ktorý tento server ponúka, aby sa dva servery, ktoré oba ponúkajú <code>search</code>, nezrazili.",
        "One <code>Name: value</code> per line.": "Jedna dvojica <code>Name: value</code> na riadok.",
        "Encrypted at rest and never shown again.": "Uložený zašifrovaný a už sa nikdy nezobrazí.",
        "%(name)s on the server": "%(name)s na serveri",
        "Nothing discovered yet. Save, then press <strong>%(button)s</strong> on the <a href=\"/admin/mcp\">list</a>.": "Zatiaľ nič nezistené. Uložte a potom v <a href=\"/admin/mcp\">zozname</a> stlačte <strong>%(button)s</strong>.",
        "Added to the system message whenever any tool from this server is offered. One piece of guidance for the server, not one per tool — the tools carry their own descriptions.": "Pridáva sa do systémovej správy vždy, keď sa ponúka niektorý nástroj z tohto servera. Jedno vedenie pre celý server, nie pre každý nástroj zvlášť — nástroje majú vlastné popisy.",
        "<strong>Someone has overridden this wording under <a href=\"/admin/prompts\">Prompts</a></strong> — that is what the model sees, not this.": "<strong>Toto znenie niekto prepísal v časti <a href=\"/admin/prompts\">Pokyny</a></strong> — model vidí to, nie toto.",
        "Tick the second only for a server on your own network. It is what stops this being aimed at %(brand)s itself, a router, or a metadata endpoint.": "Druhú možnosť zaškrtnite len pre server vo vlastnej sieti. Práve ona bráni tomu, aby sa to dalo namieriť na samotný %(brand)s, na router alebo na endpoint s metadátami.",
        "No groups yet — <a href=\"/admin/groups\">create one</a> to restrict access.": "Zatiaľ žiadne skupiny — ak chcete obmedziť prístup, <a href=\"/admin/groups\">vytvorte nejakú</a>.",
        "Add server": "Pridať server",
        "Back to all servers": "Späť na všetky servery",
        "Delete the server “%(name)s”? Chats that used its tools keep their transcripts.": "Zmazať server „%(name)s“? Konverzácie, ktoré používali jeho nástroje, si svoj prepis ponechajú.",
        "%(n)s of %(total)s": "%(n)s z %(total)s",
        "via %(connection)s": "cez %(connection)s",
        "default model": "predvolený model",
        "Make default": "Nastaviť ako predvolený",
        "Upload image": "Nahrať obrázok",
        "Place in the picker, 1–%(total)s. Typing a number is the workable way to move a model a long distance.": "Miesto vo výbere, 1–%(total)s. Napísať číslo je rozumný spôsob, ako presunúť model ďaleko.",
        "Detect from the endpoint": "Zistiť z endpointu",
        "The vocabulary is <strong>not the same for every model</strong>, and sending one a model does not know is not ignored — it is rendered into the model's chat template, which raises and fails the whole reply. gpt-oss takes <span class=\"mono\">low/medium/high</span>; Bonsai takes <span class=\"mono\">low/medium/xhigh</span> and refuses <span class=\"mono\">high</span>; OpenAI has added <span class=\"mono\">minimal</span>, <span class=\"mono\">xhigh</span> and <span class=\"mono\">max</span> at various points.": "Slovník <strong>nie je rovnaký pre každý model</strong> a hodnota, ktorú model nepozná, sa neignoruje — dostane sa do šablóny konverzácie modelu, tá vyhodí chybu a zlyhá celá odpoveď. gpt-oss berie <span class=\"mono\">low/medium/high</span>; Bonsai berie <span class=\"mono\">low/medium/xhigh</span> a odmieta <span class=\"mono\">high</span>; OpenAI postupne pridalo <span class=\"mono\">minimal</span>, <span class=\"mono\">xhigh</span> a <span class=\"mono\">max</span>.",
        "Tick none and the common three are offered, which is right for almost everything. If an endpoint ever refuses one anyway, that reply is retried without it and this list corrects itself — so this is worth setting by hand only to save that one round trip.": "Ak nezaškrtnete nič, ponúknu sa bežné tri, čo takmer vždy stačí. Ak endpoint niektorú hodnotu predsa odmietne, odpoveď sa zopakuje bez nej a tento zoznam sa sám opraví — ručne to teda oplatí nastaviť len na ušetrenie tej jednej cesty tam a späť.",
        "A <em>seed</em>, not a per-request setting: it is copied onto a chat when the chat is created and when somebody switches to this model, and from then on the chat's own value is what is sent. Changing it here therefore does nothing to chats that already exist. The composer's picker shows whichever level is actually in force, so what somebody sees there is what goes out. Anyone can change it per chat with <span class=\"mono\">/effort</span>, and the control only appears on a model marked <strong>Reasoning</strong> above.": "Ide o <em>počiatočnú hodnotu</em>, nie o nastavenie pre každú požiadavku: skopíruje sa do konverzácie, keď sa konverzácia vytvorí a keď na tento model niekto prepne, a odvtedy sa posiela vlastná hodnota konverzácie. Zmena tu preto na existujúce konverzácie nemá vplyv. Výber v poli na písanie ukazuje úroveň, ktorá naozaj platí, takže čo tam niekto vidí, to sa aj odošle. Ktokoľvek ju môže zmeniť pre konkrétnu konverzáciu cez <span class=\"mono\">/effort</span> a ovládanie sa zobrazí len pri modeli, ktorý má vyššie označené <strong>Uvažovanie</strong>.",
        "Sent two ways at once, because there is no one field that works: OpenAI and vLLM read <span class=\"mono\">reasoning_effort</span>, while llama.cpp drops it silently and reads only <span class=\"mono\">chat_template_kwargs</span> — which is the route by which it reaches gpt-oss. Both go out, and only on a chat that has an effort set, so an endpoint strict about unknown parameters is untouched until somebody chooses one.": "Posiela sa dvoma spôsobmi naraz, lebo neexistuje jedno pole, ktoré by fungovalo všade: OpenAI a vLLM čítajú <span class=\"mono\">reasoning_effort</span>, kým llama.cpp ho potichu zahodí a číta len <span class=\"mono\">chat_template_kwargs</span> — práve touto cestou sa hodnota dostane ku gpt-oss. Odchádzajú obe, a len pri konverzácii, ktorá má úsilie nastavené, takže endpoint prísny na neznáme parametre zostane nedotknutý, kým si niekto nejaké nevyberie.",
        "Shown in the chat settings panel and your users' settings — and, if any model here may ask another one a question, read by those models too.": "Zobrazuje sa v paneli nastavení konverzácie a v nastaveniach vašich používateľov — a ak tu niektorý model smie položiť otázku inému, čítajú ho aj tieto modely.",
        "No instance prompt is set.": "Pokyn instancie nie je nastavený.",
        "Leave empty to fall back to the instance prompt shown above.": "Nechajte prázdne a použije sa pokyn instancie uvedený vyššie.",
        "Leave empty to send no system prompt.": "Nechajte prázdne a neodošle sa žiadny systémový pokyn.",
        "What this endpoint can do. Endpoints rarely advertise it reliably, so it is your call. <strong>reasoning</strong> shows the thinking block, <strong>vision</strong> lets images be sent, and <strong>tools</strong> is whether a tool list may be sent at all — turn it on for a model that does not support tool calling and every one of its replies fails. <strong>embeddings</strong> is the odd one out: it says this is not a chat model at all, but one that turns text into vectors, and it is what <a href=\"/admin/extraction\">Extraction</a> picks from.": "Čo tento endpoint dokáže. Endpointy to spoľahlivo hlásia len zriedka, takže rozhodnutie je na vás. <strong>reasoning</strong> zobrazí blok uvažovania, <strong>vision</strong> dovolí posielať obrázky a <strong>tools</strong> určuje, či sa vôbec smie poslať zoznam nástrojov — zapnite ho pri modeli, ktorý volanie nástrojov nepodporuje, a zlyhá každá jeho odpoveď. <strong>embeddings</strong> je výnimka: hovorí, že nejde o konverzačný model, ale o model, ktorý mení text na vektory, a práve z nich vyberá <a href=\"/admin/extraction\">Extrakcia</a>.",
        "Built-in tools": "Vstavané nástroje",
        "What this model is given, as opposed to what it is capable of. None of it applies unless <strong>tools</strong> is ticked above. Each one is also subject to the instance being configured for it and to the reader's own permissions — this only decides whether it is offered to <em>this</em> model.": "Čo tento model dostane, na rozdiel od toho, čoho je schopný. Nič z toho neplatí, kým vyššie nie je zaškrtnuté <strong>tools</strong>. Každý nástroj navyše závisí od toho, či je preň instancia nastavená, a od oprávnení samotného používateľa — toto rozhoduje len o tom, či sa ponúkne <em>tomuto</em> modelu.",
        "Back to all models": "Späť na všetky modely",
        "Every model discovered across your connections. The order here is the order users see in the picker. <strong>Pinning</strong> does not reorder anything — it puts a shortcut in the chat sidebar. The <strong>default</strong> is what a new chat starts with.": "Všetky modely nájdené vo vašich spojeniach. Poradie tu je poradie, v akom ich používatelia vidia vo výbere. <strong>Pripnutie</strong> poradie nemení — pridá skratku do bočného panela konverzácií. <strong>Predvolený</strong> model je ten, s ktorým začína nová konverzácia.",
        "No models yet. <a href=\"/admin/connections\">Add a connection</a> and run “%(button)s”.": "Zatiaľ žiadne modely. <a href=\"/admin/connections\">Pridajte spojenie</a> a spustite „%(button)s“.",
        "Clear": "Vymazať",
        "Select %(name)s": "Vybrať %(name)s",
        "Showing %(first)s–%(last)s of %(matched)s": "Zobrazené %(first)s–%(last)s z %(matched)s",
        "(filtered from %(total)s)": "(vyfiltrované z %(total)s)",
        "Page %(page)s of %(pages)s": "Strana %(page)s z %(pages)s",
        "Everything %(brand)s puts in front of a model on its own: what day it is, how to use each tool, what it has been asked to remember. These sit above whichever system prompt was authored for the instance, the model or the chat — that prompt still wins where the two disagree. Clear a box to leave that piece out altogether; press <strong>%(use_default)s</strong> to put the built-in wording back.": "Všetko, čo %(brand)s sám predkladá modelu: aký je deň, ako používať jednotlivé nástroje, čo si má pamätať. Tieto časti stoja nad systémovým pokynom napísaným pre instanciu, model alebo konverzáciu — ak si odporujú, vyhráva stále ten pokyn. Vymazaním poľa danú časť úplne vynecháte; stlačením <strong>%(use_default)s</strong> vrátite vstavané znenie.",
        "Everything above is cut off past this. <code>0</code> means the built-in %(default)s. It is a backstop against a large skill index or memory list quietly eating the context window, not a budget to tune.": "Všetko vyššie sa za touto hranicou odreže. <code>0</code> znamená vstavaných %(default)s. Je to poistka proti tomu, aby veľký index schopností alebo zoznam spomienok potichu zjedol kontextové okno, nie rozpočet na ladenie.",
        "Write these in double braces. Anything in double braces that is not on this list is left exactly as you typed it — variable names are lowercase letters, digits and underscores, so <code>{\"total\": 1}</code> and <code>${PATH}</code> are never mistaken for one. A variable with nothing in it takes its whole line with it, so a section never appears empty.": "Píšu sa do dvojitých zložených zátvoriek. Čokoľvek v dvojitých zložených zátvorkách, čo nie je v tomto zozname, zostane presne tak, ako ste to napísali — názvy premenných sú malé písmená, číslice a podčiarkovníky, takže <code>{\"total\": 1}</code> ani <code>${PATH}</code> sa za premennú nikdy nepovažujú. Prázdna premenná so sebou vezme celý svoj riadok, takže sekcia sa nikdy nezobrazí prázdna.",
        "Only reaches the prompt with <code>agent</code> ticked below. The machine, directory and plan shown there are samples.": "Do pokynu sa dostane, len keď je nižšie zaškrtnuté <code>agent</code>. Stroj, priečinok a plán, ktoré sa tam zobrazia, sú ukážkové.",
        "Refresh": "Obnoviť",
        "A <strong>schedule</strong> runs a piece of work because time has passed rather than because somebody asked just now — a daily summary, a check every Monday, a reminder in an hour. Each one has its own chat and replies into it, or files a report. People make their own under <strong>%(scheduled)s</strong>; what you decide here is whether the feature exists and what it may spend.": "<strong>Plán</strong> spustí prácu preto, že uplynul čas, nie preto, že o ňu niekto práve požiadal — denné zhrnutie, kontrola každý pondelok, pripomienka o hodinu. Každý má vlastnú konverzáciu a odpovedá do nej, alebo založí správu. Ľudia si ich vytvárajú sami v časti <strong>%(scheduled)s</strong>; vy tu rozhodujete, či táto funkcia existuje a koľko smie minúť.",
        "This is the one thing here that spends model time with nobody watching. A schedule pointed at a chat that can run commands would run them unattended, so a scheduled task is never an agent chat — but everything else a model can reach, it can reach on a timer. Give <strong>%(permission)s</strong> under %(groups)s to the people who should have it; it is off for everybody by default.": "Toto je jediná vec tu, ktorá míňa čas modelu bez toho, aby sa niekto díval. Plán namierený na konverzáciu, ktorá smie spúšťať príkazy, by ich spúšťal bez dozoru, preto naplánovaná úloha nikdy nie je agentská konverzácia — ale všetko ostatné, na čo model dosiahne, dosiahne aj podľa časovača. Oprávnenie <strong>%(permission)s</strong> v časti %(groups)s dajte ľuďom, ktorí ho majú mať; predvolene ho nemá nikto.",
        "Off by default. Turning this off stops everything firing and deletes nothing — schedules keep their place and resume when it is turned back on.": "Predvolene vypnuté. Vypnutím sa zastaví všetko spúšťanie a nič sa nezmaže — plány zostanú, kde sú, a po opätovnom zapnutí pokračujú.",
        "There is 1 schedule on this instance, %(active)s of them not paused.": "Na tejto instancii je 1 plán, nepozastavené: %(active)s.",
        "There are %(total)s schedules on this instance, %(active)s of them not paused.": "Plány na tejto instancii: %(total)s, z toho nepozastavené: %(active)s.",
        "Who may use it is a permission — <code>tools.web_search</code> under %(groups)s.": "Kto ho smie používať, určuje oprávnenie — <code>tools.web_search</code> v časti %(groups)s.",
        "<code>wt-wt</code> is no region at all. <code>uk-en</code>, <code>de-de</code> and so on bias results to a country.": "<code>wt-wt</code> znamená bez regiónu. <code>uk-en</code>, <code>de-de</code> a podobne zvýhodnia výsledky z danej krajiny.",
        "A stock SearXNG refuses JSON. Add <code>- json</code> under <code>search.formats</code> in its <code>settings.yml</code> and restart it, or every search will fail with that message.": "Neupravený SearXNG odmieta JSON. Pridajte <code>- json</code> pod <code>search.formats</code> v jeho <code>settings.yml</code> a reštartujte ho, inak každé vyhľadávanie zlyhá s touto správou.",
        "Applies to the composer's <strong>%(link)s</strong> option and to anything the model fetches: %(brand)s retrieves the page and keeps its text.": "Platí pre možnosť <strong>%(link)s</strong> v poli na písanie a pre všetko, čo si stiahne model: %(brand)s stránku načíta a uchová jej text.",
        "Offers the <code>web_fetch</code> tool, so a model can read an address it found in a search result or was given. Turning this off leaves the composer's Link option working: that one is somebody's instruction, while this is the model choosing an address — possibly one it read in a page it had just fetched.": "Ponúka nástroj <code>web_fetch</code>, takže model si môže prečítať adresu, ktorú našiel vo výsledku vyhľadávania alebo ktorú dostal. Vypnutím tejto možnosti voľba Odkaz v poli na písanie funguje ďalej: tá je pokynom človeka, kým toto je model, ktorý si adresu vyberá sám — možno takú, ktorú si prečítal na stránke, ktorú práve stiahol.",
        "Off by default, and worth leaving off. This server can reach your router, your other services and %(brand)s itself; the address to fetch can come from a model, which can be talked into things by a web page it just read. Turn this on only if you actually want to archive pages from your own network.": "Predvolene vypnuté a oplatí sa to tak nechať. Tento server dosiahne na váš router, vaše ostatné služby aj na samotný %(brand)s; adresa na stiahnutie môže prísť od modelu, ktorého môže k všeličomu nahovoriť webová stránka, ktorú si práve prečítal. Zapnite to, len ak naozaj chcete archivovať stránky z vlastnej siete.",
        "Runs a real search against the <em>saved</em> settings, which is what a chat would do. Save first if you have just changed something.": "Spustí skutočné vyhľadávanie s <em>uloženými</em> nastaveniami, presne ako konverzácia. Ak ste práve niečo zmenili, najprv uložte.",
        "Search": "Hľadať",
        "Cards on the new-chat screen. Clicking one sends its prompt straight away, so each has to work on its own — write one that asks for whatever it needs, and the first reply becomes the right question rather than a guess. The first %(n)s enabled ones are shown, in this order.": "Karty na obrazovke novej konverzácie. Kliknutím sa ich pokyn hneď odošle, takže každá musí fungovať sama — napíšte taký, ktorý si vypýta všetko, čo potrebuje, a prvá odpoveď bude správna otázka namiesto hádania. Zobrazujú sa prvé zapnuté (%(n)s) v tomto poradí.",
        "Only the first %(n)s are shown": "Zobrazuje sa len prvých %(n)s",
        "Delete the suggestion “%(name)s”?": "Zmazať návrh „%(name)s“?",
        "%(n)s is the limit. Delete one to add another — past a dozen this is a menu, and a menu on the empty screen is a worse blank page than a blank page.": "Limit je %(n)s. Ak chcete pridať ďalší, jeden zmažte — nad tucet je to už ponuka a ponuka na prázdnej obrazovke je horšia prázdna stránka než prázdna stránka.",
        "New tool": "Nový nástroj",
        "The name the model calls, and the key its guidance is stored under. Lowercase letters, digits, hyphens and underscores.": "Názov, ktorý model volá, a kľúč, pod ktorým je uložené jeho vedenie. Malé písmená, číslice, spojovníky a podčiarkovníky.",
        "Changing it starts its guidance afresh.": "Jeho zmenou začne vedenie odznova.",
        "A JSON Schema object. Each property becomes a <code>{{name}}</code> you can use below, and a <code>description</code> on each one is worth writing — the model reads those too.": "Objekt JSON Schema. Z každej vlastnosti sa stane <code>{{name}}</code>, ktoré môžete použiť nižšie, a oplatí sa ku každej napísať <code>description</code> — model ich číta tiež.",
        "One <code>Name: value</code> per line. Placeholders work here too.": "Jedna dvojica <code>Name: value</code> na riadok. Zástupné hodnoty fungujú aj tu.",
        "Encrypted at rest and never shown again. It is dropped if the endpoint redirects to another host.": "Uložený zašifrovaný a už sa nikdy nezobrazí. Ak endpoint presmeruje na iný stroj, neodošle sa.",
        "Added to the system message whenever this tool is offered, and nowhere else. Start a line with <code>- </code> and it joins the list of tool instructions. Optional: the description above is what makes the tool usable, this is for habits.": "Pridáva sa do systémovej správy vždy, keď sa tento nástroj ponúka, a nikde inde. Riadok začatý <code>- </code> sa zaradí do zoznamu pokynov k nástrojom. Nepovinné: použiteľným robí nástroj popis vyššie, toto je pre zvyky.",
        "Create tool": "Vytvoriť nástroj",
        "Back to all tools": "Späť na všetky nástroje",
        "Delete the tool “%(name)s”? Chats that used it keep their transcripts.": "Zmazať nástroj „%(name)s“? Konverzácie, ktoré ho používali, si svoj prepis ponechajú.",
        "Calls the <em>saved</em> tool once, so what you see here is what a chat would get. Nothing is sent to a model.": "Raz zavolá <em>uložený</em> nástroj, takže tu vidíte to, čo by dostala konverzácia. Modelu sa nič neposiela.",
        "Run once": "Spustiť raz",
        "HTTP calls a model can make while it answers. Each one is offered to models marked <strong>%(custom)s</strong>, to people who have the permission, and — if it is restricted — only to the groups you choose. The model decides <em>when</em> to call it from the description you write, so write that as if explaining to a colleague what the tool is for.": "Volania HTTP, ktoré môže model urobiť počas odpovede. Každé sa ponúka modelom označeným <strong>%(custom)s</strong>, ľuďom, ktorí majú oprávnenie, a — ak je obmedzené — len skupinám, ktoré vyberiete. <em>Kedy</em> ho zavolať, model rozhoduje podľa popisu, ktorý napíšete, preto ho píšte, akoby ste kolegovi vysvetľovali, na čo nástroj slúži.",
        "No tools yet. <a href=\"/admin/tools/new\">Define one</a> — a name, a description, and a URL with <code>{{placeholders}}</code> in it.": "Zatiaľ žiadne nástroje. <a href=\"/admin/tools/new\">Definujte nejaký</a> — názov, popis a URL s <code>{{placeholders}}</code>.",
        "This host follows the <strong>stable</strong> channel — the newest release tag.": "Tento stroj sleduje kanál <strong>stable</strong> — najnovšiu značku vydania.",
        "This host follows the <strong>edge</strong> channel — the tip of <code>%(branch)s</code>, which is whatever was pushed most recently and may be half finished.": "Tento stroj sleduje kanál <strong>edge</strong> — špičku vetvy <code>%(branch)s</code>, teda čokoľvek, čo bolo naposledy odoslané, a môže to byť napoly hotové.",
        "Checking reaches the remote; opening this page does not.": "Kontrola sa spája so vzdialeným repozitárom; otvorenie tejto stránky nie.",
        "reports %(version)s": "hlási %(version)s",
        "This is tagged <strong>%(tag)s</strong> but reports version <strong>%(version)s</strong>. A tag cut before the version bump names a release nobody can identify afterwards.": "Toto je označené <strong>%(tag)s</strong>, ale hlási verziu <strong>%(version)s</strong>. Značka vytvorená pred zvýšením verzie pomenúva vydanie, ktoré potom nikto nedokáže určiť.",
        "Check the remote": "Skontrolovať vzdialený repozitár",
        "Available": "Dostupné",
        "1 commit behind": "pozadu o 1 commit",
        "%(n)s commits behind": "pozadu o commity: %(n)s",
        "Nothing on <code>%(branch)s</code> has been tagged as a release yet, so the stable channel has nothing to offer. Switch this host to <code>edge</code> in <code>lembas.env</code> to follow the branch instead.": "Nič na <code>%(branch)s</code> zatiaľ nebolo označené ako vydanie, takže stabilný kanál nemá čo ponúknuť. Ak má tento stroj namiesto toho sledovať vetvu, prepnite ho v <code>lembas.env</code> na <code>edge</code>.",
        "<strong>%(label)s</strong> is what is running.": "Beží <strong>%(label)s</strong>.",
        "1 commit between": "1 commit medzi nimi",
        "%(n)s commits between": "commity medzi nimi: %(n)s",
        "This page follows <strong>%(channel)s</strong> but the update helper on this host was installed for <strong>%(helper)s</strong> — so the button would deploy the second, not the first. They are set in two places (<code>lembas.env</code> and the systemd unit); re-run the installer with <code>INSTALL_UPDATE_HELPER=1</code> and the channel you want, which writes both.": "Táto stránka sleduje <strong>%(channel)s</strong>, ale pomocník aktualizácií na tomto stroji bol nainštalovaný pre <strong>%(helper)s</strong> — tlačidlo by teda nasadilo to druhé, nie to prvé. Nastavujú sa na dvoch miestach (<code>lembas.env</code> a jednotka systemd); spustite inštalátor znova s <code>INSTALL_UPDATE_HELPER=1</code> a želaným kanálom, ktorý zapíše oboje.",
        "This host has the update helper installed, so the button below writes a request that a systemd unit picks up and runs as root. It always deploys the <strong>%(channel)s</strong> channel — the request carries no ref and no channel, so pressing it is never “deploy something else”.": "Tento stroj má nainštalovaného pomocníka aktualizácií, takže tlačidlo nižšie zapíše požiadavku, ktorú prevezme jednotka systemd a spustí ju ako root. Vždy nasadí kanál <strong>%(channel)s</strong> — požiadavka nenesie referenciu ani kanál, takže jeho stlačenie nikdy neznamená „nasaď niečo iné“.",
        "The update helper is <strong>not installed on this host</strong>, so there is no button. That is the honest default: the service runs as an unprivileged account and cannot restart itself, and a web application that <em>can</em> is one whose worst day is much worse. Run this instead: <code>%(command)s</code>": "Pomocník aktualizácií <strong>na tomto stroji nie je nainštalovaný</strong>, preto tu nie je tlačidlo. To je poctivé predvolené nastavenie: služba beží pod neprivilegovaným účtom a sama sa reštartovať nedokáže, a webová aplikácia, ktorá to <em>dokáže</em>, má najhorší deň oveľa horší. Namiesto toho spustite: <code>%(command)s</code>",
        "To install it, re-run the installer with <code>INSTALL_UPDATE_HELPER=1</code>. Doing so means anybody who can administer this web interface can deploy the <code>%(channel)s</code> channel — which is the point, and worth deciding on purpose rather than arriving at.": "Nainštalujete ho opätovným spustením inštalátora s <code>INSTALL_UPDATE_HELPER=1</code>. Znamená to, že každý, kto smie spravovať toto webové rozhranie, môže nasadiť kanál <code>%(channel)s</code> — o to presne ide, a oplatí sa o tom rozhodnúť zámerne, nie sa k tomu len dopracovať.",
        "All users": "Všetci používatelia",
        "Read-only, and deliberately: every switch here is set somewhere else — in the <a href=\"/admin/groups\">baseline</a> or in a named group — and a control on this page would be a third place to change one thing. What it adds is the <em>source</em>, which is the question the grids could not answer without opening every group by eye.": "Len na čítanie, a zámerne: každý prepínač tu sa nastavuje inde — v <a href=\"/admin/groups\">základe</a> alebo v pomenovanej skupine — a ovládanie na tejto stránke by bolo tretím miestom na zmenu jednej veci. Pridáva však <em>zdroj</em>, teda odpoveď, ktorú mriežky nedokázali dať bez toho, aby ste otvorili každú skupinu a pozreli sa.",
        "from %(sources)s": "z %(sources)s",
        "%(used)s of %(limit)s": "%(used)s z %(limit)s",
        "%(prompt)s prompt, %(written)s written": "vstup: %(prompt)s, napísané: %(written)s",
        "%(month)s this month, %(today)s today": "tento mesiac: %(month)s, dnes: %(today)s",
        "limit %(n)s a day": "limit %(n)s denne",
        "Resolved across their groups by <strong>maximum</strong> — the union rule applied to numbers, so a second group can only ever grant more. Zero means no limit and wins outright, because a group saying “unlimited” must not count for less than one saying “a million”.": "Naprieč ich skupinami sa určuje podľa <strong>maxima</strong> — pravidlo zjednotenia použité na čísla, takže ďalšia skupina môže vždy len pridať. Nula znamená bez limitu a vyhráva hneď, lebo skupina, ktorá hovorí „neobmedzene“, nesmie mať menšiu váhu než skupina, ktorá hovorí „milión“.",
        "no limit": "bez limitu",
        "Delete %(email)s? Their chats, folders and library go with them.": "Zmazať %(email)s? Spolu s účtom zmiznú aj jeho konverzácie, priečinky a knižnica.",
        "Delete this account": "Zmazať tento účet",
        "Every account on this instance. Open one to see what it can actually do and where each of those permissions came from. Group membership is edited from the <a href=\"/admin/groups\">group's</a> own page — one control per value, so a save on one screen cannot undo a save on another.": "Všetky účty na tejto instancii. Otvorte niektorý a uvidíte, čo naozaj smie a odkiaľ každé z týchto oprávnení pochádza. Členstvo v skupine sa upravuje na vlastnej stránke <a href=\"/admin/groups\">skupiny</a> — jedno ovládanie pre každú hodnotu, takže uloženie na jednej obrazovke nemôže zrušiť uloženie na inej.",
        "Nobody matches that.": "Tomu nikto nezodpovedá.",
        "No accounts yet.": "Zatiaľ žiadne účty.",
        "%(n)s accounts": "účty: %(n)s",
        "Create account": "Vytvoriť účet",
        "New workflow": "Nový pracovný postup",
        "The only thing the model has to choose with, so say what this is <em>for</em> rather than what it contains. It never sees the graph.": "Je to jediné, podľa čoho si model vyberá, preto napíšte, <em>na čo</em> postup slúži, nie čo obsahuje. Graf model nikdy nevidí.",
        "Export this from ComfyUI with <strong>Export (API)</strong>, not Save — the two formats are different and only the API one can be submitted.": "Exportujte ho z ComfyUI cez <strong>Export (API)</strong>, nie cez Save — formáty sa líšia a odoslať sa dá len ten pre API.",
        "Then put a placeholder where each value goes. A placeholder that is the <em>whole</em> value keeps its type, so <code>\"steps\": {{steps}}</code> sends the number 20 rather than the text “20”; one inside a longer string is substituted as text, so <code>\"{{prompt}}, masterpiece\"</code> works. Anything you leave out takes its default.": "Potom na miesto každej hodnoty dajte zástupnú hodnotu. Zástupná hodnota, ktorá tvorí <em>celú</em> hodnotu, si zachová typ, takže <code>\"steps\": {{steps}}</code> pošle číslo 20, nie text „20“; zástupná hodnota vnútri dlhšieho reťazca sa dosadí ako text, takže <code>\"{{prompt}}, masterpiece\"</code> funguje. Čo vynecháte, dostane predvolenú hodnotu.",
        "Every hole a template may carry, what it fills, and what it resolves to right now. <code>{{prompt}}</code> is required — without it every image would be the same one, whatever anybody typed.": "Každé miesto, ktoré šablóna môže obsahovať, čo vypĺňa a na čo sa práve teraz vyhodnotí. <code>{{prompt}}</code> je povinné — bez neho by bol každý obrázok rovnaký, nech by ktokoľvek napísal čokoľvek.",
        "Two of these are not spelled the way ComfyUI spells them, which is the mistake that costs an afternoon: <code>{{model}}</code> fills <code>ckpt_name</code> and <code>{{sampler}}</code> fills <code>sampler_name</code>.": "Dve z nich sa nepíšu tak ako v ComfyUI, a to je chyba, ktorá stojí celé popoludnie: <code>{{model}}</code> vypĺňa <code>ckpt_name</code> a <code>{{sampler}}</code> vypĺňa <code>sampler_name</code>.",
        "The right-hand column is what an omitted placeholder resolves to today — the instance defaults from the image settings page, falling back to the built-in floor. A model may override any of them except <code>{{batch}}</code>, which is yours alone.": "Pravý stĺpec ukazuje, na čo sa vynechaná zástupná hodnota dnes vyhodnotí — predvoľby instancie zo stránky nastavení obrázkov, a ak chýbajú, vstavané minimum. Model môže ktorúkoľvek z nich prepísať okrem <code>{{batch}}</code>, ktoré patrí len vám.",
        "Add workflow": "Pridať pracovný postup",
        "Back": "Späť",
        "Delete the workflow “%(name)s”? Chats that used it fall back to the default.": "Zmazať pracovný postup „%(name)s“? Konverzácie, ktoré ho používali, prejdú na predvolený.",
        "%(n)s page": "%(n)s strana",
        "%(n)s pages": "strany: %(n)s",
        "truncated": "skrátené",
        "Remove %(name)s": "Odstrániť %(name)s",
        "<strong>%(title)s</strong> changed after you opened it, so nothing was written. Your version is below.": "<strong>%(title)s</strong> sa od otvorenia zmenil, preto sa nič nezapísalo. Vaša verzia je nižšie.",
        "Overwrite with mine": "Prepísať mojou",
        "This does not look like text, so there is nothing to show and nothing that could safely be saved back.": "Toto nevyzerá ako text, takže tu nie je čo zobraziť ani čo bezpečne uložiť späť.",
        "Showing the beginning only. Saving from here would delete the rest, so this one is read-only.": "Zobrazuje sa len začiatok. Uložením odtiaľto by sa zvyšok zmazal, preto je tento súbor len na čítanie.",
        "Scratch": "Pracovný dokument",
        "Close %(name)s": "Zavrieť %(name)s",
        "Rewind here? The %(n)s message after this one will be deleted.": "Vrátiť sa sem? %(n)s správa po tejto sa zmaže.",
        "Rewind here? The %(n)s messages after this one will be deleted.": "Vrátiť sa sem? Správy po tejto sa zmažú (počet: %(n)s).",
        "Send again": "Poslať znova",
        "%(n)s later message will be discarded.": "%(n)s neskoršia správa sa zahodí.",
        "%(n)s later messages will be discarded.": "Neskoršie správy sa zahodia (počet: %(n)s).",
        "Inspector": "Prehliadka",
        "Context": "Kontext",
        "%(n)s tokens": "tokeny: %(n)s",
        "not set — <a href=\"/admin/models/%(id)s/edit\">set it</a>": "nenastavené — <a href=\"/admin/models/%(id)s/edit\">nastaviť</a>",
        "not set": "nenastavené",
        "Lifetime": "Životnosť",
        "temporary": "dočasná",
        "%(sent)s in, %(received)s out": "%(sent)s dnu, %(received)s von",
        "not reported": "nehlásené",
        "Elapsed": "Trvanie",
        "Tool calls": "Volania nástrojov",
        "Rounds": "Kolá",
        "State": "Stav",
        "Answer": "Odpovedať",
        "Send answers": "Poslať odpovede",
        "All of them at once. Leave any blank to skip it.": "Všetky naraz. Ktorúkoľvek nechajte prázdnu, ak ju chcete preskočiť.",
        "Leave any blank to skip it.": "Ak chcete otázku preskočiť, nechajte ju prázdnu.",
        "It says: %(purpose)s": "Model uvádza: %(purpose)s",
        "Allow": "Povoliť",
        "Don't": "Nepovoliť",
        "Give reason": "Uviesť dôvod",
        "Don't, and tell it why": "Nepovoliť a povedať prečo",
        "Nothing matches “%(q)s”.": "Výrazu „%(q)s“ nič nezodpovedá.",
        "Your library is empty. <a href=\"/library/knowledge\">Add something to it</a>.": "Vaša knižnica je prázdna. <a href=\"/library/knowledge\">Pridajte do nej niečo</a>.",
        "%(n)sp": "%(n)s s.",
        "Nothing to mention yet.": "Zatiaľ tu nie je čo spomenúť.",
        "view extracted text": "zobraziť vytiahnutý text",
        "Stopped. This reply is cut short.": "Zastavené. Táto odpoveď je neúplná.",
        "Waiting to be sent": "Čaká na odoslanie",
        "Estimated.": "Odhad.",
        "What the conversation now occupies: %(n)s of %(limit)s tokens (%(percent)s%%)": "Koľko konverzácia teraz zaberá: %(n)s z %(limit)s tokenov (%(percent)s %%)",
        "What the conversation now occupies: %(n)s tokens; the window's size is not known": "Koľko konverzácia teraz zaberá: tokeny: %(n)s; veľkosť okna nie je známa",
        "in context": "v kontexte",
        "Estimated: this endpoint reports no token counts.": "Odhad: tento endpoint nehlási počty tokenov.",
        "What this reply cost: %(sent)s in, %(received)s out, the prompt paid for once in each of %(n)s rounds of tool calls": "Čo táto odpoveď stála: %(sent)s dnu, %(received)s von; prompt sa zaplatil raz v každom kole volaní nástrojov (počet kôl: %(n)s)",
        "What this reply cost: %(sent)s in, %(received)s out": "Čo táto odpoveď stála: %(sent)s dnu, %(received)s von",
        "%(n)s processed": "spracované: %(n)s",
        "The reply took %(total)s s, %(thinking)s s of it thinking": "Odpoveď trvala %(total)s s, z toho %(thinking)s s premýšľanie",
        "The reply took %(total)s s": "Odpoveď trvala %(total)s s",
        "thought %(time)s": "premýšľal %(time)s",
        "%(n)s tokens in %(ms)s ms": "tokeny: %(n)s za %(ms)s ms",
        "Switch this chat to Edit mode and carry out this plan? Every command will still be shown to you before it runs.": "Prepnúť túto konverzáciu do režimu Edit a vykonať tento plán? Každý príkaz vám bude aj tak ukázaný predtým, než sa spustí.",
        "Carry out this plan": "Vykonať tento plán",
        "Thought %(label)s": "Premýšľal %(label)s",
        "Thought": "Premýšľal",
        "%(n)s earlier messages, summarised": "Staršie správy, zhrnuté: %(n)s",
        "Web search failed": "Vyhľadávanie na webe zlyhalo",
        "Searched the web for “%(query)s”": "Hľadal na webe „%(query)s“",
        "Searched the web": "Hľadal na webe",
        "%(tool)s failed": "%(tool)s: zlyhalo",
        "%(n)s result": "%(n)s výsledok",
        "%(n)s results": "výsledky: %(n)s",
        "%(n)s tokens — %(percent)s%% of %(limit)s": "tokeny: %(n)s — %(percent)s %% z %(limit)s",
        "Nothing yet.": "Zatiaľ nič.",
        "Not recorded for %(model)s, so there is no percentage and this chat will never compact itself.": "Pre %(model)s nie je zaznamenaná, takže tu nie je percento a táto konverzácia sa nikdy sama nezhrnie.",
        "Not recorded for this model, so there is no percentage and this chat will never compact itself.": "Pre tento model nie je zaznamenaná, takže tu nie je percento a táto konverzácia sa nikdy sama nezhrnie.",
        "Set it under <a href=\"/admin/models\">Models</a>.": "Nastavte ju v časti <a href=\"/admin/models\">Modely</a>.",
        "%(n)s tokens across %(replies)s reply": "tokeny: %(n)s v %(replies)s odpovedi",
        "%(n)s tokens across %(replies)s replies": "tokeny: %(n)s, odpovede: %(replies)s",
        "%(n)s tokens/second": "%(n)s tokenov/s",
        "Page %(page)s of %(pages)s · %(n)s items": "Strana %(page)s z %(pages)s · položky: %(n)s",
        "Nobody matches “%(q)s”.": "Výrazu „%(q)s“ nikto nezodpovedá.",
        "There is nobody else here yet.": "Zatiaľ tu nikto iný nie je.",
        "All knowledge bases": "Všetky znalostné bázy",
        "Nothing found": "Nič sa nenašlo",
        "Nothing in this base matches “%(q)s”.": "Výrazu „%(q)s“ v tejto báze nič nezodpovedá.",
        "Add a file or a web page above.": "Pridajte vyššie súbor alebo webovú stránku.",
        "Delete “%(name)s” and the %(n)s document(s) in it? Messages they were attached to keep their copies.": "Zmazať „%(name)s“ a dokumenty v nej (počet: %(n)s)? Správy, ku ktorým boli priložené, si ponechajú svoje kópie.",
        "Delete base": "Zmazať bázu",
        "All bases": "Všetky bázy",
        "Shared with me": "Zdieľané so mnou",
        "%(n)s document": "%(n)s dokument",
        "%(n)s documents": "dokumenty: %(n)s",
        "Back to %(name)s": "Späť na %(name)s",
        "Back to the base": "Späť na bázu",
        "Source": "Zdroj",
        "Size": "Veľkosť",
        "%(n)s characters": "znaky: %(n)s",
        "Download": "Stiahnuť",
        "Delete “%(name)s”? Messages it was already attached to keep their copy.": "Zmazať „%(name)s“? Správy, ku ktorým už bol priložený, si ponechajú svoju kópiu.",
        "New note": "Nová poznámka",
        "All notes": "Všetky poznámky",
        "Written by a model. Edit it freely — it is yours.": "Napísal ju model. Pokojne ju upravte — je vaša.",
        "Create note": "Vytvoriť poznámku",
        "Delete “%(name)s”?": "Zmazať „%(name)s“?",
        "No notes yet": "Zatiaľ žiadne poznámky",
        "No note matches “%(q)s”.": "Výrazu „%(q)s“ nezodpovedá žiadna poznámka.",
        "Give a model the notes tool and it will start keeping them, or write the first one yourself.": "Dajte modelu nástroj na poznámky a začne si ich viesť, alebo prvú napíšte sami.",
        "New skill": "Nová schopnosť",
        "All skills": "Všetky schopnosti",
        "This one line is all a model sees until it opens the skill, so it has to say when the skill applies — not what it does.": "Tento jeden riadok je všetko, čo model vidí, kým schopnosť neotvorí, takže musí hovoriť, kedy sa schopnosť použije — nie čo robí.",
        "Worth reading before you rely on it. A model that has just read a web page can be talked into things by that page, and a skill persists. Every earlier version is below.": "Oplatí sa ju prečítať skôr, než sa na ňu budete spoliehať. Model, ktorý práve čítal webovú stránku, sa ňou môže dať k všeličomu nahovoriť, a schopnosť pretrváva. Všetky staršie verzie sú nižšie.",
        "Create skill": "Vytvoriť schopnosť",
        "Delete the skill “%(name)s” and all its history?": "Zmazať schopnosť „%(name)s“ aj s celou históriou?",
        "History": "História",
        "No skills yet": "Zatiaľ žiadne schopnosti",
        "No skill matches “%(q)s”.": "Výrazu „%(q)s“ nezodpovedá žiadna schopnosť.",
        "Write one for a task you explain often, or let a model save one after it has worked the task out.": "Napíšte ju pre úlohu, ktorú často vysvetľujete, alebo nechajte model, aby si ju uložil, keď úlohu vyrieši.",
        "All reports": "Všetky správy",
        "Filed <time datetime=\"%(iso)s\">%(when)s</time> by a schedule": "Založená <time datetime=\"%(iso)s\">%(when)s</time> plánom",
        "Filed <time datetime=\"%(iso)s\">%(when)s</time> from a chat": "Založená <time datetime=\"%(iso)s\">%(when)s</time> z konverzácie",
        "Filed <time datetime=\"%(iso)s\">%(when)s</time>": "Založená <time datetime=\"%(iso)s\">%(when)s</time>",
        "No reports yet": "Zatiaľ žiadne správy",
        "No report matches “%(q)s”.": "Výrazu „%(q)s“ nezodpovedá žiadna správa.",
        "Ask a model to write one up when it has finished looking into something, and it will appear here.": "Požiadajte model, aby ju spísal, keď niečo dopreskúma, a objaví sa tu.",
        "%d %b %Y, %H:%M": "%d. %m. %Y, %H:%M",
        "Leave the date empty for a repeating schedule and it begins now. Times are %(timezone)s.": "Pri opakovanom pláne nechajte dátum prázdny a začne hneď. Časy sú v pásme %(timezone)s.",
        "Paused.": "Pozastavené.",
        "Next run %(when)s.": "Ďalšie spustenie: %(when)s.",
        "Nothing left to run.": "Už nie je čo spustiť.",
        "Run %(n)s times so far.": "Doteraz spustené %(n)s-krát.",
        "Files a report each time.": "Zakaždým založí správu.",
        "Run now": "Spustiť teraz",
        "Pause": "Pozastaviť",
        "Resume": "Pokračovať",
        "%a %-d %b, %H:%M": "%A %-d. %B, %H:%M",
        "Open its chat": "Otvoriť jeho konverzáciu",
        "Remove schedule": "Odstrániť plán",
        "New scheduled task": "Nová naplánovaná úloha",
        "next %(when)s": "ďalšie: %(when)s",
        "run %(n)s times": "spustené %(n)s-krát",
        "What do you want to schedule?": "Čo chcete naplánovať?",
        "Times are read as %(timezone)s. Say how often, and what should happen.": "Časy sa čítajú v pásme %(timezone)s. Povedzte, ako často a čo sa má stať.",
        "Work it out": "Vyhodnotiť",
        "This will run <strong>%(summary)s</strong>. Check it below and change anything that is not what you meant.": "Toto pobeží <strong>%(summary)s</strong>. Skontrolujte to nižšie a zmeňte všetko, čo nie je tak, ako ste mysleli.",
        "Schedule it": "Naplánovať",
        "Set up %(name)s": "Nastavenie %(name)s",
        "Create administrator account": "Vytvoriť účet správcu",
        "Back to your chats": "Späť na vaše konverzácie",
        "Offline": "Offline",
        "%(name)s answers from your server, so there is nothing to read until it can be reached again.": "%(name)s odpovedá z vášho servera, takže kým nebude opäť dostupný, nie je tu čo čítať.",
        "That connection no longer exists.": "To spojenie už neexistuje.",
        "The base URL must start with http:// or https://": "Základná URL musí začínať na http:// alebo https://",
        "%(name)s: found 1 model.": "%(name)s: nájdený 1 model.",
        "%(name)s: found %(count)s models.": "%(name)s: nájdené modely: %(count)s.",
        "Spoke the test phrase: %(size)s bytes of audio.": "Testovacia veta vyslovená: zvuk s veľkosťou %(size)s bajtov.",
        "Heard \"%(text)s\".": "Počuté: „%(text)s“.",
        "Heard nothing, as expected.": "Nebolo nič počuť, ako sa očakávalo.",
        "The endpoint answered. %(heard)s": "Endpoint odpovedal. %(heard)s",
        "Unknown endpoint.": "Neznámy endpoint.",
        "No such data group.": "Taká dátová oblasť neexistuje.",
        "A data group needs a name.": "Dátová oblasť potrebuje názov.",
        "Extraction saved.": "Extrakcia uložená.",
        "Search saved.": "Hľadanie uložené.",
        "Search saved. Rebuild the index to use the new model.": "Hľadanie uložené. Aby sa použil nový model, znova zostavte index.",
        "Set a base URL first.": "Najprv nastavte základnú URL.",
        "Found 1 checkpoint, %(samplers)s samplers and %(schedulers)s schedulers.": "Nájdený 1 checkpoint; vzorkovače: %(samplers)s, plánovače: %(schedulers)s.",
        "Found %(checkpoints)s checkpoints, %(samplers)s samplers and %(schedulers)s schedulers.": "Nájdené checkpointy: %(checkpoints)s, vzorkovače: %(samplers)s, plánovače: %(schedulers)s.",
        "That workflow no longer exists.": "Ten pracovný postup už neexistuje.",
        "A workflow needs a name.": "Pracovný postup potrebuje názov.",
        "The name the model uses must be lowercase letters, digits, hyphens or underscores, and start with a letter or digit.": "Názov, ktorý používa model, smie obsahovať len malé písmená, číslice, spojovníky a podčiarkovníky a musí začínať písmenom alebo číslicou.",
        "There is already a workflow called “%(slug)s”.": "Pracovný postup s názvom „%(slug)s“ už existuje.",
        "Paste the workflow, in ComfyUI's API format.": "Vložte pracovný postup vo formáte API ComfyUI.",
        "That is not valid JSON: %(error)s": "Toto nie je platný JSON: %(error)s",
        "A ComfyUI API workflow is a JSON object keyed by node id. Use “Export (API)” in ComfyUI rather than “Save”.": "Pracovný postup API ComfyUI je objekt JSON s kľúčmi podľa id uzlov. V ComfyUI použite „Export (API)“, nie „Save“.",
        "The workflow never uses %(placeholder)s, so every image would be the same. Put it where the text prompt goes.": "Pracovný postup nikde nepoužíva %(placeholder)s, takže každý obrázok by bol rovnaký. Vložte ho tam, kam patrí textové zadanie.",
        "Unknown placeholder %(placeholder)s.": "Neznáma zástupná hodnota %(placeholder)s.",
        "Added %(name)s.": "Pridané: %(name)s.",
        "Deleted %(name)s.": "Zmazané: %(name)s.",
        "Saved %(name)s.": "Uložené: %(name)s.",
        "Fetch a page": "Načítanie stránky",
        "Ask the reader": "Otázky čitateľovi",
        "That model no longer exists.": "Ten model už neexistuje.",
        "All": "Všetky",
        "Enabled": "Zapnuté",
        "Disabled": "Vypnuté",
        "Pinned": "Pripnuté",
        "Restricted": "Obmedzené",
        "%(n)s model(s) updated.": "Aktualizované modely: %(n)s.",
        "This model's template accepts: %(efforts)s.": "Šablóna tohto modelu prijíma: %(efforts)s.",
        "The endpoint gave up its chat template, but nothing in it names a set of reasoning efforts. Either this model does not take one, or it accepts anything and never checks.": "Endpoint poskytol svoju šablónu konverzácie, no nič v nej neuvádza súbor náročností uvažovania. Tento model ju teda buď neprijíma, alebo prijme čokoľvek a nič nekontroluje.",
        "This endpoint does not publish its chat template, so there is nothing to read. llama.cpp does; OpenAI and vLLM do not.": "Tento endpoint nezverejňuje svoju šablónu konverzácie, takže nie je čo čítať. llama.cpp ju zverejňuje, OpenAI a vLLM nie.",
        "Now the default model.": "Teraz je to predvolený model.",
        "Image updated.": "Obrázok aktualizovaný.",
        "Image removed.": "Obrázok odstránený.",
        "No such image.": "Taký obrázok neexistuje.",
        "An ordinary chat": "Bežná konverzácia",
        "A scheduled task, running unattended": "Naplánovaná úloha, ktorá beží bez dozoru",
        "A helper sent by another model": "Pomocník, ktorého poslal iný model",
        "%(provider)s returned 1 result.": "%(provider)s vrátil 1 výsledok.",
        "%(provider)s returned %(n)s results.": "%(provider)s – výsledky: %(n)s.",
        "That suggestion no longer exists.": "Ten návrh už neexistuje.",
        "A suggestion needs a name.": "Návrh potrebuje názov.",
        "That is already %(n)s, which is plenty.": "Návrhov je už %(n)s, a to stačí.",
        "Text — HTML reduced to prose": "Text — HTML zredukované na súvislý text",
        "JSON — parsed, narrowed by the path below": "JSON — spracovaný a zúžený cestou nižšie",
        "Raw — exactly as it arrived": "Surové — presne tak, ako prišlo",
        "None — this endpoint needs no credential": "Žiadne — tento endpoint nepotrebuje prístupový údaj",
        "Bearer token in a header": "Token Bearer v hlavičke",
        "The header named below, verbatim": "Hlavička uvedená nižšie, doslovne",
        "A query parameter named below": "Parameter dopytu uvedený nižšie",
        "That tool no longer exists.": "Ten nástroj už neexistuje.",
        "A tool needs a name.": "Nástroj potrebuje názov.",
        "The identifier must be lowercase letters, digits, hyphens or underscores, start with a letter or digit, and be at most 48 characters. It is the name the model calls.": "Identifikátor smie obsahovať len malé písmená, číslice, spojovníky a podčiarkovníky, musí začínať písmenom alebo číslicou a mať najviac 48 znakov. Pod týmto názvom ho volá model.",
        "“%(slug)s” is the name of a built-in tool. Choose another.": "„%(slug)s“ je názov vstavaného nástroja. Zvoľte iný.",
        "There is already a tool called “%(slug)s”.": "Nástroj s názvom „%(slug)s“ už existuje.",
        "%(method)s is not a method this can send.": "Metódu %(method)s toto odoslať nevie.",
        "The parameters are not valid JSON: %(error)s": "Parametre nie sú platný JSON: %(error)s",
        "The parameters must be a JSON object whose \"type\" is \"object\".": "Parametre musia byť objekt JSON, ktorého \"type\" je \"object\".",
        "Arguments must be a JSON object.": "Argumenty musia byť objekt JSON.",
        "Those arguments are not a JSON object: %(error)s": "Tieto argumenty nie sú objekt JSON: %(error)s",
        "That server no longer exists.": "Ten server už neexistuje.",
        "A server needs a name.": "Server potrebuje názov.",
        "The identifier must be lowercase letters, digits, hyphens or underscores, and at most 24 characters. It prefixes every tool name this server offers.": "Identifikátor smie obsahovať len malé písmená, číslice, spojovníky a podčiarkovníky a mať najviac 24 znakov. Stojí pred názvom každého nástroja, ktorý tento server ponúka.",
        "There is already a server called “%(slug)s”.": "Server s názvom „%(slug)s“ už existuje.",
        "Added %(name)s, but it could not be reached: %(error)s": "%(name)s pridaný, ale nedá sa k nemu pripojiť: %(error)s",
        "Added %(name)s — %(count)s tool(s).": "%(name)s pridaný — nástroje: %(count)s.",
        "%(name)s: found 1 tool.": "%(name)s: nájdený 1 nástroj.",
        "%(name)s: found %(count)s tools.": "%(name)s: nájdené nástroje: %(count)s.",
        "The update helper is not installed on this host.": "Pomocník aktualizácií nie je na tomto hostiteľovi nainštalovaný.",
        "Request withdrawn.": "Žiadosť stiahnutá.",
        "That user no longer exists.": "Ten používateľ už neexistuje.",
        "That group no longer exists.": "Tá skupina už neexistuje.",
        "That email is already registered.": "Tento e-mail je už zaregistrovaný.",
        "Created %(email)s.": "Vytvorené: %(email)s.",
        "That is the only administrator. Promote someone else first.": "Toto je jediný správca. Najprv povýšte niekoho iného.",
        "Saved %(email)s.": "Uložené: %(email)s.",
        "Password reset. Sessions revoked.": "Heslo obnovené. Prihlásenia zrušené.",
        "You cannot delete your own account.": "Vlastný účet zmazať nemôžete.",
        "That is the only administrator.": "Toto je jediný správca.",
        "Deleted %(email)s.": "Zmazané: %(email)s.",
        "A group needs a name.": "Skupina potrebuje názov.",
        "A group with that name already exists.": "Skupina s týmto názvom už existuje.",
        "Created %(name)s.": "Vytvorené: %(name)s.",
        "Default permissions saved.": "Predvolené oprávnenia uložené.",
        "Dictation is not enabled on this instance.": "Diktovanie nie je na tejto inštancii zapnuté.",
        "That recording is too long.": "Táto nahrávka je príliš dlhá.",
        "The recording was empty.": "Nahrávka bola prázdna.",
        "Read-aloud is not enabled on this instance.": "Čítanie nahlas nie je na tejto inštancii zapnuté.",
        "There is nothing to read out.": "Nie je čo prečítať.",
        "That chat no longer exists.": "Táto konverzácia už neexistuje.",
        "That message no longer exists.": "Táto správa už neexistuje.",
        "Too many failed attempts. Try again in %(seconds)s seconds.": "Príliš veľa neúspešných pokusov. Skúste to znova o %(seconds)s s.",
        "That email and password do not match.": "E-mail a heslo sa nezhodujú.",
        "This account has been deactivated. Ask an administrator.": "Tento účet bol deaktivovaný. Obráťte sa na správcu.",
        "Registration is closed. Ask an administrator for an account.": "Registrácia je uzavretá. O účet požiadajte správcu.",
        "Please enter a name.": "Zadajte meno.",
        "Please enter a valid email address.": "Zadajte platnú e-mailovú adresu.",
        "An account with that email already exists.": "Účet s týmto e-mailom už existuje.",
        "That could not be opened.": "Nepodarilo sa to otvoriť.",
        "That could not be saved.": "Nepodarilo sa to uložiť.",
        "This chat runs on a device, which keeps its own history. Send a new message instead.": "Táto konverzácia beží na zariadení, ktoré si vedie vlastnú históriu. Pošlite radšej novú správu.",
        "The inspector is restricted to administrators.": "Prehliadka požiadavky je vyhradená správcom.",
        "Wait for the current reply to finish, then compact.": "Počkajte, kým sa dopíše aktuálna odpoveď, a potom zhrňte.",
        "There is nothing here to summarise yet.": "Zatiaľ tu nie je čo zhrnúť.",
        "Not compacted: %(error)s": "Nezhrnuté: %(error)s",
        "The device returned no summary, so nothing changed.": "Zariadenie nevrátilo žiadne zhrnutie, takže sa nič nezmenilo.",
        "Compaction is turned off: its prompt is empty under Admin → Prompts.": "Zhrnutie je vypnuté: jeho pokyn je v Správa → Pokyny prázdny.",
        "This chat has no model that can summarise it -- pick one in the composer first.": "Táto konverzácia nemá model, ktorý by ju zhrnul -- najprv ho vyberte v editore správy.",
        "Could not compact: %(error)s": "Zhrnutie sa nepodarilo: %(error)s",
        "The model returned no summary, so nothing changed.": "Model nevrátil žiadne zhrnutie, takže sa nič nezmenilo.",
        "You may not use the library.": "Knižnicu nesmiete používať.",
        "That knowledge base is not available.": "Táto znalostná báza nie je dostupná.",
        "Say what to turn on or off.": "Uveďte, čo zapnúť alebo vypnúť.",
        "Too many things switched off.": "Vypnutých je príliš veľa vecí.",
        "You already have 1 reply being written, which is this account's limit. Wait for one to finish.": "Už sa vám píše 1 odpoveď, čo je limit tohto účtu. Počkajte, kým sa dopíše.",
        "You already have %(n)s replies being written, which is this account's limit. Wait for one to finish.": "Odpovede, ktoré sa vám práve píšu: %(n)s – to je limit tohto účtu. Počkajte, kým sa niektorá dopíše.",
        "There are already %(n)s messages waiting to be sent.": "Na odoslanie už čakajú správy: %(n)s.",
        "A background job's message cannot be edited.": "Správu úlohy na pozadí nemožno upraviť.",
        "A message cannot be empty.": "Správa nemôže byť prázdna.",
        "Wait for the current reply to finish, or stop it.": "Počkajte, kým sa aktuálna odpoveď dopíše, alebo ju zastavte.",
        "There is no plan on that message.": "Pri tejto správe nie je žiadny plán.",
        "That message is not waiting to be sent.": "Táto správa nečaká na odoslanie.",
        "That question is no longer waiting for an answer.": "Táto otázka už nečaká na odpoveď.",
        "This chat will not ask about 1 more action. Clear that from the menu beside the composer.": "Táto konverzácia sa už nebude pýtať na 1 ďalšiu akciu. Zrušiť to môžete v ponuke vedľa editora správy.",
        "This chat will not ask about %(n)s more actions. Clear that from the menu beside the composer.": "Ďalšie akcie, na ktoré sa táto konverzácia už nebude pýtať: %(n)s. Zrušiť to môžete v ponuke vedľa editora správy.",
        "Allowed once. A command line that runs more than one thing cannot be stored as a rule, so this chat will ask again.": "Povolené raz. Príkazový riadok, ktorý spúšťa viac vecí naraz, nemožno uložiť ako pravidlo, takže sa táto konverzácia opýta znova.",
        "A chat's connection is fixed when it is created. Start a new chat to work somewhere else.": "Spojenie konverzácie sa určí pri jej vytvorení. Ak chcete pracovať inde, začnite novú konverzáciu.",
        "You may not change the model for a chat.": "Model konverzácie nesmiete meniť.",
        "That model is in another data group than this chat (%(group)s), so it cannot be given this chat's history. Start a new chat with it instead.": "Tento model je v inej dátovej oblasti ako táto konverzácia (%(group)s), takže mu nemožno odovzdať jej históriu. Začnite s ním radšej novú konverzáciu.",
        "That model is not available to you.": "Tento model vám nie je dostupný.",
        "You may not set a system prompt.": "Systémový pokyn nesmiete nastavovať.",
        "You may not change sampling parameters.": "Parametre vzorkovania nesmiete meniť.",
        "That reply no longer exists.": "Táto odpoveď už neexistuje.",
        "This area is restricted to administrators.": "Táto časť je vyhradená správcom.",
        "You do not have permission to do that.": "Na to nemáte oprávnenie.",
        "No such device.": "Také zariadenie neexistuje.",
        "That file no longer exists.": "Tento súbor už neexistuje.",
        "That document is not available.": "Tento dokument nie je dostupný.",
        "That file is part of a sent message.": "Tento súbor patrí k odoslanej správe.",
        "That file is no longer on disk.": "Tento súbor už nie je na disku.",
        "That folder no longer exists.": "Tento priečinok už neexistuje.",
        "Folders cannot be nested more than %(n)s deep.": "Priečinky nemožno vnárať hlbšie ako do úrovne %(n)s.",
        "A folder cannot be moved inside itself.": "Priečinok nemožno presunúť samého do seba.",
        "That base is not yours to change.": "Túto bázu nemáte právo meniť.",
        "That base is not yours to add to.": "Do tejto bázy nemáte právo pridávať.",
        "That document is not yours to change.": "Tento dokument nemáte právo meniť.",
        "That note is not available.": "Táto poznámka nie je dostupná.",
        "That note is not yours to change.": "Túto poznámku nemáte právo meniť.",
        "That skill is not available.": "Táto schopnosť nie je dostupná.",
        "That skill is not yours to change.": "Túto schopnosť nemáte právo meniť.",
        "That revision no longer exists.": "Táto verzia už neexistuje.",
        "Memory added.": "Záznam v pamäti pridaný.",
        "That memory no longer exists.": "Tento záznam v pamäti už neexistuje.",
        "Memory updated.": "Záznam v pamäti aktualizovaný.",
        "Memory removed.": "Záznam v pamäti odstránený.",
        "Fetching pages": "Načítavanie stránok",
        "Your knowledge library": "Vaša knižnica znalostí",
        "Asking you questions": "Otázky pre vás",
        "Writing in the canvas": "Písanie na plochu",
        "Generating images": "Generovanie obrázkov",
        "Filing reports": "Podávanie správ",
        "Scheduling work": "Plánovanie práce",
        "Sending helpers": "Posielanie pomocníkov",
        "Running commands": "Spúšťanie príkazov",
        "You cannot manage folders.": "Priečinky spravovať nemôžete.",
        "Default model updated.": "Predvolený model aktualizovaný.",
        "Audio preferences updated.": "Nastavenia zvuku aktualizované.",
        "Your current password is not correct.": "Vaše súčasné heslo nie je správne.",
        "The new passwords do not match.": "Nové heslá sa nezhodujú.",
        "That is already your password.": "Toto už je vaše heslo.",
        "Password changed. Any other sessions have been signed out.": "Heslo zmenené. Všetky ostatné prihlásenia boli odhlásené.",
        "You may not manage your own data groups.": "Vlastné dátové oblasti spravovať nesmiete.",
        "Data groups updated.": "Dátové oblasti aktualizované.",
        "Data group created.": "Dátová oblasť vytvorená.",
        "Personalization is off here.": "Prispôsobenie je tu vypnuté.",
        "Personalization saved.": "Prispôsobenie uložené.",
        "That report is not available.": "Táto správa nie je dostupná.",
        "Its own chat": "Vlastná konverzácia",
        "There is no model configured to work this out, so fill it in yourself.": "Nie je nastavený žiadny model, ktorý by to zistil, takže to vyplňte sami.",
        "That schedule is not available.": "Tento plán nie je dostupný.",
        "Not a shareable kind.": "Tento druh sa nedá zdieľať.",
        "That is not yours to share.": "Toto nemáte právo zdieľať.",
        "You may not share things.": "Zdieľať nesmiete.",
        "Unknown principal.": "Neznámy príjemca.",
        "The shell exited.": "Shell sa ukončil.",
        "This terminal was closed after sitting idle.": "Tento terminál bol zatvorený po čase nečinnosti.",
        "LLeMbas restarted, so this shell was closed.": "LLeMbas sa reštartoval, takže sa tento shell zatvoril.",
        "The connection behind this terminal was closed.": "Spojenie, na ktorom tento terminál beží, bolo zatvorené.",
        "The connection to the machine was lost.": "Spojenie so strojom sa prerušilo.",
        "You do not have permission to open a terminal.": "Nemáte oprávnenie otvoriť terminál.",
        "This is an ordinary chat, so it has no machine to open a shell on.": "Toto je bežná konverzácia, takže nemá stroj, na ktorom by sa otvoril shell.",
        "The terminal is switched off on this instance.": "Terminál je na tejto inštancii vypnutý.",
        "That device is not yours.": "Toto zariadenie nie je vaše.",
        "%(device)s is not linked right now.": "%(device)s teraz nie je pripojené.",
        "This chat is not on a linked device, so it has no shell to open.": "Táto konverzácia nie je na pripojenom zariadení, takže nemá shell, ktorý by sa dal otvoriť.",
        "The shell could not be started.": "Shell sa nepodarilo spustiť.",
        "Nothing has been run in this shell yet.": "V tomto shelli sa zatiaľ nič nespustilo.",
        "%(kb)s KB": "%(kb)s kB",
        "Reconnecting: output arrived faster than this window could draw it.": "Znova sa pripája: výstup prichádzal rýchlejšie, než ho toto okno stihlo vykresliť.",
        "This terminal closed.": "Tento terminál sa zatvoril.",
        "There is no such token.": "Taký token neexistuje.",
        "Password must be at least %(n)s characters.": "Heslo musí mať aspoň %(n)s znakov.",
        "A terminal opens only on a linked LLeMbas CLI device.": "Terminál sa otvára len na pripojenom zariadení s LLeMbas CLI.",
        "This instance already has as many terminals open as it allows. Close one, or ask an administrator to raise the limit.": "Na tejto inštancii je už otvorených toľko terminálov, koľko povoľuje. Jeden zatvorte alebo požiadajte správcu o zvýšenie limitu.",
        "No audio endpoint has been configured.": "Nie je nastavený žiadny zvukový endpoint.",
        "The transcription endpoint returned no text.": "Endpoint prepisu nevrátil žiadny text.",
        "The endpoint returned a response that was not JSON.": "Endpoint vrátil odpoveď, ktorá nie je JSON.",
        "That note is not there any more.": "Táto poznámka tam už nie je.",
        "That skill is not there any more.": "Táto schopnosť tam už nie je.",
        "That document is not there any more.": "Tento dokument tam už nie je.",
        "That attachment is not there any more.": "Táto príloha tam už nie je.",
        "That attachment belongs to another chat.": "Táto príloha patrí inej konverzácii.",
        "That scratch document belongs to another chat.": "Tento pracovný dokument patrí inej konverzácii.",
        "There is nothing to open here.": "Nie je tu čo otvoriť.",
        "There is nothing to save here.": "Nie je tu čo uložiť.",
        "This one can only be read.": "Tento sa dá len čítať.",
        "You do not have access to the library.": "Nemáte prístup ku knižnici.",
        "This chat has no model selected.": "Táto konverzácia nemá vybraný model.",
        "No enabled connection currently offers the model '%(model)s'. Pick another model for this chat.": "Model „%(model)s“ teraz neponúka žiadne zapnuté spojenie. Vyberte pre túto konverzáciu iný model.",
        "This chat belongs to the data group %(chat_group)s, and its model is now in %(model_group)s, so it cannot be sent this chat's history. Pick a model in %(chat_group)s, or start a new chat.": "Táto konverzácia patrí do dátovej oblasti %(chat_group)s a jej model je teraz v %(model_group)s, takže mu nemožno poslať jej históriu. Vyberte model v %(chat_group)s alebo začnite novú konverzáciu.",
        "The default group cannot be deleted.": "Predvolenú oblasť nemožno zmazať.",
        "%(n)s %(label)s": "%(label)s: %(n)s",
        "The group still holds %(listed)s. Move them out first.": "Oblasť ešte obsahuje %(listed)s. Najprv ich presuňte inam.",
        "This account cannot run agent chats.": "Tento účet nemôže spúšťať agentské konverzácie.",
        "Agent chats are switched off on this instance.": "Agentské konverzácie sú na tejto inštancii vypnuté.",
        "You no longer have permission to run agent chats.": "Už nemáte oprávnenie spúšťať agentské konverzácie.",
        "This chat's device is no longer signed in as you.": "Zariadenie tejto konverzácie už nie je prihlásené za vás.",
        "Taking in what you just added…": "Spracúva sa, čo ste práve doplnili…",
        "The device reported an error.": "Zariadenie hlási chybu.",
        "That image's dimensions are implausibly large.": "Rozmery tohto obrázka sú nepravdepodobne veľké.",
        "That image could not be read. Is it corrupt?": "Tento obrázok sa nedá prečítať. Nie je poškodený?",
        "That file is not text, and is not a format LLeMbas can read.": "Tento súbor nie je text ani formát, ktorý LLeMbas vie prečítať.",
        "That is not an image.": "Toto nie je obrázok.",
        "That file is empty.": "Tento súbor je prázdny.",
        "Files must be under %(mb)s MB.": "Súbory musia mať menej ako %(mb)s MB.",
        "Something went wrong while generating this reply.": "Pri písaní tejto odpovede sa niečo pokazilo.",
        "Summarising earlier messages…": "Zhŕňajú sa staršie správy…",
        "Your reply is ready.": "Vaša odpoveď je hotová.",
        "Waiting for your answer…": "Čaká sa na vašu odpoveď…",
        "Waiting for your answers…": "Čaká sa na vaše odpovede…",
        "Waiting for you to allow %(tool)s…": "Čaká sa, kým povolíte %(tool)s…",
        "Waiting for you to allow %(n)s actions…": "Čaká sa na vaše povolenie, počet akcií: %(n)s…",
        "You already have a knowledge base called %(name)s in another data group. Names are unique across your groups.": "Znalostnú bázu s názvom %(name)s už máte v inej dátovej oblasti. Názvy sú jedinečné naprieč všetkými vašimi oblasťami.",
        "You already have a knowledge base called %(name)s.": "Znalostnú bázu s názvom %(name)s už máte.",
        "No embedding model is configured.": "Nie je nastavený žiadny model embeddingov.",
        "A memory cannot be empty.": "Záznam v pamäti nemôže byť prázdny.",
        "There are already %(n)s memories, which is the limit, so nothing was saved. Do not remove one to make room — you are not shown all of them and would be guessing. Say that the limit has been reached, and put this in a note instead.": "Záznamov v pamäti je už %(n)s, čo je limit, takže sa nič neuložilo. Neodstraňujte žiadny, aby ste urobili miesto — nevidíte všetky a len by ste hádali. Povedzte, že limit je dosiahnutý, a zapíšte to radšej do poznámky.",
        "A skill name must be two or more letters, numbers or hyphens, such as 'weekly-report'.": "Názov schopnosti musí mať aspoň dva znaky z písmen, číslic alebo spojovníkov, napríklad 'weekly-report'.",
        "The name %(slug)s is already used by a skill in another data group. Choose a different name.": "Názov %(slug)s už používa schopnosť v inej dátovej oblasti. Zvoľte iný názov.",
        "A skill called %(slug)s already exists. Edit it instead.": "Schopnosť s názvom %(slug)s už existuje. Upravte radšej ju.",
        "A skill needs a description saying when to use it — it is the only thing shown until the skill is opened.": "Schopnosť potrebuje opis, kedy ju použiť — je to jediné, čo sa zobrazuje, kým sa schopnosť neotvorí.",
        "A reply is still being written in one of your chats. Stop it, or wait for it to finish, and try again.": "V jednej z vašich konverzácií sa ešte píše odpoveď. Zastavte ju alebo počkajte, kým sa dopíše, a skúste to znova.",
        "%(n)s replies are still being written in your chats. Stop them, or wait for them to finish, and try again.": "Odpovede, ktoré sa vo vašich konverzáciách ešte píšu: %(n)s. Zastavte ich alebo počkajte, kým sa dopíšu, a skúste to znova.",
        "Your schedule “%(title)s” is running right now. Try again when it has finished.": "Váš plán „%(title)s“ práve beží. Skúste to znova, keď skončí.",
        "That does not describe a time anything could run at. Say when it should happen — a date and time, or how often.": "Toto neopisuje čas, kedy by sa čokoľvek mohlo spustiť. Povedzte, kedy sa to má stať — dátum a čas alebo ako často.",
        "That schedule has no next run — its time has already passed.": "Tento plán nemá ďalší beh — jeho čas už uplynul.",
        "You already have %(limit)s schedules, which is the most this instance allows. Remove one before adding another.": "Plánov už máte %(limit)s, čo je najviac, koľko táto inštancia povoľuje. Pred pridaním ďalšieho jeden odstráňte.",
        "Updating in a moment — try again shortly.": "O chvíľu sa aktualizuje — skúste to o chvíľu znova.",
        "The backup before the update failed, so nothing was requested: %(error)s": "Záloha pred aktualizáciou zlyhala, takže sa o nič nežiadalo: %(error)s",
        "The file was empty.": "Súbor bol prázdny.",
        "Images must be under %(mb)s MB.": "Obrázky musia mať menej ako %(mb)s MB.",
        "That does not look like a PNG, JPEG, WEBP or GIF image.": "Toto nevyzerá ako obrázok PNG, JPEG, WEBP ani GIF.",
    }
)

# --- 2.2.0: device protocol 2, API-only mode, the CLI's own API ----------------
MESSAGES.update(
    {
        # API-only mode (Admin -> General)
        "Instance mode": "Režim inštancie",
        "What this instance is": "Čím je táto inštancia",
        "Full": "Plná",
        "API only": "Len API",
        "API only switches off chats, agent chats, the library, reports, scheduled tasks and the MCP server; the API, device sign-in, Settings and Admin keep working.": (
            "Režim Len API vypne konverzácie, agentové konverzácie, knižnicu, správy, "
            "naplánované úlohy a server MCP; API, prihlasovanie zariadení, Nastavenia "
            "a Administrácia fungujú ďalej."
        ),
        "This instance serves the API only; chats, the library, reports and scheduled tasks are switched off.": (
            "Táto inštancia slúži len ako API; konverzácie, knižnica, správy a naplánované "
            "úlohy sú vypnuté."
        ),
        # A linked device's files, commands and model
        "On the device": "Na zariadení",
        "on the device": "na zariadení",
        "Switched on the device; this chat sends no model while it runs on this one": (
            "Prepnuté na zariadení; kým beží na tomto modeli, táto konverzácia neposiela žiadny model"
        ),
        "on the device: %(ref)s": "na zariadení: %(ref)s",
        "Attached in the terminal; the file is on the device": (
            "Priložené v termináli; súbor je na zariadení"
        ),
        # A device's question and plan
        "It says:": "Hovorí:",
        "recommended": "odporúčané",
        "The plan": "Plán",
        "How to carry it out": "Ako ho vykonať",
        "Approve, and let it edit": "Schváliť a nechať ho upravovať",
        "Edit mode: it asks only before running commands.": (
            "Režim Edit: pýta sa len pred spustením príkazov."
        ),
        "Approve, and ask me each time": "Schváliť a zakaždým sa ma pýtať",
        "Manual mode: every change waits for you.": "Režim Manual: každá zmena čaká na vás.",
        "Revise": "Prepracovať",
        "What to change": "Čo zmeniť",
        "What to change in the plan…": "Čo v pláne zmeniť…",
        "Send it back": "Vrátiť na prepracovanie",
        # The terminal panel
        "The previous command (Ctrl+Shift+↑)": "Predchádzajúci príkaz (Ctrl+Shift+↑)",
        "The previous command": "Predchádzajúci príkaz",
        "The next command (Ctrl+Shift+↓)": "Nasledujúci príkaz (Ctrl+Shift+↓)",
        "The next command": "Nasledujúci príkaz",
        # Credits and personalization
        "Your credit balance": "Váš zostatok kreditov",
        "Switched off from the LLeMbas CLI: nothing is sent. Saving here switches it on again.": (
            "Vypnuté z LLeMbas CLI: nič sa neposiela. Uložením tu sa znova zapne."
        ),
    }
)

# --- 2.2.0: the sidebar, Settings and the composer rearranged --------------------
MESSAGES.update(
    {
        "Devices": "Zariadenia",
        "Settings → Devices": "Nastavenia → Zariadenia",
        "offline": "odpojené",
        "New agent chat on %(device)s": "Nová agentská konverzácia na zariadení %(device)s",
        "Agent chats run on a machine with the LLeMbas CLI, signed in to this instance and linked. None is linked now.": (
            "Agentské konverzácie bežia na počítači s LLeMbas CLI, ktoré je prihlásené do tejto "
            "instancie a pripojené. Teraz nie je pripojené žiadne."
        ),
        "More settings": "Ďalšie nastavenia",
        "Tools, skills, prompt, knowledge, sampling": "Nástroje, schopnosti, pokyn, znalosti, vzorkovanie",
        "Close chat settings": "Zavrieť nastavenia konverzácie",
        "Switched off here only. What is on to begin with is your default for this model, in Settings → Models.": (
            "Vypnuté len tu. Čo je na začiatku zapnuté, je vaša predvoľba pre tento model "
            "v Nastavenia → Modely."
        ),
        "Your defaults per model": "Vaše predvoľby pre jednotlivé modely",
        "Which tools a model may use with you, and how hard a reasoning model thinks when a chat starts. These apply to you only, can only narrow what this instance allows, and a chat's own settings still win.": (
            "Ktoré nástroje smie model s vami používať a ako intenzívne uvažujúci model premýšľa "
            "na začiatku konverzácie. Platí to len pre vás, môže to len zúžiť, čo táto instancia "
            "dovoľuje, a vlastné nastavenia konverzácie majú stále prednosť."
        ),
        "Unticked ones are not offered to this model when you chat with it. Only what this instance already offers you is listed.": (
            "Neoznačené sa tomuto modelu v konverzáciách s vami neponúknu. Zobrazené je len to, "
            "čo vám táto instancia už ponúka."
        ),
        "This model is offered no tools here.": "Tento model tu nemá ponúknuté žiadne nástroje.",
        "As the instance sets it (%(level)s)": "Podľa instancie (%(level)s)",
        "As the instance sets it (off)": "Podľa instancie (vypnuté)",
        "Where a new chat with this model starts. Change it in the chat for that chat only.": (
            "Od tejto úrovne začne nová konverzácia s týmto modelom. V konverzácii ju zmeníte "
            "len pre ňu."
        ),
        "Saved for %(model)s.": "Uložené pre %(model)s.",
        "%(n)s tools off": "vypnuté nástroje: %(n)s",
        "effort %(level)s": "náročnosť %(level)s",
        "effort off": "náročnosť vypnutá",
        "Your devices: the LLeMbas CLI signed in as you": "Vaše zariadenia: LLeMbas CLI prihlásené za vás",
        "Reports filed for you": "Správy pripravené pre vás",
        "Your scheduled tasks": "Vaše naplánované úlohy",
    }
)

# --- 2.2.0: script strings the comment-pattern bug had hidden ----------------------------------
MESSAGES.update(
    {
        "This instance serves the API only; agent chats are switched off.": "Táto inštancia slúži len ako API; agentské konverzácie sú vypnuté.",
        "Upload failed.": "Nahrávanie zlyhalo.",
        "Attach a web page": "Priložiť webovú stránku",
        "The page is fetched now and its text attached, so it will not change between writing this and sending it.": "Stránka sa stiahne hneď a priloží sa jej text, takže sa medzi písaním a odoslaním nezmení.",
        "Fetch": "Stiahnuť",
        "Attach from your library": "Priložiť z vašej knižnice",
        "Search your documents…": "Hľadať vo vašich dokumentoch…",
        "Search your documents": "Hľadať vo vašich dokumentoch",
        "Close": "Zavrieť",
        "Could not load your library.": "Vašu knižnicu sa nepodarilo načítať.",
        "Where this chat works on the device: its trusted places and anything in them. The device has the last word.": "Kde táto konverzácia pracuje na zariadení: jeho dôveryhodné miesta a čokoľvek v nich. Posledné slovo má zariadenie.",
        "Where this chat starts, and what a relative path is measured from. You can walk anywhere the account can reach.": "Kde sa táto konverzácia začína a od čoho sa počíta relatívna cesta. Prejsť môžete kamkoľvek, kam účet dosiahne.",
        "Path": "Cesta",
        "Use this directory": "Použiť tento adresár",
        "Could not reach that machine.": "Ten stroj sa nepodarilo zastihnúť.",
        "Already installed — you are using the installed app now.": "Už je nainštalovaná — práve používate nainštalovanú aplikáciu.",
        "This page is not a secure context, so the browser will not install it. That means plain http, or https with a certificate this device does not trust — a private or self-signed certificate has to be installed on the device before any browser will treat the site as secure.": "Táto stránka nie je zabezpečený kontext, takže ju prehliadač nenainštaluje. To znamená obyčajné http alebo https s certifikátom, ktorému toto zariadenie nedôveruje — súkromný alebo vlastnoručne podpísaný certifikát treba nainštalovať do zariadenia, kým ktorýkoľvek prehliadač bude stránku považovať za zabezpečenú.",
        "The service worker could not be registered, so the browser will not offer an install. The usual cause is a certificate this device does not trust. The browser said: %(reason)s": "Service worker sa nepodarilo zaregistrovať, takže prehliadač inštaláciu neponúkne. Zvyčajnou príčinou je certifikát, ktorému toto zariadenie nedôveruje. Prehliadač hlási: %(reason)s",
        "This browser does not support installing. On iOS, use Share → Add to Home Screen.": "Tento prehliadač inštaláciu nepodporuje. V iOS použite Zdieľať → Pridať na plochu.",
        "Everything this end is ready and your browser has not offered an install. Some never do — Firefox and desktop Safari — and Chrome will not offer one twice for the same app.": "Na tejto strane je všetko pripravené a váš prehliadač inštaláciu neponúkol. Niektoré to nerobia nikdy — Firefox a Safari na počítači — a Chrome ju pre tú istú aplikáciu neponúkne dvakrát.",
        "A new version is ready. Reload to use it.": "Nová verzia je pripravená. Na jej použitie stránku znova načítajte.",
        "Reload": "Znova načítať",
        "Transcription failed.": "Prepis zlyhal.",
        "Nothing was heard in that recording.": "V tej nahrávke nebolo nič počuť.",
        "The microphone needs HTTPS or localhost. This page is served over plain HTTP, so the browser will not grant it.": "Mikrofón vyžaduje HTTPS alebo localhost. Táto stránka sa podáva cez obyčajné HTTP, takže ho prehliadač nepovolí.",
        "That reply could not be read out.": "Tú odpoveď sa nepodarilo prečítať nahlas.",
        "Summarising the earlier messages…": "Zhŕňam staršie správy…",
        "Could not compact.": "Zhutnenie zlyhalo.",
        "Could not compact this chat.": "Túto konverzáciu sa nepodarilo zhutniť.",
        "No command called “%(name)s”. It will be sent as a message.": "Príkaz „%(name)s“ neexistuje. Odošle sa ako správa.",
        "Commands": "Príkazy",
        "Commands and shortcuts": "Príkazy a skratky",
        "Keyboard": "Klávesnica",
        "A message that starts with a slash but is not a command is sent as written. Type <span class='mono'>//</span> to start one with a literal slash.": "Správa, ktorá začína lomkou, ale nie je príkazom, sa odošle tak, ako je napísaná. Napíšte <span class='mono'>//</span>, ak má začínať doslovnou lomkou.",
        "↑↓ choose · Tab complete · Enter run · Esc dismiss": "↑↓ výber · Tab doplniť · Enter spustiť · Esc zavrieť",
        "↑↓ choose · Enter attach · Esc dismiss": "↑↓ výber · Enter priložiť · Esc zavrieť",
        "Esc dismiss": "Esc zavrieť",
        "Could not attach that.": "Toto sa nepodarilo priložiť.",
        "Commands are no longer attached automatically.": "Príkazy sa už neprikladajú automaticky.",
        "Every command you run will be put into the message box.": "Každý príkaz, ktorý spustíte, sa vloží do poľa správy.",
        "Every command you run will be sent as a message on its own.": "Každý príkaz, ktorý spustíte, sa odošle ako samostatná správa.",
        "Nothing to send: select some output first.": "Nie je čo odoslať: najprv označte časť výstupu.",
        "Copied the last of the screen, as it appeared. This shell does not mark where commands begin.": "Skopírovaný koniec obrazovky, tak ako vyzeral. Tento shell neoznačuje, kde sa príkazy začínajú.",
        "Copied.": "Skopírované.",
        "Value": "Hodnota",
        "OK": "OK",
        "Model switched to %(model)s": "Model prepnutý na %(model)s",
        "%(n)s new arrivals": "Nové položky: %(n)s",
        "Report filed: “%(title)s”": "Podaná správa: „%(title)s“",
        "New message": "Nová správa",
        "Reply ready in “%(title)s”": "Odpoveď je hotová v „%(title)s“",
        "This browser has no notifications to offer.": "Tento prehliadač upozornenia neponúka.",
        "Notifications are blocked for this site in your browser's own settings, which is the only place that can be undone.": "Upozornenia pre túto stránku sú zablokované v nastaveniach samotného prehliadača a len tam sa to dá zrušiť.",
        "Left off — nothing was changed.": "Ponechané vypnuté — nič sa nezmenilo.",
        "Notifications on, including while LLeMbas is closed.": "Upozornenia sú zapnuté, aj keď je LLeMbas zatvorený.",
        "Notifications on while LLeMbas is open.": "Upozornenia sú zapnuté, kým je LLeMbas otvorený.",
        "Let LLeMbas tell you when a reply, a report or a scheduled run arrives while you are looking at something else?": "Má vám LLeMbas dať vedieť, keď príde odpoveď, správa alebo naplánovaný beh, kým sa pozeráte na niečo iné?",
        "Turn on": "Zapnúť",
        "Not now": "Teraz nie",
        "Turn off notifications": "Vypnúť upozornenia",
        "Blocked for this site in your browser's settings, which is the only place that can be undone.": "Pre túto stránku zablokované v nastaveniach prehliadača a len tam sa to dá zrušiť.",
        "On in this browser. Nothing is shown while you are looking at the page.": "V tomto prehliadači zapnuté. Kým sa pozeráte na stránku, nič sa nezobrazí.",
        "Off in this browser.": "V tomto prehliadači vypnuté.",
        "Stop generating": "Zastaviť generovanie",
        "Send — it waits for its turn": "Odoslať — počká, kým príde na rad",
        "Sent now, taken in at the reply's next step or after it": "Odošle sa hneď a odpoveď si to prevezme v ďalšom kroku alebo po ňom",
        "the connection's own default": "vlastná predvoľba spojenia",
        "Choose a connection first — there is nothing to browse without one.": "Najprv vyberte spojenie — bez neho nie je čo prechádzať.",
    }
)

# 2.2.0: connection types (OpenAI dialect or Anthropic) and provider names.
MESSAGES.update(
    {
        "Anthropic's own API is the other type a connection can have.": "Druhým typom spojenia je vlastné rozhranie spoločnosti Anthropic.",
        "Type": "Typ",
        "OpenAI dialect": "Dialekt OpenAI",
        "Anthropic": "Anthropic",
        "Which API the endpoint speaks. Everything except Anthropic's own API speaks the OpenAI dialect.": "Ktorým rozhraním koncový bod hovorí. Dialektom OpenAI hovorí všetko okrem vlastného rozhrania spoločnosti Anthropic.",
        "Provider name": "Názov poskytovateľa",
        "llama": "llama",
        "How clients such as the LLeMbas CLI name this connection's models: <code>provider/model</code>, for example <code>llama/bonsai</code>. Lowercase letters, digits and hyphens, unique among connections.": "Podľa neho klienti ako LLeMbas CLI pomenúvajú modely tohto spojenia: <code>provider/model</code>, napríklad <code>llama/bonsai</code>. Malé písmená, číslice a spojovníky, medzi spojeniami jedinečný.",
        "Anthropic API key": "Kľúč rozhrania Anthropic",
        "A provider name is 1 to 32 lowercase letters, digits and hyphens, and starts with a letter or a digit.": "Názov poskytovateľa tvorí 1 až 32 malých písmen, číslic a spojovníkov a začína písmenom alebo číslicou.",
        "The connection “%(name)s” already uses the provider name “%(provider)s”.": "Spojenie „%(name)s“ už používa názov poskytovateľa „%(provider)s“.",
    }
)

# --- 2.3.0: sidebar and search -------------------------------------------
MESSAGES.update(
    {
        "Which sessions to show on %(device)s": "Ktoré relácie zobraziť na zariadení %(device)s",
        "All sessions": "Všetky relácie",
        "Active sessions": "Aktívne relácie",
        "Archived sessions": "Archivované relácie",
        "No archived sessions.": "Žiadne archivované relácie.",
        "No active sessions.": "Žiadne aktívne relácie.",
        "Search…": "Hľadať…",
        "What to search": "Čo hľadať",
        "Chats & agent sessions": "Konverzácie a agentské relácie",
        "Type a word or a phrase to search everything you have here.": "Napíšte slovo alebo frázu a prehľadá sa všetko, čo tu máte.",
        "Show all": "Zobraziť všetko",
        "archived": "archivovaná",
        "Nothing matches “%(q)s”.": "Výrazu „%(q)s“ nič nezodpovedá.",
    }
)

# --- 2.3.0: settings window and composer ---------------------------------
MESSAGES.update(
    {
        "Open as a page": "Otvoriť ako stránku",
    }
)

# --- 2.4.0: credits worth money, every AI service over the API, the phone folder menu ---
MESSAGES.update(
    {
        "More for the folder %(name)s": "Ďalšie pre zložku %(name)s",
        "Price": "Cena",
        "What speech costs, in %(currency)s, when credits are on (Admin → Credits) — in the browser and through the API alike. 0 is free, which is what a server of your own usually is.": "Koľko stojí reč v mene %(currency)s, keď sú zapnuté kredity (Správa → Kredity) — v prehliadači aj cez API rovnako. 0 je zadarmo, čo je zvyčajne prípad vlastného servera.",
        "Speech to text, per minute": "Prevod reči na text, za minútu",
        "Per minute of recording, by the length the server reports, or the recording's own.": "Za minútu nahrávky podľa dĺžky, ktorú hlási server, inak podľa samotnej nahrávky.",
        "Text to speech, per million characters": "Prevod textu na reč, za milión znakov",
        "Per character read aloud.": "Za každý prečítaný znak.",
        "You set what each model costs in money (on its page under Models), what a credit is worth, and how many credits each person gets; nothing is bought or billed here. A model with no price is free, and administrators are never charged — what they use is only recorded. Off, nothing is checked or charged.": "Určujete, koľko stojí každý model v peniazoch (na jeho stránke v časti Modely), akú hodnotu má kredit a koľko kreditov dostane každý človek; nič sa tu nekupuje ani nefakturuje. Model bez ceny je zadarmo a správcom sa nikdy nič neúčtuje — ich spotreba sa iba zaznamenáva. Keď je to vypnuté, nič sa nekontroluje ani neúčtuje.",
        "Currency": "Mena",
        "What prices are typed in, and what balances are shown in beside credits.": "V nej sa zadávajú ceny a v nej sa popri kreditoch zobrazujú zostatky.",
        "One credit is worth": "Hodnota jedného kreditu",
        "In the currency above. Prices are set in money, so changing this changes what every model, minute of speech and picture costs in credits — balances stay as they are.": "V mene uvedenej vyššie. Ceny sa zadávajú v peniazoch, takže zmena tejto hodnoty zmení, koľko kreditov stojí každý model, minúta reči aj obrázok — zostatky zostávajú, aké sú.",
        "Renews": "Obnovenie",
        "Unlimited": "Neobmedzené",
        "Renews on": "Obnoví sa dňa",
        "Renew now": "Obnoviť teraz",
        "Price and the API": "Cena a API",
        "Per picture": "Za obrázok",
        "What one picture costs when credits are on, in a chat or through the API. 0 is free.": "Koľko stojí jeden obrázok, keď sú zapnuté kredity, v konverzácii aj cez API. 0 je zadarmo.",
        "Most pictures per API request": "Najviac obrázkov na jednu požiadavku API",
        "The largest n a program may ask /v1/images/generations for. Each is a full generation.": "Najväčšie n, o ktoré môže program požiadať /v1/images/generations. Každý obrázok je samostatné generovanie.",
        "Chat models answer through the API's chat completions; embedding models only through its embeddings. A model saved before this existed answers both until it is set.": "Konverzačné modely odpovedajú cez chat completions v API, modely vnorení iba cez jeho embeddings. Model uložený pred zavedením tohto nastavenia odpovedá na oboje, kým sa nenastaví.",
        "What a reply on this model costs, in %(currency)s, when credits are on (Admin → Credits). Per million tokens, except per reply. In credits it is this divided by what a credit is worth — %(value)s now — so changing that value changes what every model costs in credits. Leave everything empty to make it free — a local model, say.": "Koľko stojí odpoveď tohto modelu v mene %(currency)s, keď sú zapnuté kredity (Správa → Kredity). Za milión tokenov, okrem ceny za odpoveď. V kreditoch je to táto suma vydelená hodnotou kreditu — teraz %(value)s — takže zmena tejto hodnoty zmení cenu každého modelu v kreditoch. Ak necháte všetko prázdne, model je zadarmo — napríklad lokálny model.",
        "Cost": "Náklady",
        "%(credits)s credits · %(money)s": "%(credits)s kreditov · %(money)s",
        "%(credits)s cr · %(money)s": "%(credits)s kr. · %(money)s",
        "Model or service": "Model alebo služba",
        "Money": "Peniaze",
        "This account is on no plan.": "Tento účet nemá žiadny plán.",
        "This plan has already renewed this month; it renews again on %(when)s.": "Tento plán sa už tento mesiac obnovil; ďalšie obnovenie bude %(when)s.",
        "Renewed.": "Obnovené.",
        "That is not a date.": "To nie je dátum.",
    }
)

# --- 1.1: the agent's directory, picked from the ones already used ---------------
MESSAGES.update(
    {
        "Choose a directory": "Vyberte adresár",
        "Recent directories": "Nedávne adresáre",
        "Browse…": "Prehliadať…",
        "Choose the directory this agent works in before sending.": "Pred odoslaním vyberte adresár, v ktorom bude agent pracovať.",
    }
)

MESSAGES.update(
    {
        "Local": "Lokálny",
        "This computer (%(host)s)": "Tento počítač (%(host)s)",
        "This computer allows no folders yet. Add one in LLeMbas Desktop, or run <code>%(command)s</code>.": "Tento počítač zatiaľ nepovoľuje žiadny priečinok. Pridajte ho v LLeMbas Desktop alebo spustite <code>%(command)s</code>.",
        "This device allows no folders yet. Add one there in LLeMbas Desktop, or run <code>%(command)s</code> on it.": "Toto zariadenie zatiaľ nepovoľuje žiadny priečinok. Pridajte ho tam v LLeMbas Desktop alebo na ňom spustite <code>%(command)s</code>.",
    }
)

# --- 1.3: the plan in every chat, and Plan mode for a chat --------------------
MESSAGES.update(
    {
        "An older override, saved for <code>%(key)s</code> before it became part of this fragment — review it: it is no longer sent.": "Staršia úprava, uložená pre <code>%(key)s</code> skôr, než sa stal súčasťou tohto fragmentu — skontrolujte ju: už sa neodosiela.",
        "Mode": "Režim",
        "Answers and acts as usual.": "Odpovedá a koná ako zvyčajne.",
        "Normal": "Bežný",
        "Approving ends Plan mode: the model carries the plan out in this reply, keeping it current above the message box.": "Schválením sa končí režim Plan: model plán vykoná v tejto odpovedi a priebežne ho aktualizuje nad poľom pre správu.",
        "Carry out this plan? The model works through it in order, and the plan stays above the message box while it does.": "Vykonať tento plán? Model ho prejde po poriadku a plán zostane nad poľom pre správu, kým na ňom pracuje.",
        "Show the whole plan": "Zobraziť celý plán",
        "This chat has no plan.": "Táto konverzácia nemá žiadny plán.",
        "Waiting for you to look at the plan…": "Čaká sa, kým sa pozriete na plán…",
        "Ask it something back": "Opýtať sa niečo späť",
        "Your question for the model…": "Vaša otázka pre model…",
        "Keeping a plan": "Vedenie plánu",
    }
)

# --- 1.6.0: background jobs, the helper panel, the subagent preamble ---
MESSAGES["A reply can hand a self-contained piece of work to a second model that runs on its own and reports back — several at once, which is what makes research fan out instead of queueing, and in the background, so the reply carries on and is told when the helper is done. There are two kinds: explore, which reads and searches only, and general, which may also change things the chat itself may change. An agent chat's helpers run on its linked device instead, under the device's own limits."] = 'Odpoveď môže samostatný kus práce podať druhému modelu, ktorý beží sám a ohlási sa — aj viacerým naraz, a práve preto sa výskum rozvetví namiesto toho, aby stál v rade; a aj na pozadí, takže odpoveď pokračuje a dozvie sa, keď pomocník skončí. Sú dva druhy: explore, ktorý len číta a vyhľadáva, a general, ktorý smie meniť aj to, čo smie meniť samotná konverzácia. Pomocníci agentskej konverzácie bežia namiesto toho na pripojenom zariadení, v jeho vlastných limitoch.'
MESSAGES["A helper cannot ask anybody anything, so nothing in its chat can stop for approval. It therefore gets only what this chat could already do <em>without</em> asking: it reads and searches the library and the web, and the general kind writes what the chat may write. It cannot send helpers of its own; it can message the main agent, and be messaged, while it works. <strong>%(mode)s</strong> is not inherited: a helper's task text can have come from a page it read."] = 'Pomocník sa nemôže nikoho na nič spýtať, takže nič v jeho konverzácii sa nemôže zastaviť kvôli schváleniu. Dostane preto len to, čo táto konverzácia už smie <em>bez</em> pýtania: číta a vyhľadáva v knižnici a na webe a druh general píše to, čo smie písať konverzácia. Vlastných pomocníkov posielať nemôže; počas práce môže písať hlavnému agentovi a ten jemu. Režim <strong>%(mode)s</strong> sa nededí: text úlohy pomocníka mohol prísť zo stránky, ktorú si prečítal.'
MESSAGES['%(job)s asks:'] = '%(job)s sa pýta:'
MESSAGES['Helper'] = 'Pomocník'
MESSAGES['Close the helper panel'] = 'Zatvoriť panel pomocníka'
MESSAGES['Open a helper from the strip above the composer to follow it here.'] = 'Otvorte pomocníka z lišty nad poľom správy a sledujte ho tu.'
MESSAGES['subagent'] = 'podagent'
MESSAGES['Stop'] = 'Zastaviť'
MESSAGES['A message to the helper'] = 'Správa pre pomocníka'
MESSAGES['Message the helper — it reads it at its next step; a finished one is started again with it'] = 'Napíšte pomocníkovi — prečíta si to v ďalšom kroku; skončený sa s tým spustí znova'
MESSAGES['From the main agent'] = 'Od hlavného agenta'
MESSAGES['The task'] = 'Úloha'
MESSAGES['Working…'] = 'Pracuje…'
MESSAGES['Background jobs'] = 'Úlohy na pozadí'
MESSAGES['Running for %(time)s'] = 'Beží %(time)s'
MESSAGES['Follow this helper'] = 'Sledovať tohto pomocníka'
MESSAGES['Follow this helper and message it'] = 'Sledovať tohto pomocníka a písať mu'
MESSAGES['Stop %(job)s'] = 'Zastaviť %(job)s'
MESSAGES['Stop this job'] = 'Zastaviť túto úlohu'
MESSAGES['Scheduled run'] = 'Plánovaný beh'
MESSAGES['The device did not answer in time.'] = 'Zariadenie neodpovedalo včas.'
MESSAGES['Not done: %(error)s'] = 'Nevykonané: %(error)s'
MESSAGES['That job is not here any more.'] = 'Táto úloha tu už nie je.'
MESSAGES['Write something to send.'] = 'Napíšte niečo na odoslanie.'
MESSAGES["That helper's conversation is gone, so it cannot be messaged."] = 'Konverzácia tohto pomocníka je preč, takže mu nemožno písať.'
MESSAGES['running'] = 'beží'
MESSAGES['done'] = 'hotovo'
MESSAGES['lost'] = 'stratená'

# The app's door (/api/v2) and its notifications.
MESSAGES['Read and write your chats, as the web interface does'] = 'Čítať a zapisovať vaše chaty, ako to robí webové rozhranie'
MESSAGES['A reply is waiting'] = 'Odpoveď čaká'
MESSAGES['A reply is waiting for your answer.'] = 'Odpoveď čaká na vašu reakciu.'
MESSAGES['That push address cannot be used: it must be https and reachable from here.'] = 'Túto adresu pre oznámenia nemožno použiť: musí byť https a dosiahnuteľná odtiaľto.'

# Stopping a reply, and why the microphone did not open.
MESSAGES['Stopping…'] = 'Zastavuje sa…'
MESSAGES['The reply could not be stopped. Try again.'] = 'Odpoveď sa nepodarilo zastaviť. Skúste to znova.'
MESSAGES["Microphone access was refused. Allow it for this site in the browser's address bar or site settings, then try again."] = 'Prístup k mikrofónu bol odmietnutý. Povoľte ho pre túto stránku v paneli s adresou alebo v nastaveniach stránky v prehliadači a skúste to znova.'
MESSAGES['No microphone was found. Connect one, or check that the system can see it, then try again.'] = 'Nenašiel sa žiadny mikrofón. Pripojte ho alebo overte, či ho systém vidí, a skúste to znova.'
MESSAGES['The microphone is in use by another application, or the system would not let the browser open it.'] = 'Mikrofón používa iná aplikácia alebo systém nedovolil prehliadaču ho otvoriť.'
MESSAGES['The microphone could not be opened (%(error)s).'] = 'Mikrofón sa nepodarilo otvoriť (%(error)s).'
