@echo off
echo Conversion MCLQ (vanilla) vers MH2O (WotLK 3.3.5)...
echo.
mkdir converted 2>nul
set COUNT=0
for %%f in (*.adt) do (
    echo Conversion de %%f...
    python mclq_to_mh2o.py "%%f" "converted\%%f"
    set /a COUNT+=1
)
echo.
echo Termine ! Fichiers convertis dans le dossier "converted\"
pause
