"""Build YouTube title / description / tags from a generated result.

Optimized for YouTube Shorts algorithm: high-CTR titles, SEO-rich descriptions,
trending hashtags, and deep tag coverage per niche.
"""

import random

# ---------------------------------------------------------------------------
# Tags — 15-20 per niche, targeting actual YouTube search volume
# ---------------------------------------------------------------------------
CATEGORY_TAGS = {
    "reddit": [
        "reddit stories", "askreddit", "reddit story time", "reddit readings",
        "storytime", "reddit confessions", "best of reddit", "reddit top posts",
        "reddit thread", "reddit tiktok", "reddit shorts", "true stories",
        "reddit drama", "reddit update", "story time tiktok", "reddit aita",
        "am i the jerk", "reddit revenge", "entitled parents", "nuclear revenge",
    ],
    "relationship": [
        "relationship advice", "relationship stories", "dating advice",
        "breakup story", "love story", "relationship storytime", "toxic relationship",
        "red flags dating", "cheating story", "boyfriend cheating", "girlfriend secrets",
        "situationship", "dating horror stories", "heartbreak", "love advice",
        "relationship drama", "couples storytime", "ex stories", "trust issues",
    ],
    "cold": [
        "motivation", "motivational speech", "mindset", "self improvement",
        "hard truths", "stoicism", "life advice", "cold motivation",
        "sigma mindset", "discipline motivation", "dark motivation", "grindset",
        "real talk", "wake up call", "brutal honesty", "success mindset",
        "entrepreneur motivation", "level up", "masculine energy", "no excuses",
    ],
    "horror": [
        "horror stories", "scary stories", "true scary stories", "creepypasta",
        "horror story time", "nosleep", "horror narration", "scary short",
        "true horror", "scary tiktok", "dont watch alone", "3am stories",
        "paranormal stories", "ghost stories", "scary reddit", "backrooms",
        "disturbing stories", "creepy facts", "skin walkers", "horror shorts",
    ],
    "simpsons": [
        "simpsons", "the simpsons", "simpsons clips", "simpsons funny moments",
        "homer simpson", "simpsons predictions", "simpsons dark theory",
        "bart simpson", "simpsons edits", "simpsons memes", "cartoon theory",
        "simpsons explained", "simpsons hidden details", "springfield",
        "simpsons shorts", "simpsons sad edit", "cartoon comedy", "matt groening",
    ],
    "anime": [
        "anime", "anime edit", "anime theory", "anime facts", "manga",
        "anime moments", "otaku", "anime shorts", "anime amv", "anime recap",
        "one piece", "attack on titan", "jujutsu kaisen", "demon slayer",
        "naruto", "anime sad edit", "anime badass moments", "top anime",
        "anime recommendations", "underrated anime", "manga spoilers",
    ],
    "betrayal": [
        "betrayal story", "betrayed by best friend", "cheating story",
        "relationship betrayal", "true story", "storytime", "trust issues",
        "backstabbing", "fake friends", "toxic people", "narcissist stories",
        "revenge story", "karma stories", "exposed", "caught cheating",
        "best friend betrayal", "family betrayal", "partner cheating", "shocking truth",
    ],
    "funny": [
        "funny stories", "funny story time", "comedy", "relatable",
        "embarrassing stories", "try not to laugh", "funny shorts",
        "funny moments", "hilarious", "comedy shorts", "memes",
        "cringe stories", "awkward moments", "school stories", "work stories",
        "texting fails", "group chat stories", "funny reddit", "chaotic energy",
    ],
    "games": [
        "gaming", "gameplay", "gaming shorts", "trending games",
        "game clips", "viral gaming", "gamer moments", "gaming news",
        "gaming reaction", "clutch moments", "gaming highlights",
        "esports", "pro gamer", "gaming community", "game review",
        "insane play", "gaming edit", "rage quit", "speedrun", "gaming memes",
    ],
    "space": [
        "space", "space mysteries", "cosmic horror", "space facts",
        "universe secrets", "astronomy", "nasa", "space travel",
        "black hole", "milky way", "aliens", "fermi paradox",
        "dark matter", "space anomaly", "terrifying space", "deep space",
        "solar system", "exoplanets", "space exploration", "cosmic scale",
    ],
}

