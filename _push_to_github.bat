@echo off
cd /d "C:\Users\USER\Downloads\NasdaqPulse_Project"

echo ==========================================
echo  NasdaqPulse - Push to GitHub
echo ==========================================
echo.

echo [1/6] Initializing git...
git init
git branch -M main

echo [2/6] Setting git identity...
git config user.email "kt.booklover5555@gmail.com"
git config user.name "krittanut789656"

echo [3/6] Setting remote...
git remote remove origin 2>nul
git remote add origin https://github.com/krittanut789656/nasdaqpulse.git

echo [4/6] Staging files (secrets excluded by .gitignore)...
git add .

echo.
echo === Files to be committed ===
git status --short
echo ==============================
echo.

echo [5/6] Committing...
git commit -m "Initial commit: NasdaqPulse DADS5001 final project"

echo.
echo [6/6] Pushing to GitHub...
echo (GitHub will ask for your username + Personal Access Token)
echo.
git push -u origin main

echo.
echo ==========================================
if %errorlevel% == 0 (
    echo  SUCCESS! Code pushed to GitHub.
) else (
    echo  Push failed. Check credentials above.
)
echo ==========================================
pause
