import os
import re
import subprocess
import tempfile
import json
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

# Simple shared-secret auth so randoms on the internet can't hit your endpoint.
# Set API_KEY as an env var on Railway, then send it as `x-api-key` header from n8n.
API_KEY = os.environ.get("API_KEY")


class TranscriptRequest(BaseModel):
    videoUrl: str = None
    videoId: str = None
    lang: str = "en"


def extract_video_id(url: str) -> str:
    match = re.search(
        r"(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})",
        url,
    )
    if not match:
        raise HTTPException(status_code=400, detail="Could not extract video ID from URL")
    return match.group(1)


def vtt_to_plain_text(vtt_content: str) -> str:
    """Strip VTT timestamps/formatting down to plain, deduplicated text."""
    lines = vtt_content.splitlines()
    text_lines = []
    seen = set()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("WEBVTT"):
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d+$", line):
            continue
        # strip inline tags like <00:00:01.000><c> word</c>
        clean = re.sub(r"<[^>]+>", "", line)
        clean = clean.strip()
        if clean and clean not in seen:
            text_lines.append(clean)
            seen.add(clean)

    return " ".join(text_lines)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/transcript")
def get_transcript(req: TranscriptRequest, x_api_key: str = Header(default=None)):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if req.videoId:
        video_id = req.videoId
    elif req.videoUrl:
        video_id = extract_video_id(req.videoUrl)
    else:
        raise HTTPException(status_code=400, detail="Provide videoUrl or videoId")

    video_url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        out_template = os.path.join(tmpdir, "%(id)s.%(ext)s")

        cmd = [
            "yt-dlp",
            "--write-auto-sub",
            "--write-sub",
            "--sub-lang", req.lang,
            "--skip-download",
            "--sub-format", "vtt",
            "-o", out_template,
            video_url,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            raise HTTPException(
                status_code=502,
                detail=f"yt-dlp failed: {result.stderr[-500:]}",
            )

        vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
        if not vtt_files:
            raise HTTPException(
                status_code=404,
                detail="No subtitles found for this video/language",
            )

        vtt_path = os.path.join(tmpdir, vtt_files[0])
        with open(vtt_path, "r", encoding="utf-8") as f:
            vtt_content = f.read()

        plain_text = vtt_to_plain_text(vtt_content)

        return {
            "videoId": video_id,
            "videoUrl": video_url,
            "lang": req.lang,
            "transcript": plain_text,
        }