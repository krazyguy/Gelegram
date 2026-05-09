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
REM =============================================================================

REM Change to the script directory (handles spaces in path)
cd /d "%~dp0"

REM Launch gateway.py using the project's venv Python
REM %~dp0 expands to the directory containing this .bat file (with trailing \)
"%~dp0.venv\Scripts\python.exe" "%~dp0gateway.py"
