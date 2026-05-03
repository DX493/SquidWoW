@echo off
echo Suppression des MCLQ (eau vanilla)...
mkdir no_water 2>nul
for %%f in (*.adt) do (
    python remove_mclq.py "%%f" "no_water\%%f"
)
echo Termine !
pause
