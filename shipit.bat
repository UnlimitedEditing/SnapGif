@echo off
color 0A
echo 🚀 Quick Publish to GitHub
echo.

:: Prompt for the repository name
set /p REPO_NAME="Name your repo (or press Enter to just use the folder name): "

:: If you just hit Enter, it grabs the current folder's name automatically
if "%REPO_NAME%"=="" (
    for %%I in (.) do set REPO_NAME=%%~nxI
)

echo.
echo Packaging it up...

:: The Git and GitHub CLI magic
git init
git add .
git commit -m "Initial commit"
gh repo create %REPO_NAME% --public --source=. --push

echo.
echo ✅ Boom. "%REPO_NAME%" is live!
:: Pause for 3 seconds so you can see the success message before it vanishes
timeout /t 3 >nul