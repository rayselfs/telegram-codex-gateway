Option Explicit
Dim shell
Set shell = CreateObject("WScript.Shell")
WScript.Quit shell.Run("powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -File ""D:\CodexTelegram\bridge\Start.ps1""", 0, True)
