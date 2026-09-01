@echo off
REM ---------------------------------------------------------------------------
REM Step 1 of 2: read Sheet1 live and write one prompt file per run.
REM Then fill in the per-problem notes, then run run_feedback_analysis.bat.
REM Any extra arguments are passed through (e.g. --dry-run).
REM ---------------------------------------------------------------------------
setlocal

set "CONDA_BAT=C:\Users\rober\anaconda3\condabin\conda.bat"
set "CONDA_ENV=edee"
set "CREDENTIALS=gen-lang-client-0925957935-703907c1f0bc.json"

cd /d "%~dp0"

if not exist "%CONDA_BAT%" (
    echo ERROR: conda not found at %CONDA_BAT%
    echo Edit CONDA_BAT at the top of this file to point at your conda install.
    goto :end
)

call "%CONDA_BAT%" activate %CONDA_ENV%
if errorlevel 1 (
    echo ERROR: could not activate the "%CONDA_ENV%" conda environment.
    echo Create it with:
    echo     conda create -n %CONDA_ENV% python=3.12 -y
    echo     conda activate %CONDA_ENV%
    echo     pip install gspread
    goto :end
)

if not exist "%CREDENTIALS%" (
    echo ERROR: service-account key not found: %CREDENTIALS%
    echo Put the JSON key in this folder, or edit CREDENTIALS at the top of
    echo this file. It must be the key for the account the sheet is shared with.
    goto :end
)

echo Reading Sheet1 and writing prompt files...
echo.
python make_feedback_prompts.py --credentials "%CREDENTIALS%" %*
set "RC=%errorlevel%"
echo.

if "%RC%"=="0" goto :ok
if "%RC%"=="1" goto :partial
echo ============================================================
echo  FAILED (exit code %RC%). Nothing further to do -- see the
echo  error above.
echo ============================================================
goto :end

:partial
echo ============================================================
echo  Finished with warnings (see above): some prompt files
echo  already existed and were left untouched, and/or some rows
echo  were skipped because their history download failed.
echo  Existing files are never overwritten. To retry just the
echo  skipped rows, run this file again.
echo ============================================================
goto :next

:ok
echo ============================================================
echo  Prompt files written.
echo ============================================================

:next
echo.
echo  NEXT STEP -- your notes, before any Gemini calls:
echo.
echo  In notes\feedback\prompts_per_problem\ there is one
echo    "problem ### user notes.txt"
echo  per problem. Fill in the ones where you have observations
echo  that span the runs of that problem -- patterns you noticed,
echo  what the runs got consistently wrong, anything the sheet's
echo  per-run columns do not capture.
echo.
echo  Empty ones are fine; they show up as "(none)". The files
echo  are read fresh each time, so you can edit them later and
echo  re-run the analysis to pick up the changes.
echo.
echo  THEN run:  run_feedback_analysis.bat
echo.

:end
endlocal
pause
