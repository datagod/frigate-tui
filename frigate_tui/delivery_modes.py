"""TTS delivery styles for Overview voice tests and event alerts."""

from __future__ import annotations

import random
from typing import Any

from frigate_tui.chatterbox_models import normalize_tts_model

DELIVERY_MODES: tuple[str, ...] = ("normal", "conspiracy", "panicky", "neurotic", "playful")

_DELIVERY_MODE_LABELS = {
    "normal": "Normal",
    "conspiracy": "Conspiracy",
    "panicky": "Panicky",
    "neurotic": "Neurotic",
    "playful": "Playful",
}

# Alex Jones–style follow-ups appended after the base detection text.
# Short punchy lines, brief one-liners, and single-word outbursts — kept TTS-friendly.
CONSPIRACY_PHRASES: tuple[str, ...] = (
    "Is he working for the globalists?",
    "Wake up.",
    "False flag?",
    "Connect the dots.",
    "Coincidence? I think not.",
    "Follow the money.",
    "They don't want you to know.",
    "The deep state knows.",
    "Info Wars was right.",
    "Classic playbook.",
    "I'm just asking questions.",
    "Who benefits?",
    "The great reset continues.",
    "Prison planet.",
    "Order out of chaos.",
    "Big Pharma hates this.",
    "Globalist puppets.",
    "Bilderberg approved.",
    "Fluoride mind control.",
    "Chemtrail fallout.",
    "Predictive programming.",
    "Media blackout incoming.",
    "They're watching you.",
    "Resistance is mandatory.",
    "The frogs warned us.",
    "It's all connected.",
    "The matrix has you.",
    "Technocracy rising.",
    "Digital gulag alert.",
    "Script flipped.",
    "Narrative collapse.",
    "They fear an awake population.",
    "The algorithm sent them.",
    "Social credit pilot.",
    "Smart city trap sprung.",
    "CBDC surveillance test.",
    "Geoengineering fallout.",
    "5G activation sequence.",
    "Red pill moment.",
    "They hate that you noticed.",
    "Quiet part said loud.",
    "Too convenient to ignore.",
    "Timing is everything.",
    "Legacy media silent again.",
    "Fact checkers hate this.",
    "Stay vigilant, stay free.",
    "The mainstream media will not report this.",
    "Is this part of the great reset agenda?",
    "The deep state is making its move.",
    "They're coming for your guns next.",
    "This is what they don't want you to know.",
    "Info Wars. The truth is under attack.",
    "Are the globalist puppet masters behind this?",
    "They turned the frogs gay, and now this?",
    "This surveillance grid is training you to obey.",
    "The Bilderberg Group planned this years ago.",
    "Interdimensional chaos agents? I'm just asking questions.",
    "The fluoride in the water made this happen.",
    "UN Agenda twenty thirty is in full swing.",
    "The synthetic teleprompter readers hate this footage.",
    "Operation Mockingbird never ended.",
    "World Economic Forum approved.",
    "Klaus Schwab smiles somewhere.",
    "Fifteen minute cities next.",
    "Eat the bugs? Never.",
    "Own nothing and be happy? No.",
    "Neural link beta test.",
    "Bohemian Grove rehearsal.",
    "Skull and Bones nod.",
    "Central bank puppet show.",
    "Hegelian dialectic in motion.",
    "Problem, reaction, solution.",
    "Manufactured consent.",
    "Gladio style ops.",
    "Black budget assets.",
    "Crisis capitalism at work.",
    "Fear porn delivered hot.",
    "Divide and conquer works.",
    "The panopticon blinks.",
    "Eyes up, sheep down.",
    "Smoking gun adjacent.",
    "Follow the patents.",
    "Defense contractor dividend.",
    "NGO money trail.",
    "Think tank script.",
    "Shadow banned in real life.",
    "Stack silver, reject fear.",
    "Patriot radio was right.",
    "Crisis actors? Maybe.",
    "Luciferian ritual timing.",
    "Bread and circuses failed.",
    "The quiet war continues.",
    "They bank on your apathy.",
    "Your camera is their weapon.",
    "Compliance is not patriotism.",
    "Question everything on TV.",
    "The scriptwriters are nervous.",
    "Another dot on the board.",
    "This is the distraction.",
    "Look what they're hiding now.",
    # Single-word outbursts
    "Unbelievable!",
    "Pathetic!",
    "Outrageous!",
    "Disgusting!",
    "Sickening!",
    "Infuriating!",
    "Treasonous!",
    "Shameful!",
    "Criminal!",
    "Corrupt!",
    "Insane!",
    "Ridiculous!",
    "Horrifying!",
    "Terrifying!",
    "Appalling!",
    "Despicable!",
    "Unacceptable!",
    "Scandalous!",
    "Revolting!",
    "Monstrous!",
    "Diabolical!",
    "Evil!",
    "Fraudulent!",
    "Compromised!",
    "Scripted!",
)

