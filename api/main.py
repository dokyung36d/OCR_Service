#!/usr/bin/env python3
"""
MonkeyOCR FastAPI Application
"""

import os
import io
import tempfile
from typing import Optional, List, Dict, Any
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import zipfile
import shutil
import time

from magic_pdf.model.custom_model import MonkeyOCR
from magic_pdf.data.data_reader_writer import FileBasedDataWriter
from parse import single_task_recognition, parse_pdf
import uvicorn

from tools.aws_s3 import *
import yaml, json

class S3Request(BaseModel):
    s3_url: str  # 또는 HttpUrl, but S3는 형식이 좀 달라서 str 추천

# Response models
class TaskResponse(BaseModel):
    success: bool
    task_type: str
    content: str
    message: Optional[str] = None
    config: Optional[str] = None

class ParseResponse(BaseModel):
    success: bool
    message: str
    output_dir: Optional[str] = None
    files: Optional[List[str]] = None
    download_url: Optional[str] = None

# Global model instance
monkey_ocr_model = MonkeyOCR(os.environ["MONKEYOCR_CONFIG_FILE_PATH"])
# monkey_ocr_model = None
executor = ThreadPoolExecutor(max_workers=2)

def initialize_model():
    """Initialize MonkeyOCR model"""
    global monkey_ocr_model
    if monkey_ocr_model is None:
        config_path = os.getenv("MONKEYOCR_CONFIG_FILE_PATH", "model_configs.yaml")
        monkey_ocr_model = MonkeyOCR(config_path)
    return monkey_ocr_model

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler"""
    # Startup
    try:
        initialize_model()
        print("✅ MonkeyOCR model initialized successfully")
    except Exception as e:
        print(f"❌ Failed to initialize MonkeyOCR model: {e}")
        raise
    
    yield
    
    # Shutdown
    global executor
    executor.shutdown(wait=True)
    print("🔄 Application shutdown complete")

app = FastAPI(
    title="MonkeyOCR API",
    description="OCR and Document Parsing API using MonkeyOCR",
    version="1.0.0",
    lifespan=lifespan
)

temp_dir = "/workspace/MonkeyOCR/app/temp"
os.makedirs(temp_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=temp_dir), name="static")

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "MonkeyOCR API is running", "version": "1.0.0"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "model_loaded": monkey_ocr_model is not None}

@app.post("/ocr/text", response_model=TaskResponse)
async def extract_text(file: UploadFile = File(...)):
    """Extract text from image or PDF"""
    return await perform_ocr_task(file, "text")

@app.post("/ocr/text/s3_url")
async def extract_text_from_s3(request : S3Request):
    try : 
        file_bytes = download_file_from_s3(request.s3_url)
        fake_upload = UploadFile(filename="from_s3.pdf", file=io.BytesIO(file_bytes))
    
    except Exception as e:
        return TaskResponse(
            success=False,
            task_type="text",
            content="",
            message=f"Error occurred: {type(e).__name__} - {e}"
        )

    # 기존의 OCR 로직 재사용
    return await perform_ocr_task(fake_upload, "text")
    

@app.post("/ocr/formula", response_model=TaskResponse)
async def extract_formula(file: UploadFile = File(...)):
    """Extract formulas from image or PDF"""
    return await perform_ocr_task(file, "formula")

@app.post("/ocr/table", response_model=TaskResponse)
async def extract_table(file: UploadFile = File(...)):
    """Extract tables from image or PDF"""
    return await perform_ocr_task(file, "table")

@app.post("/parse", response_model=ParseResponse)
async def parse_document(file: UploadFile = File(...)):
    """Parse complete document (PDF only)"""
    try:
        if not monkey_ocr_model:
            raise HTTPException(status_code=500, detail="Model not initialized")
        
        # Validate file type
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Only PDF files are supported for document parsing")
        
        # Get original filename without extension
        original_name = '.'.join(file.filename.split('.')[:-1])
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name
        
        try:
            # Create output directory
            output_dir = tempfile.mkdtemp(prefix="monkeyocr_parse_")
            
            # Run parsing in thread pool
            loop = asyncio.get_event_loop()
            result_dir = await loop.run_in_executor(
                executor, 
                parse_pdf, 
                temp_file_path, 
                output_dir, 
                monkey_ocr_model
            )
            
            # List generated files
            files = []
            if os.path.exists(result_dir):
                for root, dirs, filenames in os.walk(result_dir):
                    for filename in filenames:
                        rel_path = os.path.relpath(os.path.join(root, filename), result_dir)
                        files.append(rel_path)
            
            # Create download URL with original filename
            zip_filename = f"{original_name}_parsed_{int(time.time())}.zip"
            zip_path = os.path.join("/app/tmp", zip_filename)
            
            # Create ZIP file with renamed files
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, filenames in os.walk(result_dir):
                    for filename in filenames:
                        file_path = os.path.join(root, filename)
                        
                        # Create new filename with original name prefix
                        file_ext = os.path.splitext(filename)[1]
                        file_base = os.path.splitext(filename)[0]
                        
                        # Handle different file types
                        if filename.endswith('.md'):
                            new_filename = f"{original_name}.md"
                        elif filename.endswith('_content_list.json'):
                            new_filename = f"{original_name}_content_list.json"
                        elif filename.endswith('_middle.json'):
                            new_filename = f"{original_name}_middle.json"
                        elif filename.endswith('_model.pdf'):
                            new_filename = f"{original_name}_model.pdf"
                        elif filename.endswith('_layout.pdf'):
                            new_filename = f"{original_name}_layout.pdf"
                        elif filename.endswith('_spans.pdf'):
                            new_filename = f"{original_name}_spans.pdf"
                        else:
                            # For images and other files, keep relative path structure but rename
                            rel_path = os.path.relpath(file_path, result_dir)
                            if 'images/' in rel_path:
                                # Keep images in images subfolder with original name prefix
                                image_name = os.path.basename(rel_path)
                                new_filename = f"images/{original_name}_{image_name}"
                            else:
                                new_filename = f"{original_name}_{filename}"
                        
                        zipf.write(file_path, new_filename)
            
            download_url = f"/static/{zip_filename}"
            
            return ParseResponse(
                success=True,
                message="Document parsing completed successfully",
                output_dir=result_dir,
                files=files,
                download_url=download_url
            )
            
        finally:
            # Clean up temporary file
            os.unlink(temp_file_path)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Parsing failed: {str(e)}")

@app.get("/download/{filename}")
async def download_file(filename: str):
    """Download result files"""
    file_path = os.path.join("/app/tmp", filename)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type='application/octet-stream'
    )

@app.post("/logs/upload")
async def upload_logs_to_AWS_S3():
    try:
        os.makedirs(os.environ["LOG_FOLDER_PATH"], exist_ok = True)
        zip_folder(os.environ["LOG_FOLDER_PATH"], os.environ["OUTPUT_ZIP_FILE_PATH"])
        upload_file_to_s3(os.environ["OUTPUT_ZIP_FILE_PATH"])
        
        delete_files_in_directory(os.environ["LOG_FOLDER_PATH"])
    
    except Exception as e:
        return TaskResponse(
            success=False,
            task_type="Log",
            content="",
            message=f"OCR task failed: {str(e)}"
        )

    
    return TaskResponse(
            success=True,
            task_type="Log",
            content="",
            message=f"log files uploaded to AWS S3"
        )






@app.get("/results/{task_id}")
async def get_results(task_id: str):
    """Get parsing results by task ID"""
    result_dir = f"/app/tmp/monkeyocr_parse_{task_id}"
    
    if not os.path.exists(result_dir):
        raise HTTPException(status_code=404, detail="Results not found")
    
    files = []
    for root, dirs, filenames in os.walk(result_dir):
        for filename in filenames:
            rel_path = os.path.relpath(os.path.join(root, filename), result_dir)
            files.append(rel_path)
    
    return {"files": files, "result_dir": result_dir}

async def perform_ocr_task(file: UploadFile, task_type: str) -> TaskResponse:
    """Perform OCR task on uploaded file"""
    try:
        if not monkey_ocr_model:
            raise HTTPException(status_code=500, detail="Model not initialized")
        
        # Validate file type
        allowed_extensions = {'.pdf', '.jpg', '.jpeg', '.png'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"Unsupported file type: {file_ext}. Allowed: {', '.join(allowed_extensions)}"
            )
        
        # Save uploaded file temporarily
        with tempfile.NamedTemporaryFile(delete=False, prefix = Path(file.filename).stem, suffix=file_ext) as temp_file:
            content = await file.read()
            temp_file.write(content)
            temp_file_path = temp_file.name

        # print("before running")
        # upload_file_dir = os.environ["UPLOAD_FOLDER_PATH"]
        # os.makedirs(upload_file_dir, exist_ok = True)
        # filename = generate_log_filename(file.filename)
        # upload_file_path = os.path.join(upload_file_dir, filename)
        # content = await file.read()
        # with open(upload_file_path, "wb") as f:
        #     f.write(content)

        try:
            # Create output directory
            output_dir = tempfile.mkdtemp(prefix=f"monkeyocr_{task_type}_")
            
            # Run OCR task in thread pool
            loop = asyncio.get_event_loop()
            result_dir = await loop.run_in_executor(
                executor,
                single_task_recognition,
                temp_file_path,
                output_dir,
                monkey_ocr_model,
                task_type
            )
            
            # Read result file
            result_files = [f for f in os.listdir(result_dir) if f.endswith(f'_{task_type}_result.md')]
            if not result_files:
                raise Exception("No result file generated")
            
            result_file_path = os.path.join(result_dir, result_files[0])
            with open(result_file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            # with open(os.environ["MONKEYOCR_CONFIG_FILE_PATH"], "r") as f:
            #     config_dict = yaml.safe_load(f)
            
            return TaskResponse(
                success=True,
                task_type=task_type,
                content=content,
                message=f"{task_type.capitalize()} extraction completed successfully",
                config=os.path.basename(os.environ["MONKEYOCR_CONFIG_FILE_PATH"])
            )
            
        finally:
            # Clean up temporary file
            os.unlink(temp_file_path)
            
    except Exception as e:
        return TaskResponse(
            success=False,
            task_type=task_type,
            content="",
            message=f"OCR task failed: {str(e)}"
        )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
