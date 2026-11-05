Option Explicit
Dim shell, command, owner
If WScript.Arguments.Count <> 1 Then WScript.Quit 2
owner = WScript.Arguments(0)
If Not IsNumeric(owner) Then WScript.Quit 2
Set shell = CreateObject("WScript.Shell")
command = """D:\CodexTelegram\runtime\gateway-venv\Scripts\python.exe"" -B ""D:\CodexTelegram\bridge\schedule_worker.py"" --owner " & owner
WScript.Quit shell.Run(command, 0, True)