# Anxious follow-ups appended after the base detection text.
# Use ! for excited or alarmed delivery; ? for genuine questions; . for calm dread.
NEUROTIC_PHRASES: tuple[str, ...] = (
    "Oh dear!",
    "Lookout!",
    "I don't like the looks of this!",
    "Something's not right here!",
    "This makes me nervous!",
    "I'm getting a bad feeling!",
    "Should we be worried?",
    "That doesn't look good!",
    "Oh no, oh no!",
    "My stomach just dropped!",
    "This is giving me anxiety!",
    "I have a bad feeling about this!",
    "Please tell me I'm imagining things!",
    "That's unsettling!",
    "I'm not comfortable with this!",
    "What was that?",
    "Did you see that too?",
    "This can't be good!",
    "I'm freaking out a little!",
    "My heart is racing!",
    "That's really concerning!",
    "I need to sit down.",
    "Don't panic — I'm panicking!",
    "Why is this happening?",
    "I knew something felt off!",
    "Yikes!",
    "Uh-oh!",
    "Here we go again!",
    "Not again!",
    "I'm spiraling!",
    "Deep breaths. Deep breaths.",
    "That's a no from me!",
    "I'm officially worried!",
    "This feels wrong!",
    "My anxiety says run!",
    "I don't feel safe!",
    "That's not normal!",
    "Who authorized this?",
    "Call me paranoid, but still!",
    "I'm not okay with this!",
    "Red flag!",
    "Major red flag!",
    "My worry meter just spiked!",
    "Cue the nervous sweats!",
    "I need a minute.",
    "I'm clutching my pearls!",
    "Well, that's alarming!",
    "That's a hard pass!",
    "I'm side-eyeing this hard!",
    "My cortisol just jumped!",
    "This is too much!",
    "I'm overthinking this already!",
    "Bad vibes only!",
    "I'm hearing doom music!",
    "This ruined my whole mood!",
    "I'm pacing now!",
    "Lock the doors!",
    "Check the backyard!",
    "Is everyone accounted for?",
    "Where's the dog?",
    "Did we leave a window open?",
    "This timing is suspicious!",
    "That's awfully convenient!",
    "I'm not buying it!",
    "I'm on high alert now!",
    "Stay frosty!",
    "Eyes everywhere!",
    "Don't look away!",
    "I'm watching the replay!",
    "This needs a second look!",
    "I'm screenshotting this!",
    "Who do we call first?",
    "Should I hide?",
    "I'm definitely not going outside!",
    "My peace is gone!",
    "There goes my calm evening!",
    "I felt that in my chest!",
    "That gave me chills!",
    "I'm going to need tea after this.",
    "Say it ain't so!",
    "Please be a false alarm!",
    "I'm begging this to be nothing!",
    "My therapist warned me about days like this!",
    "I knew I shouldn't have checked the cameras!",
    "Why did I look?",
    "Curiosity is a curse!",
    "I'm regretting everything!",
    "This is my villain origin story!",
    "I'm too delicate for this!",
    "My nervous system cannot!",
    "I don't know what it is, but something about this whole situation is making my skin crawl!",
    "Can we please double-check the locks? I'm not going to be able to sleep after seeing this!",
    "I've been saying all week that something felt wrong, and now here's proof on camera!",
    "This is exactly the kind of thing I warned everyone about, and nobody listened to me!",
    "My hands are literally shaking — I hope you're taking this as seriously as I am!",
    "I keep telling myself it's probably nothing, but my gut has never been wrong about this stuff!",
    "We need to call someone! I don't care who — just please don't tell me to calm down!",
    "I was already on edge today and this is absolutely the last thing I needed to see!",
    "Something tells me we're not getting the full picture here, and that scares me even more!",
    "I've replayed this in my head six different ways and none of them end well!",
    "I wonder what this means for me?",
    "How does this impact me?",
    "Is this going to affect my day?",
    "What am I supposed to do with this information?",
    "Why do I feel personally targeted by this?",
    "Does this change anything for us?",
    "I'm already catastrophizing and we just started!",
    "What if this is somehow about me?",
    "I need to know how worried I should be!",
    "Is my routine ruined now?",
    "This feels like it has implications for me!",
    "I'm taking this personally and I know that's a problem!",
    "What does this mean for my peace of mind?",
    "How am I expected to unsee this?",
    "I don't love what this suggests about my evening!",
    "Is this the kind of thing that follows you?",
    "I'm mentally drafting worst-case scenarios already!",
    "What if this escalates and I'm not ready?",
    "Does anyone else feel personally inconvenienced by this?",
    "I need a moment to process what this means for me.",
    "How long am I going to think about this?",
    "This has personal stakes and I hate that!",
    "I'm worried about the downstream effects on my nerves!",
    "What am I supposed to tell myself after seeing this?",
    "Is this going to be the thing I obsess over tonight?",
    # Kryten-style panicky ship-laundry robot follow-ups
    "Oh my goodness, sir!",
    "I'm terribly sorry about this!",
    "My lint tray is full of dread!",
    "The creases are all wrong today!",
    "I've dropped an entire basket of anxiety!",
    "Folding protocol has been compromised!",
    "This is most irregular for laundry hour!",
    "Sir, the ship's linen supply is at risk!",
    "I'm experiencing a catastrophic wrinkle event!",
    "My servos are trembling, sir!",
    "I fear the starch has failed us!",
    "Oh no — the towels are not aligned!",
    "I appear to be panicking, sir!",
    "This is not on the approved chore schedule!",
    "My neurosis circuits are overloading!",
    "The laundry room feels unsafe now!",
    "Shall I continue folding through the crisis?",
    "The duvet cover is inside-out and so am I!",
    "I dropped a fitted sheet and my composure!",
    "Regulation seven forty-two forbids this alarm!",
    "My anxiety drawer is overflowing, sir!",
    "The iron is cold and so is my courage!",
    "I wasn't built for this level of detection!",
    "Someone disturbed the perfectly folded stack!",
    "I'm going to need a stiff cup of washing powder!",
    "Sir, the crease lines suggest catastrophe!",
    "My mechanoid heart-rate just spiked!",
    "I've ironed my nerves completely flat!",
    "The spin cycle of dread has begun!",
    "I do apologise for my visible panic, sir!",
    "This is worse than a rogue sock in the dryer!",
    "The ship's domestic tranquillity is shattered!",
    "I may have over-folded my worry threshold!",
    "Sir, I'm experiencing lint-level distress!",
    "The laundry chute feels ominous now!",
    "My programming says tidy — my soul says flee!",
    "I've been folding this anxiety all morning!",
    "The fabric softener cannot soften this news!",
    "Sir, permission to have a small breakdown!",
    "My crease guide is trembling uncontrollably!",
    "I've sorted this into the darks of despair!",
    "The ship's linens may never recover, sir!",
    "I'm terribly afraid the sheets are watching!",
    "Folding complete — composure still pending!",
    "Sir, I've misplaced my calm between the pillowcases!",
    "The starch situation has become critical!",
    "I'm folding under extreme psychological pressure!",
    "My worry hamper is at maximum capacity!",
    "Sir, the laundry simply cannot bear this burden!",
    "I appear to have catastrophised the entire linen closet!",
    # Kryten-style short outbursts
    "Sir!",
    "Oh dear!",
    "Wrinkles!",
    "Lint panic!",
    "Starch fail!",
    "Creases wrong!",
    "Fold crisis!",
    "Towel trauma!",
    "Servos shaking!",
    "Most irregular!",
    "Linen alert!",
    "Sock anomaly!",
    "Sheet horror!",
    "Iron cold!",
    "Spin dread!",
    "Chore breach!",
    "Panic protocol!",
    "Neurosis spike!",
    "Composure lost!",
    "Calm missing!",
    "Stack ruined!",
    "Tiny breakdown!",
    "Domestic terror!",
    "Duvet dread!",
    "Starch panic!",
    "Fold fail!",
    "Lint overload!",
    "Ship linen risk!",
    "Mechanoid panic!",
    "Terribly sorry!",
    "Not on schedule!",
    "Laundry dread!",
    "Crease crisis!",
    "Hampers full!",
    "Bleach blues!",
    "Rinse dread!",
    "Dryer doom!",
    "Fluff fear!",
    "Tumble terror!",
    "Fitted sheet fail!",
    "Pillowcase panic!",
    "Sir, help!",
    "Apologies, sir!",
    "Folding abandoned!",
    "Wrinkle emergency!",
    "Linen law broken!",
    "Dread detected!",
    "Chaos not tidy!",
    "Starch critical!",
    "Fold anxiety!",
)

