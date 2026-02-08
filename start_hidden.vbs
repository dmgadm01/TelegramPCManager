Set WshShell = CreateObject("WScript.Shell")
WshShell.Run "pythonw.exe """ & Replace(WScript.ScriptFullName, WScript.ScriptName, "") & "bot.py""", 0, False
