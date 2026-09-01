@echo off
REM ---------------------------------------------------------------------------
REM Step 2 of 2: send each prompt file to Gemini, then assemble
REM notes\feedback\feedback_analysis_prompt.txt from the results plus your
REM per-problem notes.
REM
REM This one COSTS MONEY. Results are cached per run, so re-running only pays
REM for runs that do not have a result yet.
REM
REM Any extra arguments are passed through, e.g.:
REM     run_feedback_analysis.bat --limit 1      (one call, to sanity-check)
REM     run_feedback_analysis.bat --assemble-only (no API calls at all)
REM ---------------------------------------------------------------------------
setlocal

set "CONDA_BAT=C:\Users\rober\anaconda3\condabin\conda.bat"
set "CONDA_ENV=edee"

cd /d "%~dp0"

if not exist "%CONDA_BAT%" (
    echo ERROR: conda not found at %CONDA_BAT%
    echo Edit CONDA_BAT at the top of this file to point at your conda install.
    goto :end
)

call "%CONDA_BAT%" activate %CONDA_ENV%
if errorlevel 1 (
    echo ERROR: could not activate the "%CONDA_ENV%" conda environment.
    goto :end
)

if not exist "notes\feedback\prompts_per_problem\manifest.json" (
    echo ERROR: no manifest.json found.
    echo Run make_feedback_prompts.bat first.
    goto :end
)

echo Did you fill in the "problem ### user notes.txt" files you wanted to?
echo They are read fresh on every run, so you can also edit them and re-run
echo this later -- re-assembling is free, only new Gemini calls cost money.
echo.
echo Press Ctrl+C to stop, or
pause

echo.
python run_feedback_analysis.py %*
set "RC=%errorlevel%"
echo.

if "%RC%"=="0" goto :ok
echo ============================================================
echo  Finished with errors (exit code %RC%). Any run that failed
echo  was NOT cached -- just run this file again to retry only
echo  the ones still missing. Nothing already paid for is redone.
echo ============================================================
goto :end

:ok
echo ============================================================
echo  Done. The assembled pass-2 prompt is at:
echo    notes\feedback\^<today^> feedback_analysis_prompt.txt
echo  Paste it into whichever model you want the systemic
echo  summary from.
echo.
echo  Once every run had a result, the prompt files and your
echo  per-problem notes were zipped into
echo    notes\feedback\^<today^> consumed_prompts.zip
echo  and removed from prompts_per_problem. manifest.json stays
echo  put -- it keeps the problem/run numbering stable and holds
echo  the cost history. Gemini results are kept too, so
echo  re-assembling costs nothing.
echo ============================================================

:end
endlocal
pause