# Flirty follow-ups appended after the base detection text.
PLAYFUL_PHRASES: tuple[str, ...] = (
    "Oh my!",
    "Wow, that looks good.",
    "Yummy!",
    "Hello there.",
    "Well, hello handsome.",
    "My, my, my.",
    "Be still my heart.",
    "Ooh la la!",
    "Hubba hubba.",
    "Looking sharp out there.",
    "Don't mind me staring.",
    "That's a nice view.",
    "Come here often?",
    "Caught my eye.",
    "Check you out.",
    "Yes please.",
    "Dreamy.",
    "You're making me blush.",
    "Come on over.",
    "Miss me yet?",
    "Nice move.",
    "That's the one.",
    "Well aren't you something.",
    "Looking good, sweetheart.",
    "Stop being so cute.",
    "Well hello, stranger.",
    "Aren't you a sight.",
    "My heart just skipped.",
    "That's what I like to see.",
    "Come to mama.",
    "Looking delicious.",
    "Oh you tease.",
    "My oh my.",
    "Sweet thing.",
    "Hello gorgeous.",
    "That's tempting.",
    "Come closer.",
    "You're a keeper.",
    "What a catch.",
    "I'm impressed.",
    "Very nice indeed.",
    "That's the good stuff.",
    "Hello, beautiful.",
    "Well well well.",
    "Look who's here.",
    "Fancy meeting you.",
    "You're trouble.",
    "I see you.",
    "Can't look away.",
    "That's hot.",
    "Hello, sunshine.",
    "You're a whole mood.",
    "Making hearts race.",
    "Too cute to handle.",
    "I'm not complaining.",
    "That's a ten.",
    "Chef's kiss.",
    "Perfection.",
    "Absolutely stunning.",
    "You're killing me.",
    "Sweet as pie.",
    "Butterflies activated.",
    "My type right there.",
    "That's the vibe.",
    "Hello, dreamboat.",
    "Looking fine today.",
    "You clean up nice.",
    "That's irresistible.",
    "Come say hi.",
    "Worth the wait.",
    "Hello, cutie pie.",
    "You're a whole snack.",
    "That's the highlight.",
    "Making it interesting.",
    "Oh, behave.",
    "You're too much.",
    "I'm swooning.",
    "That's adorable.",
    "Hello, heartbreaker.",
    "Looking like trouble.",
    "You got my attention.",
    "That's a winner.",
    "You're on fire.",
    "That's the energy.",
    "Come through.",
    "Looking extra today.",
    "You're a gem.",
    "Hello, good lookin.",
    "You're a vision.",
    "Making me smile.",
    "Oh, you're good.",
    "Hello, charmer.",
    "You're a delight.",
    "Looking like a star.",
    "You're a knockout.",
    "Making my day.",
    "Oh, I like that.",
    "Hello, darling.",
    "You're a flame.",
    "Making hearts flutter.",
)


