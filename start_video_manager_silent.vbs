Option Explicit

Dim shell, fso, appDir, scriptPath

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = appDir & "\start_video_manager.pyw"

If Not fso.FileExists(scriptPath) Then
    MsgBox "start_video_manager.pyw was not found.", vbCritical, "Startup Error"
    WScript.Quit 1
End If

If TryRun("pyw -3 """ & scriptPath & """") Then
    WScript.Quit 0
End If

If TryRun("pythonw """ & scriptPath & """") Then
    WScript.Quit 0
End If

MsgBox "No windowed Python launcher was found (pyw/pythonw)." & vbCrLf & vbCrLf & _
       "For troubleshooting, run start_video_manager_debug.bat.", vbCritical, "Startup Error"
WScript.Quit 1

Function TryRun(command)
    On Error Resume Next
    shell.Run command, 0, False
    TryRun = (Err.Number = 0)
    Err.Clear
    On Error GoTo 0
End Function
