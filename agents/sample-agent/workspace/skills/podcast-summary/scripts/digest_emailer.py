from __future__ import annotations

"""
digest_emailer.py — Build and send the nightly podcast digest email.

Usage (CLI test mode):
  python3 digest_emailer.py --test --to your@email.com
"""

import html as html_module
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path


# Maps source_quality values from transcript_fetcher → human-readable labels
SOURCE_QUALITY_LABELS: dict[str, str] = {
    "published_transcript":   "Full Transcript",
    "third_party_transcript": "3rd-Party Transcript",
    "whisper_large":          "Whisper (high quality)",
    "whisper_small":          "Whisper (standard)",
    "show_notes":             "Show Notes Only",
}


def _format_date(iso_date: str) -> str:
    """Parse ISO 8601 date string and return 'Mon Mar 20, 2026' format."""
    try:
        dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        dt = dt.astimezone()  # convert UTC to local time
        return dt.strftime("%a %b %-d, %Y")
    except (ValueError, AttributeError):
        return iso_date or ""


def _summary_to_html(summary: str, summary_style: str | None = None) -> str:
    """
    Convert plain-text summary to HTML.

    For hunting_outdoor style: lines matching "Topic: blurb" get the topic
    wrapped in <strong>. All styles: double newlines become paragraph breaks.
    """
    if not summary:
        return ""

    # Escape HTML entities before any markup insertion
    escaped = html_module.escape(summary)

    if summary_style == "hunting_outdoor":
        lines = escaped.splitlines()
        formatted_lines = []
        for line in lines:
            # Match "Word(s): rest of line" — topic header pattern
            if ": " in line and not line.startswith(" "):
                topic, _, rest = line.partition(": ")
                if topic and rest:
                    line = f"<strong>{topic}</strong>: {rest}"
            formatted_lines.append(line)
        escaped = "\n".join(formatted_lines)

    # Convert double newlines to paragraph breaks; wrap in <p> tags
    paragraphs = [p.strip() for p in escaped.split("\n\n") if p.strip()]
    if not paragraphs:
        return f"<p>{escaped}</p>"
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paragraphs)


def _episode_card_html(episode: dict) -> str:
    """Render one episode as an HTML card block."""
    show_name = html_module.escape(episode.get("show_name") or episode.get("show_id") or "")
    title = html_module.escape(episode.get("title") or "")
    audio_url = episode.get("audio_url") or "#"
    source_quality = episode.get("source_quality") or ""
    quality_label = SOURCE_QUALITY_LABELS.get(source_quality, source_quality)
    pub_date = _format_date(episode.get("pub_date") or "")
    summary_style = episode.get("summary_style")
    summary_html = _summary_to_html(episode.get("summary") or "", summary_style)

    return (
        '<div style="border-bottom: 1px solid #ddd; padding: 20px 0; margin-bottom: 20px;">\n'
        f'  <h2 style="margin:0 0 4px 0; font-size:18px;">{show_name} — {title}</h2>\n'
        f'  <p style="margin:0 0 12px 0; color:#888; font-size:13px;">'
        f'{html_module.escape(quality_label)} · {pub_date}'
        f"</p>\n"
        f'  <div style="line-height:1.6;">{summary_html}</div>\n'
        f'  <p style="margin:12px 0 0 0;">'
        f'<a href="{html_module.escape(audio_url)}" style="color:#2563eb;">Listen &#8594;</a>'
        f"</p>\n"
        "</div>"
    )


def _footer_html(newsletter_count: int, newsletter_names: list[str] | None) -> str:
    """Render the digest footer. Newsletter line only appears when count > 0."""
    newsletter_line = ""
    if newsletter_count > 0:
        plural = "s" if newsletter_count != 1 else ""
        names_str = html_module.escape(", ".join(newsletter_names or []))
        newsletter_line = (
            f"  <p>&#128218; <strong>Health Archive:</strong> "
            f"{newsletter_count} newsletter{plural} stored"
            + (f" — {names_str}" if names_str else "")
            + "</p>\n"
        )
    return (
        '<div style="border-top: 2px solid #ddd; padding-top: 16px; color:#666; font-size:13px;">\n'
        f"{newsletter_line}"
        "</div>"
    )


def build_digest_html(
    episodes: list[dict],
    newsletter_count: int = 0,
    newsletter_names: list[str] | None = None,
) -> str:
    """
    Build a complete HTML email body for the podcast digest.

    Each episode dict should contain:
      show_name, title, pub_date (ISO), audio_url, source_quality, summary,
      summary_style (optional — affects hunting_outdoor topic formatting)
    """
    today = datetime.now()
    date_str = today.strftime("%a %b %-d")  # e.g. "Sun Mar 22"
    n = len(episodes)
    plural = "s" if n != 1 else ""

    cards_html = "\n".join(_episode_card_html(ep) for ep in episodes)
    footer = _footer_html(newsletter_count, newsletter_names)

    return (
        "<html><body"
        ' style="font-family: Georgia, serif; max-width: 700px;'
        ' margin: 0 auto; padding: 20px; color: #222;">\n'
        '  <h1 style="font-size:22px; border-bottom: 2px solid #222; padding-bottom:12px;">\n'
        f"    &#127897;&#65039; Podcast Digest &mdash; {n} new episode{plural} &middot; {date_str}\n"
        "  </h1>\n"
        f"  {cards_html}\n"
        f"  {footer}\n"
        "</body></html>"
    )