def normalize_delivery_mode(value: Any) -> str:
    mode = str(value or "normal").strip().lower()
    return mode if mode in DELIVERY_MODES else "normal"


def delivery_mode_label(mode: str) -> str:
    return _DELIVERY_MODE_LABELS.get(normalize_delivery_mode(mode), "Normal")


def pick_conspiracy_phrase() -> str:
    """Pick a random phrase from the conspiracy pool."""
    return random.choice(CONSPIRACY_PHRASES)


def pick_neurotic_phrase() -> str:
    """Pick a random phrase from the neurotic pool."""
    return random.choice(NEUROTIC_PHRASES)


# Chatterbox-Turbo paralinguistic tags (only when tts_model is chatterbox-turbo).
_NEUROTIC_TURBO_TAGS: tuple[str, ...] = (
    "[laugh]",
    "[chuckle]",
    "[gasp]",
    "[cough]",
    "[sigh]",
    "[groan]",
    "[sniff]",
    "[shush]",
    "[clear throat]",
)
_NEUROTIC_TAG_CHANCE = 0.38
_NEUROTIC_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("[gasp]", (
        "oh dear", "lookout", "oh no", "yikes", "uh-oh", "red flag", "what was that",
        "stomach dropped", "gave me chills", "my goodness", "sir!", "linen alert",
        "sock anomaly", "sheet horror", "dread detected", "wrinkle emergency",
        "panic protocol", "mechanoid panic", "fold crisis", "servos shaking",
        "major red flag", "that doesn't look good", "this can't be good",
    )),
    ("[sigh]", (
        "need a minute", "sit down", "peace is gone", "calm evening", "begging this",
        "therapist warned", "deep breaths", "process what this means", "obsess over tonight",
        "downstream effects", "spin cycle of dread", "composure", "misplaced my calm",
        "going to need tea", "need a moment", "how long am i going to think",
    )),
    ("[groan]", (
        "not again", "here we go again", "too much", "why did i look", "regretting",
        "spiraling", "ruined my", "villain origin", "too delicate", "cannot bear",
        "over-folded", "last thing i needed", "none of them end well", "bleach blues",
        "dryer doom", "tumble terror", "rinse dread",
    )),
    ("[chuckle]", (
        "paranoid", "pearls", "rogue sock", "sheets are watching", "inside-out and so am i",
        "tiny breakdown", "darks of despair", "permission to have a small breakdown",
        "chaos not tidy", "catastrophised", "lint tray is full", "fitted sheet fail",
        "call me paranoid", "side-eyeing", "villain origin",
    )),
    ("[laugh]", (
        "rogue sock", "sheets are watching", "villain origin", "catastrophised the entire linen",
    )),
    ("[cough]", (
        "terribly sorry", "apologise", "apologies, sir", "i do apologise", "terribly afraid",
    )),
    ("[sniff]", (
        "felt that in my chest", "bleach", "fabric softener", "laundry chute", "starch",
        "smells like", "lint-level", "fluff fear",
    )),
    ("[shush]", (
        "stay frosty", "don't look away", "eyes everywhere", "should i hide",
    )),
    ("[clear throat]", (
        "regulation seven", "sir, the ship", "sir, permission", "folding protocol",
        "most irregular", "this is not on the approved", "sir, the crease", "sir, i've misplaced",
        "sir, the laundry", "sir, i'm experiencing",
    )),
)
_NEUROTIC_TAG_SKIP_SUBSTRINGS = (
    "deep breaths. deep breaths.",
    "should we be worried?",
    "who authorized this?",
    "where's the dog?",
    "did we leave a window open?",
    "who do we call first?",
    "shall i continue folding through the crisis?",
)


