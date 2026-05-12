@echo off
REM =============================================================================
REM  Gelegram Gateway Launcher (batch wrapper for NSSM)
REM =============================================================================
REM  NSSM has trouble with spaces in Application/AppParameters paths.
REM  This batch file acts as a simple wrapper that NSSM can call reliably,
REM  since the batch file itself handles the quoting correctly.
REM
REM  NSSM registers THIS file as the Application (no spaces in the args),
REM  and this file launches the actual Python gateway with properly quoted paths.
REM
REM  MICROSOFT ACCOUNT (MSA) FIX:
REM  When the service user is signed in with an email / Microsoft account,
REM  Windows may not set USERPROFILE / APPDATA correctly in the service token.
REM  This causes gemini CLI to fail finding ~/.gemini credentials.
REM  We detect the mismatch and repair the env before launching Python.
REM =============================================================================

REM Change to the script directory (handles spaces in path)
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM  Repair USERPROFILE / APPDATA if they still point at the system profile.
REM  This happens when the service account is a Microsoft / email account and
REM  Windows did not load the user's registry hive into the service token.
REM ---------------------------------------------------------------------------

REM Read the real profile path for the ObjectName user stored by install_service.ps1.
REM install_service.ps1 writes the correct USERPROFILE into a small config file
REM next to this .bat so we can restore it here without hard-coding a username.
IF EXIST "%~dp0service_userprofile.txt" (
    SET /P REAL_USERPROFILE=<"%~dp0service_userprofile.txt"
) ELSE (
    REM Fallback: trust whatever USERPROFILE is already set
    SET REAL_USERPROFILE=%USERPROFILE%
)

REM Only override if USERPROFILE looks like a system/LocalSystem path
IF /I "%USERPROFILE%"=="C:\Windows\system32\config\systemprofile" (
    SET USERPROFILE=%REAL_USERPROFILE%
    SET HOMEPATH=\Users\%USERNAME%
    SET HOMEDRIVE=C:
    SET APPDATA=%REAL_USERPROFILE%\AppData\Roaming
    SET LOCALAPPDATA=%REAL_USERPROFILE%\AppData\Local
)
IF /I "%USERPROFILE%"=="C:\Windows\SysWOW64\config\systemprofile" (
    SET USERPROFILE=%REAL_USERPROFILE%
    SET HOMEPATH=\Users\%USERNAME%
    SET HOMEDRIVE=C:
    SET APPDATA=%REAL_USERPROFILE%\AppData\Roaming
    SET LOCALAPPDATA=%REAL_USERPROFILE%\AppData\Local
)

REM If USERPROFILE is still empty for any reason, fall back to REAL_USERPROFILE
IF "%USERPROFILE%"=="" (
    SET USERPROFILE=%REAL_USERPROFILE%
    SET APPDATA=%REAL_USERPROFILE%\AppData\Roaming
    SET LOCALAPPDATA=%REAL_USERPROFILE%\AppData\Local
)

REM Launch gateway.py using the project's venv Python
REM %~dp0 expands to the directory containing this .bat file (with trailing \)
"%~dp0.venv\Scripts\python.exe" "%~dp0gateway.py"
