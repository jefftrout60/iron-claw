#!/usr/bin/env python3
"""
summarizer.py — OpenAI-powered episode summarizer for the podcast-summary skill.

Provides two public functions:
    summarize(episode_dict, transcript_text, summary_style, depth, api_key, model) -> str
    classify_show_style(show_title, show_description, api_key, model) -> str

All HTTP done via urllib.request (no openai package, no requests).
Reads OPENAI_API_KEY from agents/sample-agent/.env when not passed explicitly.

Usage (CLI test mode):
    python3 summarizer.py --style deep_science --title "Test Episode" --transcript "test text"
    python3 summarizer.py --classify --title "Huberman Lab" --description "Neuroscience podcast"
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# .env helpers — reuses same pattern as engine.py
# ---------------------------------------------------------------------------

def _find_repo_root() -> Path:
    current = Path(__file__).resolve().parent
    for candidate in [current, *current.parents]:
        if (candidate / "CLAUDE.md").exists():
            return candidate
    raise FileNotFoundError(
        "Cannot locate ironclaw repo root (no CLAUDE.md found walking up from "
        f"{Path(__file__).resolve()})"
    )


def _load_env(agent_name: str = "sample-agent") -> dict:
    import os as _os

    _KNOWN_KEYS = (
        "OPENAI_API_KEY", "PODCAST_SUMMARY_MODEL",
        "DIGEST_TO_EMAIL", "SMTP_FROM_EMAIL", "GMAIL_APP_PASSWORD",
    )
    env_from_environ = {k: _os.environ[k] for k in _KNOWN_KEYS if k in _os.environ}
    if env_from_environ.get("OPENAI_API_KEY"):
        return env_from_environ

    # Fall back to .env file — works on host
    env: dict = {}
    try:
        env_path = _find_repo_root() / "agents" / agent_name / ".env"
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1]
                env[key] = value
    except (OSError, FileNotFoundError):
        pass
    return env


# ---------------------------------------------------------------------------
# OpenAI API call — urllib only, no external packages
# ---------------------------------------------------------------------------

def call_openai(
    prompt: str,
    system_prompt: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    max_tokens: int = 2000,
) -> str:
    """Call OpenAI Chat Completions and return the assistant message content."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.load(resp)
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        raise RuntimeError(
            f"OpenAI API error {e.code}: {e.reason}. Body: {body[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"OpenAI network error: {e.reason}") from e


# ---------------------------------------------------------------------------
# Per-show extra instructions appended to the user prompt.
# Key: lowercase substring of the show title.  Value: instruction string.
_SHOW_EXTRA_INSTRUCTIONS: dict[str, str] = {
    "triggernometry": (
        "\n\nTriggernometry always ends with the hosts asking the guest: "
        "\"What is the one thing we are not talking about that we should be?\" "
        "Find the guest's answer to that question and append a final section to your summary "
        "titled exactly \"The Unasked Question:\" followed by their answer. "
        "If the transcript does not contain the answer, omit the section entirely."
    ),
    "vomradio": (
        "\n\nVOM Radio episodes always end with the host asking: "
        "\"How can we equip our listeners to pray?\" "
        "Find that question and the guest's answer and append a final section to your summary "
        "titled exactly \"How to Pray:\" followed by their answer in full. "
        "If the transcript does not contain the answer, omit the section entirely."
    ),
    "rokcast": (
        "\n\nFor any gear, products, or equipment discussed: list every specific item "
        "by name — brand, model, and any key details mentioned (price, weight, feature). "
        "Do NOT group them into a single sentence like 'they discussed suppressors and stoves'. "
        "Each product deserves its own bullet point. If a comparison or strong opinion was "
        "given about a product, include it."
    ),
    "peter attia": (
        "\n\nIf this is an AMA (Ask Me Anything) episode — indicated by 'AMA' in the title — "
        "structure the entire summary as a Q&A: list every question asked and provide "
        "Peter's full answer for each one. Do not collapse multiple questions into a paragraph. "
        "Format each entry as:\n**Q: [question]**\nA: [Peter's answer]\n\n"
        "If this is NOT an AMA episode, summarize normally."
    ),
    "foundmyfitness": (
        "\n\nIf this is an AMA (Ask Me Anything) episode — indicated by 'AMA' in the title — "
        "structure the entire summary as a Q&A: list every question asked and provide "
        "Rhonda's full answer for each one. Do not collapse multiple questions into a paragraph. "
        "Format each entry as:\n**Q: [question]**\nA: [Rhonda's answer]\n\n"
        "If this is NOT an AMA episode, summarize normally."
    ),
    "hunt backcountry": (
        "\n\nIf this is a Monday Minisode — indicated by 'MM' in the title — it is a listener "
        "Q&A episode. Structure the entire summary as a Q&A: list every listener question and "
        "provide the host's full answer for each one. Do not collapse multiple questions into a "
        "paragraph. Format each entry as:\n**Q: [listener question]**\nA: [host's answer]\n\n"
        "If this is NOT an MM episode, summarize normally as a long-form interview."
    ),
}


# ---------------------------------------------------------------------------
# Per-style prompt builders
# ---------------------------------------------------------------------------

def _build_prompt(
    summary_style: str,
    show: str,
    title: str,
    transcript: str,
    depth: str,
    source_quality: str = "",
    summary_paragraphs: int = 0,
    show_notes: str = "",
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given style.

    Transcript is pre-truncated by the caller — we use it as-is here.
    """
    extended_suffix = ""
    if depth == "extended":
        extended_suffix = (
            "\n\nProvide a more detailed summary than usual. "
            "Include specific quotes, statistics, or data points mentioned. "
            "Aim for 6-8 paragraphs with depth."
        )

    qa_suffix = ""
    if "q&a" in title.lower():
        qa_suffix = (
            "\n\nThis episode has 'Q&A' in the title. Structure the summary as a Q&A: "
            "list every question asked and provide the full answer for each one. "
            "Do not collapse multiple questions into a paragraph. "
            "Format each entry as:\n**Q: [question]**\nA: [answer]"
        )

    list_suffix = (
        "\n\nIMPORTANT: If the episode presents any numbered or explicit list of tips, "
        "techniques, steps, gear items, species, recommendations, or rules — enumerate "
        "ALL items in that list individually. Do not abbreviate, collapse, or say "
        "'and more'. Every item must appear. "
        "This also applies to any named framework where a specific number is part of the concept "
        "(e.g. 'the three macronutrients of happiness', 'four idols that won't make you happy', "
        "'five-step protocol') — list every component of that numbered framework explicitly. "
        "Do not refer to the framework by name alone without enumerating its parts."
    )

    show_lower = show.lower()
    show_extra = next(
        (instr for key, instr in _SHOW_EXTRA_INSTRUCTIONS.items() if key in show_lower),
        "",
    )

    topic_map_section = ""
    if show_notes and len(show_notes.strip()) > 200:
        topic_map_section = (
            f"\n\nEpisode description / show notes (use as a topic guide — "
            f"ensure your summary explicitly covers each topic or framework mentioned):\n"
            f"{show_notes.strip()[:1500]}"
        )

    if summary_style == "deep_science":
        large_whisper = "whisper_large" in source_quality
        if summary_paragraphs:
            para_count = str(summary_paragraphs)
        elif large_whisper:
            para_count = "6-8"
        else:
            para_count = "3-4"
        specificity = (
            " Name every specific protocol, supplement, compound, dosage, or intervention"
            " mentioned — do not summarise them as 'a number of protocols' without listing them."
            if large_whisper else ""
        )
        system = (
            "You are a science communicator summarizing health and longevity podcast episodes. "
            "Write in clear, accessible prose that accurately represents the science."
        )
        user = (
            f"Summarize this podcast episode in {para_count} paragraphs covering: "
            "(1) the main topic and why it matters, "
            "(2) key scientific claims or findings discussed, "
            "(3) specific protocols, supplements, or actionable recommendations mentioned, "
            "(4) any studies or experts cited. "
            f"Be specific and concrete — avoid vague generalities.{specificity}"
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    elif summary_style == "long_form_interview":
        system = (
            "You are summarizing a long-form podcast interview. "
            "Assess the depth and importance of the content and scale your summary accordingly."
        )
        user = (
            "Summarize this podcast episode. "
            "If the content is dense and substantive (detailed expertise, specific insights), "
            "write 2-3 paragraphs. "
            "If the content is lighter (casual conversation, entertainment), write 1 tight paragraph."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    elif summary_style == "commentary":
        system = "You are summarizing a commentary or discussion podcast."
        if summary_paragraphs:
            user = (
                f"Write a comprehensive {summary_paragraphs}-paragraph summary of this episode. "
                "Structure your summary to cover: "
                "(1) the central thesis or question being examined and why it matters, "
                "(2) each major argument or position developed, with the key reasoning behind it, "
                "(3) evidence, sources, or references cited to support the arguments, "
                "(4) counterarguments or alternative perspectives addressed, "
                "(5) practical implications, calls to action, or conclusions drawn. "
                "Be thorough — this is meant as a detailed reference summary, not a brief overview."
                f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
                + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
            )
        else:
            user = (
                "Summarize the key arguments, positions, and conclusions from this episode "
                "in 1-2 paragraphs. "
                "Focus on what was actually argued, not just what was discussed."
                f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
                + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
            )

    elif summary_style == "hunting_outdoor":
        system = "You are summarizing a hunting or outdoor sports podcast."
        user = (
            "Identify the main topics covered in this episode and write a short blurb "
            "(1-3 sentences) for each topic actually discussed. "
            "Use these topic categories where relevant: "
            "Species/Hunt Target, Location/Terrain, Tactics & Strategy, Gear & Equipment, "
            "Archery/Shooting, Fitness & Training, Guest Profile, Conservation, Trip Planning. "
            "Only include categories that were meaningfully discussed."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    elif summary_style == "orvis_fly_fishing":
        system = "You are summarizing the Orvis Fly-Fishing Podcast hosted by Tom Rosenbauer."
        user = (
            "This episode has two distinct sections. Summarize each section separately.\n\n"
            "## Part 1: Listener Q&A\n"
            "List every question Tom answers in the Q&A segment. For each question:\n"
            "- State the question clearly (paraphrase if needed)\n"
            "- Summarize Tom's answer in 2-4 sentences\n"
            "Include all questions — do not skip any.\n\n"
            "## Part 2: Guest Interview\n"
            "Summarize the guest interview in 3-6 sentences covering: who the guest is, "
            "the main topics discussed, and the most useful or actionable advice shared."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    elif summary_style == "meateater":
        system = (
            "You are summarizing The MeatEater Podcast with Steve Rinella — "
            "a wide-ranging conversation show about hunting, fishing, wild food, conservation, and the outdoors."
        )
        if summary_paragraphs:
            user = (
                f"Write a {summary_paragraphs}-paragraph summary of this episode as flowing prose. "
                "Cover: who the guest is and their background, the main stories and topics discussed, "
                "any notable opinions or debates, conservation or policy topics raised, "
                "and memorable moments or takeaways. "
                "Write it as a narrative summary a reader could use to decide whether to listen."
                f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
                + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
            )
        else:
            user = (
                "Summarize this episode in 2-3 paragraphs as flowing prose. "
                "Cover: who the guest is, the main stories and topics discussed, "
                "and any notable takeaways or memorable moments."
                f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
                + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
            )

    elif summary_style == "devotional":
        system = "You are summarizing a Christian devotional or Bible teaching podcast."
        user = (
            "Summarize this episode in 2-3 sentences covering: "
            "the scripture passage discussed, "
            "the main spiritual insight or application taught."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    else:
        # Unknown style — fall back to a generic summary
        system = "You are summarizing a podcast episode."
        user = (
            f"Summarize this podcast episode concisely."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + topic_map_section + list_suffix + extended_suffix + qa_suffix
        )

    return system, user


# ---------------------------------------------------------------------------
# Transcript length limits per style (characters, not tokens)
# Approximate: 4 chars ≈ 1 token; 12000 chars ≈ 3000 tokens (safe margin)
# ---------------------------------------------------------------------------

_TRANSCRIPT_LIMITS: dict[str, int] = {
    "deep_science": 12000,
    "long_form_interview": 12000,
    "commentary": 10000,
    "hunting_outdoor": 10000,
    "orvis_fly_fishing": 12000,
    "meateater": 12000,
    "devotional": 6000,
}
_DEFAULT_TRANSCRIPT_LIMIT = 12000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize(
    episode_dict: dict,
    transcript_text: str | None,
    summary_style: str,
    depth: str = "standard",
    api_key: str | None = None,
    model: str | None = None,
    source_quality: str = "",
    summary_paragraphs: int = 0,
    show_notes: str = "",
) -> str:
    """Generate a summary for an episode using the appropriate style prompt.

    Args:
        episode_dict:   Episode metadata dict (must have 'title'; optionally
                        'show_title', 'description', 'full_notes').
        transcript_text: Full transcript string, or None/empty if unavailable.
        summary_style:  One of deep_science, long_form_interview, commentary,
                        hunting_outdoor, meateater, orvis_fly_fishing, devotional.
        depth:          "standard" or "extended" (extended = longer, more detailed).
        api_key:        OpenAI API key. If None, read from .env.
        model:          Model name. If None, read PODCAST_SUMMARY_MODEL from .env,
                        then fall back to "gpt-4o-mini".

    Returns:
        Plain-text summary string.
    """
    env = _load_env()

    if api_key is None:
        api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY not set — add it to agents/sample-agent/.env"
        )

    if model is None:
        model = env.get("PODCAST_SUMMARY_MODEL", "gpt-4o-mini")

    show = episode_dict.get("show_title") or episode_dict.get("show_id", "Unknown Show")
    title = episode_dict.get("title", "Unknown Episode")

    # Resolve content: prefer transcript, fall back to full_notes or description
    content_label = ""
    if transcript_text and transcript_text.strip():
        content = transcript_text
    else:
        content = (
            episode_dict.get("full_notes")
            or episode_dict.get("description")
            or ""
        ).strip()
        if content:
            content_label = "[Summary based on show notes only]\n\n"
        else:
            content = "(No transcript or show notes available.)"
            content_label = "[Summary based on show notes only — no content available]\n\n"

    # Truncate transcript to stay within token budget.
    # For high-quality transcripts (whisper_large / openai_whisper), use a
    # larger window so long Huberman/Attia episodes get meaningful coverage.
    char_limit = _TRANSCRIPT_LIMITS.get(summary_style, _DEFAULT_TRANSCRIPT_LIMIT)
    high_quality = "whisper_large" in source_quality or "openai_whisper" in source_quality
    if high_quality:
        char_limit = max(char_limit, 40000)
    if len(content) > char_limit:
        content = content[:char_limit]

    # Use more output tokens when a long summary is requested
    effective_paragraphs = summary_paragraphs or (6 if high_quality else 0)
    max_tokens = 4000 if effective_paragraphs >= 8 else 2000

    system_prompt, user_prompt = _build_prompt(
        summary_style, show, title, content, depth, source_quality, summary_paragraphs,
        show_notes=show_notes,
    )
    summary = call_openai(user_prompt, system_prompt, api_key, model, max_tokens=max_tokens)

    return content_label + summary


def classify_show_style(
    show_title: str,
    show_description: str = "",
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Classify a podcast show into one of the seven summary style categories.

    Sends the show title and optional description to the LLM and returns one
    of: deep_science, long_form_interview, commentary, hunting_outdoor,
    meateater, orvis_fly_fishing, devotional.

    Defaults to "long_form_interview" if the LLM returns an unrecognised value
    or the call fails.

    Args:
        show_title:       The show's display name.
        show_description: Optional RSS feed description for the show.
        api_key:          OpenAI API key. If None, read from .env.
        model:            Model name. If None, read PODCAST_SUMMARY_MODEL from .env,
                          then fall back to "gpt-4o-mini".

    Returns:
        One of the seven valid style strings.
    """
    valid_styles = {
        "deep_science",
        "long_form_interview",
        "commentary",
        "hunting_outdoor",
        "meateater",
        "orvis_fly_fishing",
        "devotional",
    }
    fallback = "long_form_interview"

    env = _load_env()

    if api_key is None:
        api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        print(
            "[summarizer] WARNING: OPENAI_API_KEY not set — defaulting style to long_form_interview",
            file=sys.stderr,
        )
        return fallback

    if model is None:
        model = env.get("PODCAST_SUMMARY_MODEL", "gpt-4o-mini")

    system = "You are a podcast categorisation assistant. Reply with only the category name — no punctuation, no explanation."
    user = (
        "Classify this podcast into one of these categories based on its title and description: "
        "deep_science (health/science/longevity), "
        "long_form_interview (long conversations with varied guests), "
        "commentary (news/politics/philosophy discussion), "
        "hunting_outdoor (hunting, fishing, outdoor sports), "
        "meateater (MeatEater Podcast — hunting, wild food, conservation with Steve Rinella), "
        "orvis_fly_fishing (Orvis fly-fishing podcast with Tom Rosenbauer), "
        "devotional (Christian/religious teaching). "
        f"Show title: {show_title}. "
        f"Description: {show_description}. "
        "Reply with only the category name."
    )

    try:
        result = call_openai(user, system, api_key, model)
        # Normalise: lowercase, strip whitespace and punctuation
        normalised = result.lower().strip().strip(".,;:")
        if normalised in valid_styles:
            return normalised
        # Try partial match in case model adds surrounding words
        for style in valid_styles:
            if style in normalised:
                return style
        print(
            f"[summarizer] WARNING: unexpected classification '{result}' — defaulting to {fallback}",
            file=sys.stderr,
        )
        return fallback
    except RuntimeError as e:
        print(
            f"[summarizer] WARNING: classification failed ({e}) — defaulting to {fallback}",
            file=sys.stderr,
        )
        return fallback


# ---------------------------------------------------------------------------
# CLI entry point — quick local test
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test summarizer.py from the command line."
    )
    subparsers = parser.add_subparsers(dest="command")

    # summarize sub-command
    sub_sum = subparsers.add_parser("summarize", help="Summarize an episode")
    sub_sum.add_argument("--style", required=True,
                         choices=["deep_science", "long_form_interview", "commentary",
                                  "hunting_outdoor", "meateater", "orvis_fly_fishing",
                                  "devotional"],
                         help="Summary style")
    sub_sum.add_argument("--title", required=True, help="Episode title")
    sub_sum.add_argument("--show", default="Test Show", help="Show name")
    sub_sum.add_argument("--transcript", default="", help="Transcript text")
    sub_sum.add_argument("--depth", default="standard", choices=["standard", "extended"],
                         help="Summary depth")

    # classify sub-command
    sub_cls = subparsers.add_parser("classify", help="Classify a show's style")
    sub_cls.add_argument("--title", required=True, help="Show title")
    sub_cls.add_argument("--description", default="", help="Show description")

    # Legacy flat-args mode for backwards compatibility with the spec example:
    #   python3 summarizer.py --style deep_science --title "..." --transcript "..."
    parser.add_argument("--style",
                        choices=["deep_science", "long_form_interview", "commentary",
                                 "hunting_outdoor", "meateater", "orvis_fly_fishing",
                                 "devotional"])
    parser.add_argument("--title")
    parser.add_argument("--show", default="Test Show")
    parser.add_argument("--transcript", default="")
    parser.add_argument("--depth", default="standard", choices=["standard", "extended"])
    parser.add_argument("--classify", action="store_true",
                        help="Classify show style instead of summarizing")
    parser.add_argument("--description", default="")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Classify mode
    if getattr(args, "classify", False) or getattr(args, "command", None) == "classify":
        title = args.title
        description = getattr(args, "description", "")
        if not title:
            print("ERROR: --title is required for classification", file=sys.stderr)
            sys.exit(1)
        style = classify_show_style(title, description)
        print(f"Classified style: {style}")
        return

    # Summarize mode
    style = getattr(args, "style", None)
    title = getattr(args, "title", None)
    if not style or not title:
        print("ERROR: --style and --title are required", file=sys.stderr)
        sys.exit(1)

    episode = {
        "title": title,
        "show_title": getattr(args, "show", "Test Show"),
    }
    transcript = getattr(args, "transcript", "")
    depth = getattr(args, "depth", "standard")

    summary = summarize(episode, transcript or None, style, depth)
    print(summary)


if __name__ == "__main__":
    main()
