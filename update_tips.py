#!/usr/bin/env python3
"""
update_tips.py — Daily Reset content pipeline.

Usage:
  python update_tips.py
  python update_tips.py --dry-run
  python update_tips.py --video-id VIDEO_ID
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from googleapiclient.discovery import build as build_youtube
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)
from openai import OpenAI

from config import (
    CHANNELS,
    DEFAULT_LOOKBACK_HOURS,
    MAX_TRANSCRIPT_CHARS,
    OPENAI_MAX_TOKENS,
    OPENAI_MODEL,
    OPENAI_TEMPERATURE,
    STATE_FILE_PATH,
    TIPS_JSON_PATH,
    TIPS_PER_VIDEO,
    PROJECT_ROOT,
)
from prompts import SYSTEM_PROMPT, build_user_prompt

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daily-reset")


# ── State management ──
def load_state():
    if STATE_FILE_PATH.exists():
        try:
            return json.loads(STATE_FILE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Corrupt state file — starting fresh.")
    return {}


def save_state(state):
    STATE_FILE_PATH.write_text(json.dumps(state, indent=2) + "\n")


# ── Step 1: Find new videos ──
def fetch_new_videos(youtube, channel_id, published_after):
    try:
        response = (
            youtube.search()
            .list(
                channelId=channel_id,
                publishedAfter=published_after,
                order="date",
                type="video",
                part="id,snippet",
                maxResults=5,
            )
            .execute()
        )
    except Exception as exc:
        log.error("YouTube API error for channel %s: %s", channel_id, exc)
        return []

    videos = []
    for item in response.get("items", []):
        vid_id = item["id"].get("videoId")
        title = item["snippet"].get("title", "")
        if vid_id:
            videos.append({"video_id": vid_id, "title": title})
    return videos


# ── Step 2: Get transcript ──
def fetch_transcript(video_id):
    try:
        entries = YouTubeTranscriptApi.get_transcript(
            video_id, languages=["en", "en-US", "en-GB"]
        )
    except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as exc:
        log.warning("Transcript unavailable for %s: %s", video_id, type(exc).__name__)
        return None
    except Exception as exc:
        log.warning("Unexpected transcript error for %s: %s", video_id, exc)
        return None

    full_text = " ".join(entry["text"] for entry in entries)
    if len(full_text) > MAX_TRANSCRIPT_CHARS:
        log.info("Transcript trimmed from %d to %d chars.", len(full_text), MAX_TRANSCRIPT_CHARS)
        full_text = full_text[:MAX_TRANSCRIPT_CHARS]
    return full_text


# ── Step 3: Extract tips via LLM ──
VALID_CATEGORIES = {
    "Sleep", "Focus", "Anxiety", "Relationships", "Wealth",
    "Mindset", "Health", "Nutrition", "Fitness", "Digital Detox",
    "Productivity",
}


def extract_tips(client, transcript, video_title, channel_name):
    system = SYSTEM_PROMPT.replace("{tips_per_video}", str(TIPS_PER_VIDEO))
    user = build_user_prompt(transcript, video_title, channel_name)

    try:
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            temperature=OPENAI_TEMPERATURE,
            max_tokens=OPENAI_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        log.error("OpenAI API error: %s", exc)
        return []

    raw = response.choices[0].message.content or ""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        tips = json.loads(raw)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON:\n%s", raw[:500])
        return []

    if not isinstance(tips, list):
        log.error("LLM returned non-array JSON.")
        return []

    validated = []
    for tip in tips:
        if not isinstance(tip, dict):
            continue
        category = tip.get("category", "")
        source = tip.get("source", "")
        title = tip.get("title", "")
        content = tip.get("content", "")

        if category not in VALID_CATEGORIES:
            log.warning("Skipping tip with bad category: '%s'", category)
            continue
        if len(content) > 280:
            content = content[:277] + "..."
        if not title or not content or not source:
            continue

        validated.append({
            "category": category,
            "source": source,
            "title": title,
            "content": content,
        })
    return validated


# ── Step 4: Save tips ──
def load_existing_tips():
    if TIPS_JSON_PATH.exists():
        try:
            return json.loads(TIPS_JSON_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            log.warning("Could not parse tips.json — starting fresh.")
    return []


def next_id(existing):
    if not existing:
        return 1
    return max(t.get("id", 0) for t in existing) + 1


def save_tips(all_tips):
    TIPS_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    TIPS_JSON_PATH.write_text(json.dumps(all_tips, indent=2, ensure_ascii=False) + "\n")
    log.info("Wrote %d tips to %s", len(all_tips), TIPS_JSON_PATH)

    ts_path = PROJECT_ROOT / "lib" / "data.ts"
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    ts_path.write_text(generate_data_ts(all_tips))
    log.info("Regenerated %s", ts_path)


def generate_data_ts(tips_list):
    json_blob = json.dumps(tips_list, indent=2, ensure_ascii=False)
    return f'''\
export interface Tip {{
  id: number;
  category: string;
  source: string;
  title: string;
  content: string;
}}

export const tips: Tip[] = {json_blob};

export const categoryColors: Record<string, {{ bg: string; text: string }}> = {{
  "Digital Detox": {{ bg: "bg-violet-100", text: "text-violet-700" }},
  Sleep:          {{ bg: "bg-indigo-100", text: "text-indigo-700" }},
  Anxiety:        {{ bg: "bg-rose-100",   text: "text-rose-700" }},
  Relationships:  {{ bg: "bg-amber-100",  text: "text-amber-700" }},
  Wealth:         {{ bg: "bg-emerald-100", text: "text-emerald-700" }},
  Focus:          {{ bg: "bg-cyan-100",   text: "text-cyan-700" }},
  Mindset:        {{ bg: "bg-teal-100",   text: "text-teal-700" }},
  Health:         {{ bg: "bg-green-100",  text: "text-green-700" }},
  Nutrition:      {{ bg: "bg-lime-100",   text: "text-lime-700" }},
  Fitness:        {{ bg: "bg-orange-100", text: "text-orange-700" }},
  Productivity:   {{ bg: "bg-sky-100",    text: "text-sky-700" }},
}};
'''


# ── Step 5: Deduplication ──
def is_duplicate(new_tip, existing):
    new_title = new_tip["title"].lower().strip()
    new_content = new_tip["content"].lower().strip()
    for tip in existing:
        if tip.get("title", "").lower().strip() == new_title:
            return True
        if tip.get("content", "").lower().strip() == new_content:
            return True
    return False


# ── Orchestrator ──
def process_single_video(openai_client, video_id, video_title, channel_name, existing_tips, dry_run):
    log.info('Processing: "%s" (%s)', video_title, video_id)

    transcript = fetch_transcript(video_id)
    if not transcript:
        log.warning("  Skipping — no transcript available.")
        return []
    log.info("  Transcript fetched (%d chars).", len(transcript))

    raw_tips = extract_tips(openai_client, transcript, video_title, channel_name)
    if not raw_tips:
        log.info("  LLM returned 0 tips.")
        return []
    log.info("  LLM extracted %d tip(s).", len(raw_tips))

    added = []
    current_id = next_id(existing_tips)
    for tip in raw_tips:
        if is_duplicate(tip, existing_tips):
            log.info('  Duplicate skipped: "%s"', tip["title"])
            continue
        tip["id"] = current_id
        current_id += 1
        added.append(tip)
        existing_tips.append(tip)

    if dry_run:
        log.info("  [DRY RUN] Would add %d tip(s):", len(added))
        for t in added:
            log.info("    [%s] %s — %s", t["category"], t["title"], t["content"][:80])
    else:
        log.info("  Added %d new tip(s).", len(added))
    return added


def main():
    parser = argparse.ArgumentParser(description="Daily Reset — tip updater")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--video-id", type=str, default=None)
    args = parser.parse_args()

    yt_key = os.getenv("YOUTUBE_API_KEY")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not yt_key:
        log.error("YOUTUBE_API_KEY not set.")
        sys.exit(1)
    if not openai_key:
        log.error("OPENAI_API_KEY not set.")
        sys.exit(1)

    youtube = build_youtube("youtube", "v3", developerKey=yt_key)
    openai_client = OpenAI(api_key=openai_key)

    existing_tips = load_existing_tips()
    state = load_state()
    total_added = []

    if args.video_id:
        log.info("Force-processing video: %s", args.video_id)
        added = process_single_video(
            openai_client, args.video_id,
            f"Manual ({args.video_id})", "Unknown Channel",
            existing_tips, args.dry_run,
        )
        total_added.extend(added)
    else:
        for channel in CHANNELS:
            ch_id = channel["id"]
            ch_name = channel["name"]
            log.info("Checking channel: %s (%s)", ch_name, ch_id)

            last_checked = state.get(ch_id)
            if last_checked:
                published_after = last_checked
            else:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
                published_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

            videos = fetch_new_videos(youtube, ch_id, published_after)
            if not videos:
                log.info("  No new videos since %s.", published_after)
            else:
                log.info("  Found %d new video(s).", len(videos))

            for video in videos:
                added = process_single_video(
                    openai_client, video["video_id"], video["title"],
                    ch_name, existing_tips, args.dry_run,
                )
                total_added.extend(added)

            state[ch_id] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if total_added and not args.dry_run:
        save_tips(existing_tips)
        save_state(state)
        log.info("Done — %d new tip(s) saved.", len(total_added))
    elif not total_added:
        save_state(state)
        log.info("Done — no new tips to add.")
    else:
        log.info("Dry run complete — %d tip(s) would be added.", len(total_added))


if __name__ == "__main__":
    main()
