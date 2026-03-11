Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$PSScriptRoot'; python -m uvicorn app.main:app --host 0.0.0.0 --port 8888 --reload"