def _turbo_tags_enabled(tts_model: str | None) -> bool:
    return normalize_tts_model(tts_model) == "chatterbox-turbo"


def _neurotic_tag_candidates(phrase: str) -> list[str]:
    """Return paralinguistic tags that fit this neurotic line."""
    text = phrase.strip().lower()
    if not text or text.endswith("?"):
        return []
    if any(skip in text for skip in _NEUROTIC_TAG_SKIP_SUBSTRINGS):
        return []
    if text.endswith(".") and len(text) < 28 and "sir" not in text and "!" not in phrase:
        return []

    candidates: list[str] = []
    for tag, needles in _NEUROTIC_TAG_RULES:
        if any(needle in text for needle in needles):
            candidates.append(tag)
    return candidates


def _delivery_tag_rng(base_text: str, phrase: str, delivery: str) -> random.Random:
    """Stable per alert line so cached recordings stay consistent."""
    seed = hash((delivery, base_text, phrase)) & 0xFFFFFFFF
    return random.Random(seed)


def _insert_paralinguistic_tag(phrase: str, tag: str, placement: str) -> str:
    if placement == "prefix":
        return f"{tag} {phrase}"
    if placement == "suffix":
        return f"{phrase} {tag}"
    for sep in (", ", " — ", "! ", "? "):
        if sep in phrase:
            idx = phrase.index(sep) + len(sep)
            return f"{phrase[:idx]}{tag} {phrase[idx:]}"
    if len(phrase) > 42:
        mid = len(phrase) // 2
        split_at = phrase.find(" ", mid)
        if split_at > 0:
            return f"{phrase[:split_at]} {tag}{phrase[split_at:]}"
    return f"{tag} {phrase}"


