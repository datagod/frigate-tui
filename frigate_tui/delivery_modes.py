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
# Short punchy lines, brief one-liners, and longer rants — kept TTS-friendly.
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
    # Longer Alex Jones–style rants
    "I'm not saying it's aliens, but they don't want you asking why the timing fits their occult calendar.",
    "The globalists are panicking because the human spirit is rising, and this footage proves the narrative is collapsing.",
    "They've got chemicals in the water turning the friggin' frogs gay, and now they're coming through your security cameras.",
    "This is textbook Problem Reaction Solution, and if you can't see it yet, they're counting on you staying asleep.",
    "The mainstream dinosaur media won't touch this because their corporate masters signed on to the same depopulation agenda.",
    "I have the documents, folks. The pieces are on the table. You connect them or they connect you to their digital leash.",
    "They want you afraid, isolated, and eating bugs while they fly to Davos on private jets to plan your future.",
    "Every camera on your property is a node in the prison planet grid, and they are beta testing your compliance right now.",
    "The deep state operatives are nervous tonight because patriots are watching feeds they thought nobody would monitor.",
    "This is the soft tyranny phase before the hard lockdown, and they are running rehearsals in your neighborhood.",
    "Klaus Schwab and his unelected buddies dream of a world where you own nothing and they own every second of your day.",
    "The World Economic Forum crowd laughs at you while they choreograph crises that always end with more control.",
    "They've been caught before with false flags and staged narratives, so forgive me for not trusting this timing.",
    "Your grandparents fought real tyranny. Don't dishonor them by pretending this surveillance state is normal.",
    "The CIA playbook is out in the open now, and Operation Mockingbird never stopped feeding you bedtime stories.",
    "Big Pharma, Big Tech, and Big Government share the same investors, and those investors love obedient populations.",
    "They hate this website, they hate independent thinkers, and they especially hate when you share what your cameras see.",
    "The fluoride stare is real, folks. Mass apathy is engineered, and breaking through it starts with one alert at a time.",
    "Interdimensional bureaucrats wouldn't surprise me anymore, because nothing about this timeline feels accidental.",
    "They poison the sky, poison the medicine, poison the news, and then act shocked when you question a single frame.",
    "The panopticon doesn't sleep. It blinks, records, uploads, and trains algorithms to predict your next move.",
    "Patriot listeners have been ahead of the curve for decades, and once again the so called experts are months behind.",
    "If you think this is just a motion alert, you haven't read the white papers on predictive behavioral scoring.",
    "The social credit pilot program is already here, hidden inside loyalty apps, smart devices, and quiet camera feeds.",
    "They want your kids tracked, tagged, medicated, and taught that freedom is selfish and compliance is virtue.",
    "Every crisis they monetize follows the same script: scare the public, suspend rights, and never give them back fully.",
    "The defense contractors need enemies at the border, in your yard, and in your head to keep the contracts flowing.",
    "I'm human, I'm angry, and I'm telling you they're desperate because the awakening cannot be put back in the box.",
    "The synthetic voices on television repeat the same lies, but your own eyes on this feed tell a different story.",
    "They built the smart city trap one ordinance at a time, and your driveway camera is part of the perimeter.",
    "The UN, the WHO, and the banksters share lunch notes about how to turn your home into a monitored cell.",
    "Chemtrail Monday, fluoride Tuesday, propaganda Wednesday, and by Friday they expect you grateful for the cage.",
    "This is not about safety. This is about training you to accept that being watched every second is civilization.",
    "The globalist hive mind hates rural America, hates self sufficiency, and hates anyone who can see without permission.",
    "They think you're too distracted by sports and celebrity drama to notice patterns repeating on your own property.",
    "Bohemian Grove types don't care about your family. They care about rituals, leverage, and controlled collapse.",
    "The black budget boys play with toys you paid for, then test the psyops on your street before breakfast.",
    "If the fact checkers are rushing to debunk it, that usually means you're standing on something hot and real.",
    "The algorithm flagged this moment because even machines know it doesn't fit the approved story they're selling.",
    "Resistance isn't a hobby, it's a duty, and every person who stays awake makes their whole machine shudder.",
    "They want you arguing about nonsense while the real operation moves through back channels and camera networks.",
    "The great reset isn't a conspiracy theory anymore. It's a brochure, and your footage is a page they didn't want published.",
    "Historians will ask why so many saw the signs on their own cameras and still waited for permission to speak.",
    "The Luciferian globalist technocrats believe fear is fertilizer, and they're spreading it across every screen you own.",
    "I told you years ago they'd wire the world into a control grid, and now the grid is pinging your front porch.",
    "The banking cartel creates booms, busts, and panic on schedule, then buys the land you lose when you panic sell.",
    "Your neighbors may laugh today, but when the digital gulag door slams shut, they'll remember who warned them.",
    "They can censor platforms, shadowban accounts, and smear names, but they cannot erase what your lens captured.",
    "The emergency always arrives right when their poll numbers drop or their product launch needs a distraction.",
    "Stay in the fight, stay loud, stay free, because the second you go quiet is the second they think they won.",
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