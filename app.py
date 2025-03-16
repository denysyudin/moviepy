from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any
import moviepy.editor as mp
import os
import uuid
import re
from urllib.parse import urlparse
import requests
import uvicorn
import tempfile

app = FastAPI()

# Define your models based on the payload structure
class ReplaceItem(BaseModel):
    find: str
    replace: str

class TextSettings(BaseModel):
    line_color: Optional[str] = "white"
    word_color: Optional[str] = "white"
    all_caps: Optional[bool] = False
    max_words_per_line: Optional[int] = 7
    font_size: Optional[int] = 40
    bold: Optional[bool] = False
    italic: Optional[bool] = False
    underline: Optional[bool] = False
    strikeout: Optional[bool] = False
    outline_width: Optional[int] = 1
    shadow_offset: Optional[int] = 0
    style: Optional[str] = "highlight"
    font_family: Optional[str] = "Arial"
    position: Optional[str] = "middle center"

class VideoTextRequest(BaseModel):
    video_url: HttpUrl
    replace: List[ReplaceItem] = []
    settings: TextSettings
    id: Optional[str] = None
    transcription: Optional[str] = ""

class JobStatus(BaseModel):
    id: str
    status: str
    output_url: Optional[str] = None

# Store job statuses
jobs = {}

def download_video(url: str) -> str:
    """Download a video from a URL and return the local path"""
    # Parse URL to get filename
    parsed_url = urlparse(url)
    filename = os.path.basename(parsed_url.path)
    if not filename:
        filename = f"video_{uuid.uuid4()}.mp4"
    
    # Create temp file
    local_path = os.path.join(tempfile.gettempdir(), filename)
    
    # Download the file
    with requests.get(url, stream=True) as r:
        r.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    
    return local_path

def parse_position(position_str: str):
    """Convert position string to (x, y) coordinates"""
    positions = {
        "top left": ("left", "top"),
        "top center": ("center", "top"),
        "top right": ("right", "top"),
        "middle left": ("left", "center"),
        "middle center": ("center", "center"),
        "middle right": ("right", "center"),
        "bottom left": ("left", "bottom"),
        "bottom center": ("center", "bottom"),
        "bottom right": ("right", "bottom")
    }
    return positions.get(position_str.lower(), ("center", "center"))

def apply_text_transformations(text: str, replace_items: List[ReplaceItem], settings: TextSettings) -> str:
    """Apply text transformations based on settings and replacements"""
    result = text
    
    # Apply replacements
    for item in replace_items:
        result = re.sub(r'\b' + re.escape(item.find) + r'\b', item.replace, result, flags=re.IGNORECASE)
    
    # Apply all caps if needed
    if settings.all_caps:
        result = result.upper()
    
    return result

def process_video(job_id: str, video_url: str, replace_items: List[ReplaceItem], 
                  settings: TextSettings, transcription: str):
    try:
        # Update job status
        jobs[job_id] = {"status": "downloading", "output_url": None}
        
        # Download the video
        local_path = download_video(video_url)
        
        # Update job status
        jobs[job_id]["status"] = "processing"
        
        # Load the video
        video = mp.VideoFileClip(local_path)
        
        # If transcription is empty, we might want to generate it
        # This would require an ASR service, but for this example we'll skip that
        text_to_show = transcription
        
        # Apply text transformations
        processed_text = apply_text_transformations(text_to_show, replace_items, settings)
        
        # Split text into lines based on max_words_per_line
        words = processed_text.split()
        lines = []
        for i in range(0, len(words), settings.max_words_per_line):
            lines.append(" ".join(words[i:i + settings.max_words_per_line]))
        
        # Join lines back together with newlines
        final_text = "\n".join(lines)
        
        # Parse position
        h_pos, v_pos = parse_position(settings.position)
        
        # Create text clip
        txt_clip = mp.TextClip(
            final_text, 
            fontsize=settings.font_size,
            font=settings.font_family,
            color=settings.word_color,
            stroke_color="black",
            stroke_width=settings.outline_width,
            method='caption',
            align='center'
        )
        
        # Set position
        txt_clip = txt_clip.set_position((h_pos, v_pos))
        
        # Set duration to match video
        txt_clip = txt_clip.set_duration(video.duration)
        
        # Composite video with text
        final_video = mp.CompositeVideoClip([video, txt_clip])
        
        # Generate output path
        output_path = os.path.join(tempfile.gettempdir(), f"processed_{job_id}.mp4")
        
        # Write the result to a file
        final_video.write_videofile(output_path, codec='libx264', audio_codec='aac')
        
        # In a real app, you would upload this to a storage service
        # For this example, we'll just use a file path
        output_url = f"file://{output_path}"
        
        # Clean up
        video.close()
        txt_clip.close()
        final_video.close()
        
        # Update job status
        jobs[job_id] = {"status": "completed", "output_url": output_url}
        
    except Exception as e:
        # Update job status with error
        jobs[job_id] = {"status": "failed", "error": str(e)}
        
        # Clean up any resources
        if 'video' in locals():
            video.close()
        if 'txt_clip' in locals():
            txt_clip.close()
        if 'final_video' in locals():
            final_video.close()

@app.post("/process-video/", response_model=JobStatus)
async def create_video_job(request: VideoTextRequest, background_tasks: BackgroundTasks):
    # Generate job id if not provided
    job_id = request.id if request.id else str(uuid.uuid4())
    
    # Store initial job status
    jobs[job_id] = {"status": "queued", "output_url": None}
    
    # Start processing in the background
    background_tasks.add_task(
        process_video,
        job_id=job_id,
        video_url=str(request.video_url),
        replace_items=request.replace,
        settings=request.settings,
        transcription=request.transcription
    )
    
    return {"id": job_id, "status": "queued"}

@app.get("/job-status/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    
    job = jobs[job_id]
    return {"id": job_id, "status": job["status"], "output_url": job.get("output_url")}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000) 