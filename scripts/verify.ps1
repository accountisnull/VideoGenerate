param([ValidateRange(1, 65535)][int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run scripts/setup.ps1 first.' }
@'
import json, sys, time
import httpx
with httpx.Client(base_url='http://127.0.0.1:' + sys.argv[1], timeout=10, trust_env=False) as client:
    health = client.get('/api/health').raise_for_status().json()
    assert health['application'] == 'video-generate-local', 'Wrong application on this port'
    assert health['worker'] == 'online', 'Worker is offline'
    assert all(health['media'].values()), 'FFmpeg or ffprobe is unavailable'
    assert '/assets/index-' in client.get('/').raise_for_status().text, 'Frontend not served'
    job = client.post('/api/checks').raise_for_status().json()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        records = client.get('/api/checks').raise_for_status().json()
        result = next(record for record in records if record['id'] == job['id'])
        if result['state'] in ('succeeded', 'failed'):
            break
        time.sleep(1)
    assert result['state'] == 'succeeded', result
    video = client.get('/api/checks/' + job['id'] + '/video').raise_for_status()
    assert video.headers['content-type'] == 'video/mp4'
    assert video.content[4:8] == b'ftyp', 'Invalid MP4 header'
    partial = client.get('/api/checks/' + job['id'] + '/video', headers={'Range': 'bytes=0-99'})
    assert partial.status_code == 206 and len(partial.content) == 100
    print(json.dumps({'verification': 'passed', 'check_id': job['id'], 'video_bytes': len(video.content), 'production_ready': health['production_ready']}))
'@ | & $pythonPath - $Port
if ($LASTEXITCODE -ne 0) { throw 'End-to-end environment verification failed.' }
