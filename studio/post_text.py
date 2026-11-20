"""Build the final post text with creator credit that cannot be trimmed away."""
import re

from . import games


class CreditError(ValueError):
    """Posting needs the creator's credit settings corrected first."""


def token(value, prefix, label):
    value = str(value or "").strip()
    if value and not value.startswith(prefix):
        value = prefix + value
    pattern = r"@[\w.-]{1,100}" if prefix == "@" else r"#\w{1,100}"
    if value and not re.fullmatch(pattern, value):
        raise CreditError(f"{label} must be one {prefix}name, without spaces or a link.")
    return value


def hashtags(values):
    """Normalize and deduplicate tags, including AI tags that already have a #."""
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values.strip())
    out, seen = [], set()
    for value in values if isinstance(values, list) else []:
        tag = str(value or "").strip().lstrip("#")
        if re.fullmatch(r"\w{1,100}", tag) and tag.casefold() not in seen:
            out.append(tag)
            seen.add(tag.casefold())
    return out


def compose(clip, creator, platform, game="", *, required_hashtags=None, max_tags=15):
    if not creator.get("require_credit") and not any(creator.get(k) for k in
            ("credit_name", "credit_handle", "credit_tiktok_handle", "credit_hashtag")):
        tags = hashtags(clip.get("hashtags", []))[:max_tags]
        extra = "" if clip.get("fixed_description") or clip.get("hashtags_in_description") else " ".join("#" + h for h in tags)
        caption = "\n\n".join(x for x in (clip.get("description", ""), extra, creator.get("post_footer", "")) if x)
        return caption, tags
    credit_name = str(creator.get("credit_name") or "").strip()
    if credit_name and clip.get("rendered_credit_name") != credit_name:
        raise CreditError("Posting blocked: render this clip again to include the required creator name on screen.")
    handle_key = "credit_tiktok_handle" if platform == "tiktok" else "credit_handle"
    handle = token(creator.get(handle_key), "@", "Creator mention")
    credit_tag = token(creator.get("credit_hashtag"), "#", "Creator hashtag")
    if not handle or not credit_tag:
        raise CreditError("Posting blocked: set the required Creator mention and Creator hashtag in this creator's Setup.")
    game = game or creator.get("game") or ""
    game_tags = games.tags_of(game)
    if games.name_of(game) == "Grand Theft Auto VI":
        game_tags = ["GTA6", "GTAVI", "GrandTheftAutoVI", "RockstarGames"]
    required = hashtags([credit_tag, *game_tags, *hashtags(creator.get("post_hashtags", "")),
                         *(["Shorts"] if platform == "youtube" else [])])
    if required_hashtags is not None:
        required = hashtags([credit_tag, *hashtags(required_hashtags)])
    optional = hashtags(clip.get("hashtags", [])) if required_hashtags is None and not clip.get("fixed_description") and not clip.get("hashtags_in_description") else []
    tags = hashtags([*required, *optional])[:max_tags]
    included = {tag.casefold() for tag in tags}
    if any(tag.casefold() not in included for tag in required):
        raise CreditError(f"Posting blocked: too many required hashtags. Use at most {max_tags} including the creator hashtag.")
    def clean(text):
        text = re.sub(r"[<>]", "", str(text or ""))
        if required_hashtags is not None:
            text = re.sub(r"#\w+", "", text)
        return text.strip()
    footer = clean(creator.get("post_footer", ""))
    tail = "\n\n".join(x for x in (f"Credit: {credit_name} ({handle})" if credit_name else f"Credit: {handle}", footer, " ".join("#" + t for t in tags)) if x)
    limit = 4900 if platform == "youtube" else 2200
    if len(tail) > limit:
        raise CreditError("Posting blocked: required credit and hashtags exceed this platform's description limit.")
    body = clean(clip.get("description", ""))
    if platform != "youtube":
        body = "\n\n".join(x for x in (clean(clip.get("title") or clip.get("hook")), body) if x)
    room = limit - len(tail) - 2
    body = body[:max(0, room)].rstrip()
    caption = (body + "\n\n" if body else "") + tail
    # Match whole tokens: @creator2 / #creator2 cannot substitute for @creator / #creator.
    for required_token in (handle, *("#" + tag for tag in required)):
        if not re.search(r"(?<![\w@#.-])" + re.escape(required_token) + r"(?![\w.-])", caption, re.IGNORECASE):
            raise CreditError("Posting blocked: required creator credit or hashtags are missing from the final description.")
    return caption, tags
