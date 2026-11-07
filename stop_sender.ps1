# Stops the hidden AutoDoc sender (used by restart_sender.bat)
Get-CimInstance Win32_Process -Filter "name='pythonw.exe'" |
  Where-Object { $_.CommandLine -like '*autodoc_sender*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "Stopped old sender (PID $($_.ProcessId))" }