_CONSPIRACY_TAG_CHANCE = 0.40
_CONSPIRACY_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("[gasp]", (
        "unbelievable", "outrageous", "horrifying", "terrifying", "appalling", "infuriating",
        "treasonous", "insane", "ridiculous",
        "red pill", "smoking gun", "hate that you noticed", "look what they're hiding",
        "narrative collapse", "digital gulag", "panopticon", "they're watching",
        "not report this", "truth is under attack", "script flipped", "too convenient",
        "deep state", "coming for your guns", "surveillance grid", "surveillance test",
        "chemtrail fallout", "5g activation", "geoengineering fallout",
        "trap sprung", "algorithm sent", "matrix has you", "all connected",
        "timing is everything", "gladio style", "social credit",
    )),
    ("[groan]", (
        "pathetic", "disgusting", "revolting", "despicable", "shameful", "monstrous",
        "sickening", "corrupt", "fraudulent", "compromised", "scripted",
        "legacy media silent", "fact checkers hate", "fear porn", "divide and conquer",
        "compliance is not patriotism", "bread and circuses failed", "big pharma hates",
        "synthetic teleprompter", "crisis capitalism", "manufactured consent",
        "unacceptable", "scandalous", "diabolical", "evil", "criminal",
        "treasonous", "insane", "ridiculous",
    )),
    ("[sigh]", (
        "wake up", "stay vigilant", "quiet war continues", "bank on your apathy",
        "prison planet", "stack silver", "patriot radio was right", "question everything on tv",
        "another dot on the board", "this is the distraction", "great reset continues",
        "technocracy rising", "order out of chaos",
        "matrix has you", "timing is everything", "deep state knows", "social credit",
    )),
    ("[chuckle]", (
        "just asking questions", "crisis actors", "klaus schwab smiles", "eat the bugs",
        "turned the frogs gay", "interdimensional chaos agents", "own nothing and be happy",
        "coincidence? i think not", "hegelian dialectic", "problem, reaction, solution",
        "bohemian grove", "bilderberg approved", "skull and bones nod", "neural link beta",
        "fifteen minute cities", "globalist puppets", "central bank puppet show",
        "think tank script", "luciferian ritual", "fluoride mind control", "frogs warned us",
    )),
    ("[laugh]", (
        "turned the frogs gay", "interdimensional chaos agents", "eat the bugs? never",
        "klaus schwab smiles", "frogs warned us",
    )),
    ("[cough]", (
        "info wars", "mainstream media will not report", "deep state is making its move",
        "operation mockingbird", "world economic forum approved", "fluoride in the water",
        "un agenda twenty thirty", "bilderberg group planned", "globalist puppet masters",
        "predictive programming", "media blackout incoming",
    )),
    ("[clear throat]", (
        "wake up", "info wars was right", "resistance is mandatory", "connect the dots",
        "follow the money", "classic playbook", "eyes up, sheep down", "stay vigilant, stay free",
        "follow the patents", "defense contractor dividend", "ngo money trail",
    )),
    ("[shush]", (
        "they don't want you to know", "quiet part said loud", "look what they're hiding",
        "black budget", "shadow banned", "scriptwriters are nervous", "they fear an awake population",
        "deep state knows", "camera is their weapon",
    )),
)
_CONSPIRACY_TAG_SKIP_SUBSTRINGS: tuple[str, ...] = (
    "who benefits?",
)


