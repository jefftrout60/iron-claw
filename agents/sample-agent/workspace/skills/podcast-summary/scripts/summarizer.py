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
    env_path = _find_repo_root() / "agents" / agent_name / ".env"
    env: dict = {}
    try:
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
    except OSError:
        pass
    return env


# ---------------------------------------------------------------------------
# OpenAI API call — urllib only, no external packages
# ---------------------------------------------------------------------------

def call_openai(prompt: str, system_prompt: str, api_key: str, model: str = "gpt-4o-mini") -> str:
    """Call OpenAI Chat Completions and return the assistant message content."""
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2000,
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
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the given style.

    Transcript is pre-truncated by the caller — we use it as-is here.
    """
    extended_suffix = ""
    if depth == "extended":
        extended_suffix = (
            "\n\nProvide a more detailed summary than usual. "
            "Include specific quotes, statistics, or data points mentioned. "
            "Aim for 4-6 paragraphs with depth."
        )

    show_lower = show.lower()
    show_extra = next(
        (instr for key, instr in _SHOW_EXTRA_INSTRUCTIONS.items() if key in show_lower),
        "",
    )

    if summary_style == "deep_science":
        system = (
            "You are a science communicator summarizing health and longevity podcast episodes. "
            "Write in clear, accessible prose that accurately represents the science."
        )
        user = (
            "Summarize this podcast episode in 3-4 paragraphs covering: "
            "(1) the main topic and why it matters, "
            "(2) key scientific claims or findings discussed, "
            "(3) specific protocols, supplements, or actionable recommendations mentioned, "
            "(4) any studies or experts cited. "
            "Be specific and concrete — avoid vague generalities."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + extended_suffix
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
            + show_extra + extended_suffix
        )

    elif summary_style == "commentary":
        system = "You are summarizing a commentary or discussion podcast."
        user = (
            "Summarize the key arguments, positions, and conclusions from this episode "
            "in 1-2 paragraphs. "
            "Focus on what was actually argued, not just what was discussed."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + extended_suffix
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
            + show_extra + extended_suffix
        )

    elif summary_style == "devotional":
        system = "You are summarizing a Christian devotional or Bible teaching podcast."
        user = (
            "Summarize this episode in 2-3 sentences covering: "
            "the scripture passage discussed, "
            "the main spiritual insight or application taught."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + extended_suffix
        )

    else:
        # Unknown style — fall back to a generic summary
        system = "You are summarizing a podcast episode."
        user = (
            f"Summarize this podcast episode concisely."
            f"\n\nShow: {show}\nEpisode: {title}\nTranscript:\n{transcript}"
            + show_extra + extended_suffix
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
) -> str:
    """Generate a summary for an episode using the appropriate style prompt.

    Args:
        episode_dict:   Episode metadata dict (must have 'title'; optionally
                        'show_title', 'description', 'full_notes').
        transcript_text: Full transcript string, or None/empty if unavailable.
        summary_style:  One of deep_science, long_form_interview, commentary,
                        hunting_outdoor, devotional.
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

    # Truncate transcript to stay within token budget
    char_limit = _TRANSCRIPT_LIMITS.get(summary_style, _DEFAULT_TRANSCRIPT_LIMIT)
    if len(content) > char_limit:
        content = content[:char_limit]

    system_prompt, user_prompt = _build_prompt(summary_style, show, title, content, depth)
    summary = call_openai(user_prompt, system_prompt, api_key, model)

    return content_label + summary


def classify_show_style(
    show_title: str,
    show_description: str = "",
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Classify a podcast show into one of the five summary style categories.

    Sends the show title and optional description to the LLM and returns one
    of: deep_science, long_form_interview, commentary, hunting_outdoor, devotional.

    Defaults to "long_form_interview" if the LLM returns an unrecognised value
    or the call fails.

    Args:
        show_title:       The show's display name.
        show_description: Optional RSS feed description for the show.
        api_key:          OpenAI API key. If None, read from .env.
        model:            Model name. If None, read PODCAST_SUMMARY_MODEL from .env,
                          then fall back to "gpt-4o-mini".

    Returns:
        One of the five valid style strings.
    """
    valid_styles = {
        "deep_science",
        "long_form_interview",
        "commentary",
        "hunting_outdoor",
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
                                  "hunting_outdoor", "devotional"],
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
                                 "hunting_outdoor", "devotional"])
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
