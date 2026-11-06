Option Explicit
Dim shell, fs, script, config, owner, quote
If WScript.Arguments.Count <> 2 Then WScript.Quit 2
Set shell=CreateObject("WScript.Shell")
Set fs=CreateObject("Scripting.FileSystemObject")
quote=Chr(34)
script=fs.BuildPath(fs.GetParentFolderName(WScript.ScriptFullName),"Run-Worker.ps1")
config=WScript.Arguments(0)
owner=WScript.Arguments(1)
If InStr(config,quote)>0 Or Not IsNumeric(owner) Then WScript.Quit 2
shell.Run "powershell.exe -NoProfile -NonInteractive -File " & quote & script & quote & " -Config " & quote & config & quote & " -Owner " & owner,0,True