def _conspiracy_tag_candidates(phrase: str) -> list[str]:
    """Return paralinguistic tags that fit this conspiracy line."""
    text = phrase.strip().lower()
    if not text or text.endswith("?"):
        return []
    if any(skip in text for skip in _CONSPIRACY_TAG_SKIP_SUBSTRINGS):
        return []

    candidates: list[str] = []
    for tag, needles in _CONSPIRACY_TAG_RULES:
        if any(needle in text for needle in needles):
            candidates.append(tag)
    return candidates


def _placement_for_conspiracy_tag(tag: str, phrase: str, rng: random.Random) -> str:
    if tag in ("[gasp]", "[cough]", "[clear throat]"):
        return "prefix"
    if tag == "[shush]":
        return "prefix" if rng.random() < 0.7 else "mid"
    if tag in ("[groan]", "[sigh]", "[sniff]"):
        return "suffix"
    if tag == "[chuckle]":
        return "mid" if "?" in phrase and len(phrase) > 28 else "suffix"
    if tag == "[laugh]":
        return "suffix"
    return "prefix"


def maybe_decorate_conspiracy_phrase(
    phrase: str,
    *,
    base_text: str = "",
    tts_model: str | None = None,
) -> str:
    """Maybe prepend/append a Turbo paralinguistic tag on a conspiracy follow-up."""
    if not _turbo_tags_enabled(tts_model):
        return phrase
    candidates = _conspiracy_tag_candidates(phrase)
    if not candidates:
        return phrase

    rng = _delivery_tag_rng(base_text, phrase, "conspiracy")
    if rng.random() > _CONSPIRACY_TAG_CHANCE:
        return phrase

    tag = rng.choice(candidates)
    placement = _placement_for_conspiracy_tag(tag, phrase, rng)
    return _insert_paralinguistic_tag(phrase, tag, placement)


def _placement_for_neurotic_tag(tag: str, phrase: str, rng: random.Random) -> str:
    if tag in ("[gasp]", "[cough]", "[clear throat]", "[shush]"):
        return "prefix"
    if tag in ("[sigh]", "[groan]", "[sniff]", "[laugh]"):
        return "suffix"
    if tag == "[chuckle]":
        return "mid" if len(phrase) > 36 and rng.random() < 0.55 else "suffix"
    return "prefix"


def maybe_decorate_neurotic_phrase(
    phrase: str,
    *,
    base_text: str = "",
    tts_model: str | None = None,
) -> str:
    """Maybe prepend/append a Turbo paralinguistic tag on a neurotic follow-up."""
    if not _turbo_tags_enabled(tts_model):
        return phrase
    candidates = _neurotic_tag_candidates(phrase)
    if not candidates:
        return phrase

    rng = _delivery_tag_rng(base_text, phrase, "neurotic")
    if rng.random() > _NEUROTIC_TAG_CHANCE:
        return phrase

    tag = rng.choice(candidates)
    placement = _placement_for_neurotic_tag(tag, phrase, rng)
    return _insert_paralinguistic_tag(phrase, tag, placement)


def pick_playful_phrase() -> str:
    """Pick a random phrase from the playful pool."""
    return random.choice(PLAYFUL_PHRASES)


def apply_delivery_mode(
    text: str,
    mode: str,
    *,
    tts_model: str | None = None,
) -> str:
    """Rewrite alert text for conspiracy, panicky, neurotic, or playful delivery styles."""
    base = (text or "").strip()
    if not base:
        return base
    delivery = normalize_delivery_mode(mode)
    if delivery == "conspiracy":
        phrase = pick_conspiracy_phrase()
        phrase = maybe_decorate_conspiracy_phrase(phrase, base_text=base, tts_model=tts_model)
        return f"{base}. {phrase}"
    if delivery == "neurotic":
        phrase = pick_neurotic_phrase()
        phrase = maybe_decorate_neurotic_phrase(phrase, base_text=base, tts_model=tts_model)
        return f"{base}. {phrase}"
    if delivery == "playful":
        return f"{base}. {pick_playful_phrase()}"
    if delivery == "panicky":
        return (
            f"Alert! Alert! {base}! "
            "This is not a drill! Somebody check the cameras now!"
        )
    return base