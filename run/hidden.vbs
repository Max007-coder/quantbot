' Starts the bot with no window at all. Used by start_bot.bat and by the
' Startup shortcut, so the app keeps running with no console to babysit.
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = root
sh.Run "cmd /c """ & root & "\run\bot.cmd""", 0, False