# ---------------------------------------------------------------------------
# Hashtags — algorithm-optimized combos (YouTube indexes first 3 heavily)
# ---------------------------------------------------------------------------
CATEGORY_HASHTAGS = {
    "reddit": [
        "#shorts #redditstories #askreddit #storytime #viral #fyp",
        "#shorts #reddit #redditreadings #storytime #truestory #fyp",
        "#shorts #askreddit #redditstorytime #confession #viral #fyp",
    ],
    "relationship": [
        "#shorts #relationship #breakup #storytime #dating #fyp",
        "#shorts #relationshipadvice #toxicrelationship #heartbreak #viral #fyp",
        "#shorts #dating #cheating #redflags #storytime #fyp",
    ],
    "cold": [
        "#shorts #motivation #coldmotivation #mindset #sigma #fyp",
        "#shorts #motivation #hardtruths #discipline #grindset #fyp",
        "#shorts #selfimprovement #stoicism #darkmotivation #levelup #fyp",
    ],
    "horror": [
        "#shorts #horror #scarystories #creepypasta #dontwatchalone #fyp",
        "#shorts #scary #horrorstories #nosleep #3am #fyp",
        "#shorts #horror #truescarystories #paranormal #creepy #fyp",
    ],
    "simpsons": [
        "#shorts #simpsons #thesimpsons #simpsonspredictions #cartoon #fyp",
        "#shorts #simpsons #homersimpson #springfield #comedy #fyp",
        "#shorts #simpsons #bartsimpson #simpsonstheory #viral #fyp",
    ],
    "anime": [
        "#shorts #anime #animeedit #manga #otaku #fyp",
        "#shorts #anime #animetheory #animefacts #weeb #fyp",
        "#shorts #anime #amv #animemoments #animeshorts #fyp",
    ],
    "betrayal": [
        "#shorts #betrayal #storytime #cheating #truestory #fyp",
        "#shorts #betrayed #fakefriends #revenge #karma #fyp",
        "#shorts #betrayal #exposed #toxic #storytime #fyp",
    ],
    "funny": [
        "#shorts #funny #comedy #relatable #trynottolaugh #fyp",
        "#shorts #funny #hilarious #storytime #embarrassing #fyp",
        "#shorts #comedy #funnyshorts #memes #chaotic #fyp",
    ],
    "games": [
        "#shorts #gaming #gamer #gameplay #clutch #fyp",
        "#shorts #gaming #proplayer #esports #gamingclips #fyp",
        "#shorts #gaming #viral #insaneplay #gamingmoments #fyp",
    ],
    "space": [
        "#shorts #space #nasa #universe #cosmichorror #fyp",
        "#shorts #space #blackhole #astronomy #terrifying #fyp",
        "#shorts #space #aliens #fermiparadox #deepspace #fyp",
    ],
}

# ---------------------------------------------------------------------------
# Category-specific description templates (richer, more engaging)
# ---------------------------------------------------------------------------
CATEGORY_DESCRIPTIONS = {
    "reddit": {
        "cta": "📖 Follow for daily Reddit stories that will blow your mind!",
        "engage": "💬 Drop your craziest story in the comments 👇",
    },
    "relationship": {
        "cta": "💔 Follow for real relationship stories that hit different.",
        "engage": "💬 Has this ever happened to you? Comment below 👇",
    },
    "cold": {
        "cta": "🧊 Follow for daily cold truths that most people can't handle.",
        "engage": "💬 Tag someone who needs to hear this 👇",
    },
    "horror": {
        "cta": "👻 Follow if you dare... new horror stories daily at 3AM.",
        "engage": "💬 Could you survive this? Comment below 👇",
    },
    "simpsons": {
        "cta": "🍩 Follow for daily Simpsons content — predictions, theories & hidden details!",
        "engage": "💬 Did you catch this detail? Comment below 👇",
    },
    "anime": {
        "cta": "🌸 Follow for anime theories, edits & moments you missed!",
        "engage": "💬 What's YOUR hot take? Drop it below 👇",
    },
    "betrayal": {
        "cta": "🔪 Follow for true betrayal stories that will make your blood boil.",
        "engage": "💬 Would you forgive them? Comment below 👇",
    },
    "funny": {
        "cta": "😂 Follow for chaotic stories that get worse every second!",
        "engage": "💬 What's YOUR most embarrassing moment? Drop it below 👇",
    },
    "games": {
        "cta": "🎮 Follow for insane gaming moments, clutches & reactions daily!",
        "engage": "💬 Could you pull this off? Comment below 👇",
    },
    "space": {
        "cta": "🚀 Follow for terrifying space facts that will keep you up at night.",
        "engage": "💬 Are we alone in the universe? Comment below 👇",
    },
}

# Title emoji prefixes (subtle, adds CTR without being spammy)
CATEGORY_EMOJI = {
    "reddit": "👽", "relationship": "💔", "cold": "🧊", "horror": "👻",
    "simpsons": "🍩", "anime": "⚡", "betrayal": "🔪", "funny": "😂",
    "games": "🎮", "space": "🌌",
}


def build_metadata(result):
    """Build optimized YouTube metadata from a generated video result."""
    category = result.get("category", "reddit")
    title = (result.get("title") or f"Amazing {category} story").strip()

    # Smart title formatting: add emoji prefix if short enough, never double-hashtag
    emoji = CATEGORY_EMOJI.get(category, "🔥")
    clean_title = title.replace("#shorts", "").replace("#Shorts", "").strip()

    # Keep title punchy: emoji + title + #shorts (only if room)
    if len(clean_title) < 85:
        final_title = f"{emoji} {clean_title} #shorts"
    elif len(clean_title) < 95:
        final_title = f"{clean_title} #shorts"
    else:
        final_title = clean_title

    # Build rich description
    hook = (result.get("hook") or "").strip()
    cat_desc = CATEGORY_DESCRIPTIONS.get(category, {
        "cta": "🔥 Follow for daily content!",
        "engage": "💬 Comment below 👇",
    })

    # Pick random hashtag set for variety (avoids repetitive descriptions)
    hashtag_options = CATEGORY_HASHTAGS.get(category, [f"#shorts #{category} #viral #fyp"])
    hashtags = random.choice(hashtag_options) if isinstance(hashtag_options, list) else hashtag_options

    desc_parts = []
    if hook:
        desc_parts.append(hook)
    desc_parts.append("")  # blank line
    desc_parts.append(cat_desc["cta"])
    desc_parts.append(cat_desc["engage"])
    source_url = result.get("source_url")
    if source_url:
        desc_parts.append(f"\n🎬 Original: {source_url}")
    desc_parts.append(f"\n{hashtags}")
    description = "\n".join(desc_parts)

    # Tags: base virality tags + deep niche tags
    tags = (["shorts", "viral", "fyp", "trending", "youtube shorts"]
            + CATEGORY_TAGS.get(category, [category, "story"]))

    return {
        "title": final_title[:100],
        "description": description,
        "tags": tags,
    }
