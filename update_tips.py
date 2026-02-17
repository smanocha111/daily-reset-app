cd backend
cp .env.example .env     # Fill in YOUTUBE_API_KEY and OPENAI_API_KEY
pip3 install -r requirements.txt

python3 update_tips.py              # Full pipeline
python3 update_tips.py --dry-run    # Preview without saving
python3 update_tips.py --video-id VIDEO_ID   # Process one specific video
