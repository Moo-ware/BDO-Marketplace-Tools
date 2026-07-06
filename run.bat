@echo off
setlocal

cd /d "%~dp0"

set "WT_COLUMNS=113"
set "WT_LINES=36"
set "PROJECT_DIR=%CD%"
set "PYTHONDONTWRITEBYTECODE=1"
set "LAUNCH_MARKER=%TEMP%\bdo-marketplace-tools-launch.ok"

REM Where the app actually stores its browser profiles. Data moved out of the repo to a
REM per-user dir, so mirror storage/paths.py precedence here: BDO_DATA_DIR override first,
REM otherwise %LOCALAPPDATA%\bdo-marketplace-tools\data. The :reap sweep below uses this to
REM find and clear orphaned patchright Chrome.
if defined BDO_DATA_DIR (
    set "PROFILE_DIR=%BDO_DATA_DIR%\browser_profiles"
) else (
    set "PROFILE_DIR=%LOCALAPPDATA%\bdo-marketplace-tools\data\browser_profiles"
)

REM --inside-wt marks the second pass that actually runs the app inside the Windows
REM Terminal window. The first pass self-heals and opens the window; it is invisible to
REM the user -- there is only ever one file to run: this one.
set "INSIDE_WT="
if /I "%~1"=="--inside-wt" (
    set "INSIDE_WT=1"
    shift /1
)

set "APP_ARGS="
:collect_args
if "%~1"=="" goto args_done
if /I "%~1"=="--inside-wt" (
    shift /1
    goto collect_args
)
set "APP_ARGS=%APP_ARGS% "%~1""
shift /1
goto collect_args
:args_done

if defined INSIDE_WT goto run_app

REM First pass self-heals before launching. Closing the window with the X button
REM hard-kills the whole process tree and can orphan the patchright Chrome it spawned;
REM those pile up across launches until new windows can no longer be created -- only a
REM logoff or reboot frees them. The first pass always runs, so the X can never skip the
REM sweep. This also makes the app single-instance: relaunching replaces a running copy,
REM which is what you want for a buy bot anyway.
call :reap

if defined WT_SESSION goto run_app
if defined BDO_DISABLE_WT goto run_app
where wt.exe >nul 2>&1
if errorlevel 1 goto run_app

REM wt.exe is fire-and-forget: it reports success even when no window ever appears.
REM Store-app activation can drop the launch, and relaunching right after closing a
REM window hands the command to a Windows Terminal instance that is mid-shutdown, which
REM silently discards it (the "click it five times" failure). So: launch, verify that the
REM second pass actually started (it writes LAUNCH_MARKER as its first action), retry
REM once, and if the window still never materializes run the app in THIS console instead.
REM The fallback reuses the window that is already open rather than asking Windows for a
REM new one, so the app opens even when new-window creation itself is what is failing.
set "WT_ATTEMPTS=0"
:try_wt
set /a WT_ATTEMPTS+=1
if exist "%LAUNCH_MARKER%" del "%LAUNCH_MARKER%" >nul 2>&1
start "Marketplace Tools" wt.exe -w new --size %WT_COLUMNS%,%WT_LINES% -d "%PROJECT_DIR%" cmd.exe /c call "%~f0" --inside-wt %APP_ARGS%
for /L %%i in (1,1,5) do (
    if exist "%LAUNCH_MARKER%" exit /b 0
    ping -n 2 127.0.0.1 >nul
)
if exist "%LAUNCH_MARKER%" exit /b 0
if %WT_ATTEMPTS% lss 2 goto try_wt
goto run_app

:run_app
if defined INSIDE_WT echo ok>"%LAUNCH_MARKER%"

mode con: cols=%WT_COLUMNS% lines=%WT_LINES% >nul 2>&1

REM Launch with the full path to main.py so each instance is identifiable on its command
REM line; the :reap sweep above relies on this to find and clear a leftover instance.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "%PROJECT_DIR%\main.py" %APP_ARGS%
) else (
    py -3 --version >nul 2>&1
    if errorlevel 1 (
        python "%PROJECT_DIR%\main.py" %APP_ARGS%
    ) else (
        py -3 "%PROJECT_DIR%\main.py" %APP_ARGS%
    )
)

if errorlevel 1 (
    echo.
    echo The app exited with an error ^(or was replaced by a new launch^).
    echo This window closes automatically in 30 seconds; press a key to close now.
    timeout /t 30 >nul
)
exit /b 0

:reap
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d='%PROJECT_DIR%'; $p='%PROFILE_DIR%'; try { Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object { $_.CommandLine } | Where-Object { ($d -and $_.Name -in 'python.exe','pythonw.exe','py.exe' -and $_.CommandLine -like ('*'+$d+'\main.py*')) -or ($p -and $_.Name -eq 'chrome.exe' -and $_.CommandLine -like ('*'+$p+'*')) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} } } catch {}" >nul 2>&1
exit /b 0
