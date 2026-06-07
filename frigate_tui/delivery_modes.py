"""TTS delivery styles for Overview voice tests and event alerts."""

from __future__ import annotations

import random
from typing import Any

DELIVERY_MODES: tuple[str, ...] = ("normal", "conspiracy", "panicky")

_DELIVERY_MODE_LABELS = {
    "normal": "Normal",
    "conspiracy": "Conspiracy",
    "panicky": "Panicky",
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


def normalize_delivery_mode(value: Any) -> str:
    mode = str(value or "normal").strip().lower()
    return mode if mode in DELIVERY_MODES else "normal"


def delivery_mode_label(mode: str) -> str:
    return _DELIVERY_MODE_LABELS.get(normalize_delivery_mode(mode), "Normal")


def pick_conspiracy_phrase() -> str:
    """Pick a random phrase from the conspiracy pool."""
    return random.choice(CONSPIRACY_PHRASES)


def apply_delivery_mode(text: str, mode: str) -> str:
    """Rewrite alert text for conspiracy or panicky delivery styles."""
    base = (text or "").strip()
    if not base:
        return base
    delivery = normalize_delivery_mode(mode)
    if delivery == "conspiracy":
        return f"{base}. {pick_conspiracy_phrase()}"
    if delivery == "panicky":
        return (
            f"Alert! Alert! {base}! "
            "This is not a drill! Somebody check the cameras now!"
        )
    return base