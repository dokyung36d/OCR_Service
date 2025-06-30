import os
import boto3
import uuid
import zipfile
import shutil
from datetime import datetime

    


s3_client = None

def get_s3_client():
    global s3_client
    if s3_client is not None:
        return s3_client

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"]
    )
    return s3_client


def upload_file_to_s3(file_path):
    s3_client = get_s3_client()

    log_filename = generate_log_filename(file_path)

    s3_client.upload_file(
        Filename = file_path,
        Bucket = os.environ["AWS_S3_BUCKET_NAME"],
        Key = log_filename
    )

def download_file_from_s3(s3_url: str) -> bytes:
    # s3://bucket/key 형식 처리
    if not s3_url.startswith("s3://"):
        raise ValueError("Invalid S3 URL format")

    _, path = s3_url.split("s3://", 1)
    bucket, key = path.split("/", 1)

    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket, Key=key)
    return response['Body'].read()



def delete_files_in_directory(folder_path: str):
    if not os.path.isdir(folder_path):
        raise ValueError(f"{folder_path}는 유효한 디렉토리가 아닙니다.")

    shutil.rmtree(folder_path)        # 폴더 자체 삭제
    os.makedirs(folder_path)          # 폴더 다시 생성 (비워진 상태로)



def generate_log_filename(file_path):
    timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
    unique_id = str(uuid.uuid4())
    filename_without_exstension = get_filename_without_extension(file_path)
    file_extension = get_extension_from_file_path(file_path)

    log_filename = f"{timestamp}_{filename_without_exstension}_{unique_id}{file_extension}"
    return log_filename

def zip_folder(input_folder: str, output_zip_path: str):
    with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for foldername, subfolders, filenames in os.walk(input_folder):
            for filename in filenames:
                file_path = os.path.join(foldername, filename)
                arcname = os.path.relpath(file_path, input_folder)
                zipf.write(file_path, arcname)

def get_filename_without_extension(file_path : str):
    filename = os.path.basename(file_path)
    basename, _ = os.path.splitext(filename)
    return basename

def get_extension_from_file_path(file_path : str):
    _, ext = os.path.splitext(file_path)

    return ext

if __name__ == "__main__":
    log_filename = generate_log_filename("./pdf/1706.03762v7.zip")
    # zip_folder("output", "./compressed/" + generate_log_filename("logs.zip"))
    # delete_files_in_directory("./compressed/")
    # upload_file("./pdf/1706.03762v7.pdf")
    print(log_filename)

    os.makedirs(os.environ["LOG_FOLDER_PATH"], exist_ok = True)
    zip_folder(os.environ["LOG_FOLDER_PATH"], os.environ["OUTPUT_ZIP_FILE_PATH"])