from moviepy import *
import numpy as np
from fastapi import FastAPI, Body, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import requests
import os
import uuid
from pathlib import Path
from moviepy import *
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Add CORS middleware to allow requests from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create directories if they don't exist
os.makedirs("downloads", exist_ok=True)
os.makedirs("output", exist_ok=True)

# Mount the output directory to serve files
app.mount("/videos", StaticFiles(directory="output"), name="videos")

# Base URL for video files - change this to your domain in production
BASE_URL = "http://localhost:8000"

class TranscribeWord(BaseModel):
    word: str = ""
    start: float
    end: float

class ReplaceItem(BaseModel):
    find: str
    replace: str

class TextSettings(BaseModel):
    line_color: str
    word_color: str
    all_caps: bool
    max_words_per_line: int
    font_size: int
    bold: bool
    italic: bool
    underline: bool
    strikeout: bool
    outline_width: int
    shadow_offset: int
    style: str
    font_family: str
    position: str

class VideoRequest(BaseModel):
    video_url: str
    transcribe: List[TranscribeWord]
    replace: List[ReplaceItem]
    settings: TextSettings

def download_video(url: str) -> str:
    """
    Download video from URL and return the local file path.
    """
    try:
        # Generate unique filename
        filename = f"downloads/{uuid.uuid4()}.mp4"
        
        # Stream download to avoid loading large files into memory
        with requests.get(url, stream=True) as response:
            response.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        
        return filename
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to download video: {str(e)}")

def process_transcription(video_path: str, transcribe: List[TranscribeWord], 
                          replace_words: List[ReplaceItem], settings: TextSettings) -> str:
    """
    Process video by dividing it according to transcription timestamps and adding text overlays.
    """
    # Load video
    video = VideoFileClip(video_path)
    # Track all created clips
    clips = []
    
    # Word replacements dictionary for censoring
    replacements = {item.find: item.replace for item in replace_words}

    # Process each transcription item
    previous_end_time = 0
    
    for i, word_data in enumerate(transcribe):
        start_time = word_data.start
        end_time = word_data.end
        
        # Skip invalid time ranges
        if end_time <= start_time or start_time < 0 or end_time > video.duration:
            continue
            
        # Check if there's a gap between previous word and current word
        if previous_end_time > 0 and start_time > previous_end_time:
            # Create a silent clip for the gap
            gap_clip = video.subclipped(previous_end_time, start_time)
            clips.append(gap_clip)
            print(f"Added silent gap clip from {previous_end_time} to {start_time}")
        
        # Update previous_end_time for the next iteration
        previous_end_time = end_time
        
        # For empty words, add the clip without text overlay
        if not word_data.word:
            word_clip = video.subclipped(start_time, end_time)
            clips.append(word_clip)
            continue
            
        # Get word, applying replacements if needed
        display_word = word_data.word
        
        for find, replace in replacements.items():
            if find.lower() in display_word.lower():
                display_word = replace
                
        # Apply text formatting from settings
        if settings.all_caps:
            display_word = display_word.upper()
        
        word_clip = video.subclipped(start_time, end_time)
        # Create text clip
        txt_clip = TextClip(
            text=display_word,
            font = "./font/font.ttf",
            font_size=settings.font_size,
            color=settings.word_color,
            duration=end_time - start_time
        )
        # Position text based on settings
        # if settings.position == "middle_center":
        #     txt_clip = txt_clip.text_align("center")
        # elif settings.position == "bottom_center":
        #     txt_clip = txt_clip.text_align("center")
        # elif settings.position == "top_center":
        #     txt_clip = txt_clip.text_align("center")
        # Set duration to match the word's duration
        # Combine video and text
        composite = CompositeVideoClip([word_clip, txt_clip])
        clips.append(composite)
    
    # Concatenate all clips
    if clips:
        final_clip = concatenate_videoclips(clips)
        
        # Generate output filename with UUID
        output_filename = f"{uuid.uuid4()}.mp4"
        output_path = f"output/{output_filename}"
        
        # Write final video
        final_clip.write_videofile(output_path, codec="libx264")
        
        # Close clips to release resources
        final_clip.close()
        for clip in clips:
            clip.close()
        video.close()
        
        return output_filename
    else:
        video.close()
        raise HTTPException(status_code=400, detail="No valid word segments found in transcription")

@app.post("/v1/video/caption")
async def process_video_action(data: VideoRequest):
    # Download the video file
    local_video_path = download_video(data.video_url)
    try:
        # Process the video with MoviePy
        output_filename = process_transcription(
            local_video_path, 
            data.transcribe, 
            data.replace, 
            data.settings
        )
        
        # Generate the full URL for the processed video
        video_url = f"{BASE_URL}/videos/{output_filename}"
        
        response = {
            "status": "success", 
            "message": f"Processed video with {len(data.transcribe)} words",
            "output_path": f"output/{output_filename}",
            "video_url": video_url
        }
        
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing video: {str(e)}")
    finally:
        # Clean up the downloaded file
        try:
            if os.path.exists(local_video_path):
                os.remove(local_video_path)
        except:
            pass