def _find_send_email() -> str:
    """Walk up from this file to find workspace/skills/send-email/scripts/send_email.py."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        candidate = parent / "skills" / "send-email" / "scripts" / "send_email.py"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        "send_email.py not found — searched parents of: " + str(p)
    )


def send_digest(
    episodes: list[dict],
    to_email: str,
    from_email: str | None = None,
    smtp_password: str | None = None,
    newsletter_count: int = 0,
    newsletter_names: list[str] | None = None,
    subject_override: str | None = None,
) -> bool:
    """
    Build and send the digest email via send_email.py.

    Returns True on success, False if skipped (empty episodes list).
    Raises RuntimeError if send_email.py exits non-zero.

    Credentials are passed via environment variables so send_email.py can
    pick them up — the script reads SMTP_FROM_EMAIL and GMAIL_APP_PASSWORD
    from env, then falls back to its own skills/send-email/.env file.

    subject_override: if provided, replaces the default subject line.
    """
    if not episodes:
        print("No new episodes — skipping digest.", flush=True)
        return False

    html_body = build_digest_html(episodes, newsletter_count, newsletter_names)

    if subject_override:
        subject = subject_override
    else:
        today = datetime.now()
        date_str = today.strftime("%a %b %-d")  # e.g. "Sun Mar 22"
        n = len(episodes)
        plural = "s" if n != 1 else ""
        subject = f"\U0001f3a7 Podcast Digest \u2014 {n} new episode{plural} \u00b7 {date_str}"

    send_email_path = _find_send_email()

    # send_email.py requires a file path for the body (not stdin by default)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_body)
        tmp_path = tmp.name

    try:
        env = os.environ.copy()
        # Inject credentials into subprocess env if explicitly provided
        if from_email:
            env["SMTP_FROM_EMAIL"] = from_email
        if smtp_password:
            env["GMAIL_APP_PASSWORD"] = smtp_password

        # CLI: send_email.py <to> <subject> <body-file> [--html]
        result = subprocess.run(
            [sys.executable, send_email_path, to_email, subject, tmp_path, "--html"],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if result.returncode != 0:
        raise RuntimeError(f"Email send failed: {result.stderr.strip()}")

    print(result.stdout.strip(), flush=True)
    return True


# ---------------------------------------------------------------------------
# CLI test mode
# ---------------------------------------------------------------------------

_FAKE_EPISODES: list[dict] = [
    {
        "show_name": "The Peter Attia Drive",
        "show_id": "the-peter-attia-drive",
        "title": "Episode 224: Cardiovascular Risk Factors with Dr. Tom Dayspring",
        "pub_date": "2026-03-20T08:00:00Z",
        "audio_url": "https://example.com/ep224.mp3",
        "source_quality": "whisper_large",
        "summary_style": "deep_science",
        "summary": (
            "Dr. Tom Dayspring returns to discuss the nuanced interpretation of "
            "ApoB as a causal risk factor for atherosclerosis.\n\n"
            "Key claim: ApoB particles — not LDL-C — are the direct cause of "
            "plaque formation. A patient can have 'normal' LDL-C with dangerously "
            "elevated ApoB.\n\n"
            "Actionable: Request ApoB testing alongside standard lipid panels. "
            "Target ApoB below 60 mg/dL for high-risk patients."
        ),
    },
    {
        "show_name": "The Hunt Backcountry Podcast",
        "show_id": "the-hunt-backcountry-podcast",
        "title": "Archery Elk in the Dark Timber",
        "pub_date": "2026-03-19T12:00:00Z",
        "audio_url": "https://example.com/hunt-ep88.mp3",
        "source_quality": "show_notes",
        "summary_style": "hunting_outdoor",
        "summary": (
            "Elk Strategies: Focus on dark timber wallows during archery season — "
            "bulls bed close and respond to cow calls.\n\n"
            "Gear: Lightweight boot options for steep terrain; review of Sitka Mountain "
            "Hauler 2200 pack.\n\n"
            "Guest: Guide from Unit 10 Colorado shares wind thermals timing chart."
        ),
    },
    {
        "show_name": "All-In with Chamath Jason Sacks & Friedberg",
        "show_id": "all-in-with-chamath-jason-sacks-friedberg",
        "title": "E168: AI regulation, tariff fallout, and the 2026 midterm landscape",
        "pub_date": "2026-03-21T10:00:00Z",
        "audio_url": "https://example.com/allin-ep168.mp3",
        "source_quality": "show_notes",
        "summary_style": "commentary",
        "summary": (
            "The besties debate the EU AI Act's chilling effect on US frontier labs, "
            "with Chamath arguing exemptions for open-weight models are too narrow.\n\n"
            "Tariff segment: Friedberg walks through second-order supply chain effects "
            "of steel tariffs on domestic EV battery costs."
        ),
    },
]


def _cli_test(to_email: str) -> None:
    print(f"Sending test digest to {to_email} with {len(_FAKE_EPISODES)} fake episodes…")
    result = send_digest(
        episodes=_FAKE_EPISODES,
        to_email=to_email,
        newsletter_count=2,
        newsletter_names=["Attia Weekly Brief", "FoundMyFitness Digest"],
    )
    if result:
        print("Test digest sent successfully.")
    else:
        print("No episodes — nothing sent.")


def _parse_cli() -> tuple[str, str | None]:
    """Minimal CLI parser. Returns (mode, to_email)."""
    args = sys.argv[1:]
    mode = None
    to_email = None
    i = 0
    while i < len(args):
        if args[i] == "--test":
            mode = "test"
        elif args[i] == "--to" and i + 1 < len(args):
            to_email = args[i + 1]
            i += 1
        i += 1
    return mode or "", to_email


if __name__ == "__main__":
    mode, to_email = _parse_cli()
    if mode == "test":
        if not to_email:
            print("Usage: python3 digest_emailer.py --test --to your@email.com", file=sys.stderr)
            sys.exit(1)
        _cli_test(to_email)
    else:
        print("Usage: python3 digest_emailer.py --test --to your@email.com", file=sys.stderr)
        sys.exit(1)
