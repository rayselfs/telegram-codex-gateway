Option Explicit
Dim shell, fs, script, config, quote
If WScript.Arguments.Count <> 1 Then WScript.Quit 2
Set shell=CreateObject("WScript.Shell")
Set fs=CreateObject("Scripting.FileSystemObject")
quote=Chr(34)
script=fs.BuildPath(fs.GetParentFolderName(WScript.ScriptFullName),"Start.ps1")
config=WScript.Arguments(0)
If InStr(config,quote)>0 Then WScript.Quit 2
shell.Run "powershell.exe -NoProfile -NonInteractive -File " & quote & script & quote & " -Config " & quote & config & quote,0,False
