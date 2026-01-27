import requests
import time
import json
import os
import zipfile
import io

BASE_URL = "http://localhost:5000"

def test_full_flow():
    print("1. Starting Export...")
    # Use a channel that has few videos or limit max_videos
    payload = {
        "channel_url": "https://www.youtube.com/@GoogleDeepMind", 
        "max_videos": 3,
        "process_transcripts": True
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/start", json=payload)
        response.raise_for_status()
        session_id = response.json().get('session_id')
        print(f"   Session ID: {session_id}")
    except Exception as e:
        print(f"FAILED to start export: {e}")
        return

    print("\n2. Monitoring Progress...")
    channel_name = None
    
    # Simulate listener
    with requests.get(f"{BASE_URL}/api/progress/{session_id}", stream=True) as r:
        for line in r.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data: '):
                    data_str = decoded_line[6:]
                    try:
                        data = json.loads(data_str)
                        
                        if 'status' in data:
                            print(f"   Status: {data['status']}")
                        
                        if 'channel_name' in data:
                            channel_name = data['channel_name']
                            
                        if data.get('complete'):
                            print("   √ Export Complete!")
                            break
                            
                        if data.get('error'):
                            print(f"   X Error in progress: {data['error']}")
                            return
                            
                    except json.JSONDecodeError:
                        pass

    if not channel_name:
        print("FAILED: Could not determine channel name.")
        return

    print(f"\n3. Testing ZIP Download for '{channel_name}'...")
    download_url = f"{BASE_URL}/api/download/{channel_name}"
    
    try:
        r = requests.get(download_url)
        r.raise_for_status()
        
        # Verify it's a valid zip
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            print("   √ ZIP file downloaded and valid.")
            print("   Contents:")
            for info in z.infolist():
                print(f"     - {info.filename}")
                
            # Check for specific files
            files = z.namelist()
            if "_INDEX.md" in files:
                print("   √ _INDEX.md found")
            else:
                print("   X _INDEX.md MISSING")
                
    except Exception as e:
        print(f"FAILED to download ZIP: {e}")

if __name__ == "__main__":
    try:
        # Check if server is up
        r = requests.get(BASE_URL)
        if r.status_code == 200:
            print("Server is running.")
            test_full_flow()
        else:
            print(f"Server returned {r.status_code}")
    except requests.exceptions.ConnectionError:
        print("Server is NOT running. Please start app.py first.")
