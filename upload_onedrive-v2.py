import os
import sys
import glob
import msal
import requests

def main():
    print("==========================================")
    print("🚀 ONEDRIVE UPLOADER SCRIPT: STARTING")
    print("==========================================")
    
    client_id = os.getenv("ONEDRIVE_CLIENT_ID")
    tenant_id = os.getenv("ONEDRIVE_TENANT_ID")
    client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET")
    user_email = os.getenv("ONEDRIVE_USER_EMAIL")
    drive_id = os.getenv("ONEDRIVE_DRIVE_ID")
    folder_name = os.getenv("ONEDRIVE_FOLDER", "Daily Podcasts")

    if not client_id or not tenant_id:
        print("❌ Error: ONEDRIVE_CLIENT_ID or ONEDRIVE_TENANT_ID is not set.")
        sys.exit(1)

    # Find the latest generated MP3 file
    mp3_files = glob.glob("*.mp3")
    if not mp3_files:
        print("❌ Error: No MP3 files found in the current directory to upload.")
        sys.exit(1)
    
    # Sort by modification time to get the newest one
    mp3_files.sort(key=os.path.getmtime, reverse=True)
    file_to_upload = mp3_files[0]
    filename = os.path.basename(file_to_upload)
    file_size = os.path.getsize(file_to_upload)
    print(f"📁 Found file to upload: '{filename}' ({file_size / (1024*1024):.2f} MB)")

    # 1. Acquire Token
    print("🔑 Authenticating via Microsoft Entra ID (Client Credentials)...")
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret
    )
    
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" in result:
        access_token = result["access_token"]
        print("✓ Access Token acquired successfully.")
    else:
        print(f"❌ Error acquiring token: {result.get('error_description', result.get('error'))}")
        sys.exit(1)

    # 2. Construct base URL for drive item creation
    if drive_id:
        base_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_name}/{filename}"
    elif user_email:
        base_url = f"https://graph.microsoft.com/v1.0/users/{user_email}/drive/root:/{folder_name}/{filename}"
    else:
        print("❌ Error: You must specify either ONEDRIVE_DRIVE_ID or ONEDRIVE_USER_EMAIL in your workflow secrets.")
        sys.exit(1)

    # 3. Choose upload strategy based on file size (Microsoft Graph limit is 4MB for simple upload)
    max_simple_upload_size = 4 * 1024 * 1024  # 4 MB
    
    if file_size <= max_simple_upload_size:
        print(f"⚡ File is under 4MB. Performing simple PUT upload...")
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/octet-stream"
        }
        upload_url = f"{base_url}:/content"
        try:
            with open(file_to_upload, "rb") as f:
                data = f.read()
            response = requests.put(upload_url, headers=headers, data=data)
            if response.status_code in [200, 201]:
                print(f"🎉 SUCCESS! File uploaded successfully to OneDrive under folder '{folder_name}'!")
            else:
                print(f"❌ Simple upload failed (Status: {response.status_code}): {response.text}")
                sys.exit(1)
        except Exception as e:
            print(f"❌ Simple upload failed with exception: {e}")
            sys.exit(1)
    else:
        print(f"📦 File is larger than 4MB. Creating large file upload session...")
        session_url = f"{base_url}:/createUploadSession"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }
        body = {
            "item": {
                "@microsoft.graph.conflictBehavior": "replace",
                "name": filename
            }
        }
        
        try:
            session_resp = requests.post(session_url, headers=headers, json=body)
            if session_resp.status_code != 200:
                print(f"❌ Failed to create upload session (Status: {session_resp.status_code}): {session_resp.text}")
                sys.exit(1)
                
            upload_url = session_resp.json()["uploadUrl"]
            print("✓ Upload session created. Beginning chunked upload...")
            
            # Upload file in 4MB chunks
            chunk_size = 4 * 1024 * 1024  # 4MB chunk size (must be a multiple of 327,680 bytes)
            with open(file_to_upload, "rb") as f:
                start = 0
                while start < file_size:
                    chunk = f.read(chunk_size)
                    length = len(chunk)
                    end = start + length - 1
                    
                    chunk_headers = {
                        "Content-Length": str(length),
                        "Content-Range": f"bytes {start}-{end}/{file_size}"
                    }
                    
                    # Perform PUT request for this chunk (upload_url doesn't need auth header)
                    chunk_resp = requests.put(upload_url, headers=chunk_headers, data=chunk)
                    if chunk_resp.status_code not in [200, 201, 202]:
                        print(f"❌ Chunk upload failed at byte {start} (Status: {chunk_resp.status_code}): {chunk_resp.text}")
                        sys.exit(1)
                        
                    progress = (end + 1) / file_size * 100
                    print(f"  → Uploaded bytes {start}-{end} ({progress:.1f}% complete)")
                    start += length
            
            print(f"🎉 SUCCESS! Large file uploaded successfully to OneDrive under folder '{folder_name}'!")
            
        except Exception as e:
            print(f"❌ Large file upload failed with exception: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
